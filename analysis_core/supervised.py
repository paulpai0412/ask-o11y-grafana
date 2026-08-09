"""Allowlisted deterministic supervised learning and evaluation."""
from __future__ import annotations

from typing import Any, Literal

import pandas as pd  # pyright: ignore[reportMissingImports]
from sklearn.compose import ColumnTransformer  # pyright: ignore[reportMissingImports]
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor  # pyright: ignore[reportMissingImports]
from sklearn.impute import SimpleImputer  # pyright: ignore[reportMissingImports]
from sklearn.linear_model import LinearRegression, LogisticRegression  # pyright: ignore[reportMissingImports]
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, mean_absolute_error, mean_squared_error, precision_score, r2_score, recall_score  # pyright: ignore[reportMissingImports]
from sklearn.pipeline import Pipeline  # pyright: ignore[reportMissingImports]
from sklearn.preprocessing import StandardScaler  # pyright: ignore[reportMissingImports]

from .frame import AnalysisCoreError, scalar_float

Task = Literal["regression", "classification"]
ModelFamily = Literal["linear", "random_forest"]


def _numeric_frame(data: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    if not fields or len(set(fields)) != len(fields):
        raise AnalysisCoreError("features must contain unique field names")
    unknown = [field for field in fields if field not in data.columns]
    if unknown:
        raise AnalysisCoreError("unknown features: " + ", ".join(unknown))
    converted = {field: pd.to_numeric(data.loc[:, field], errors="coerce") for field in fields}
    return pd.DataFrame(converted)


def _split_indices(data: pd.DataFrame, *, test_fraction: float, seed: int, time_field: str | None) -> tuple[list[int], list[int], str]:
    if not 0.1 <= test_fraction <= 0.4:
        raise AnalysisCoreError("test_fraction must be between 0.1 and 0.4")
    count = len(data)
    test_count = max(2, round(count * test_fraction))
    if count - test_count < 5:
        raise AnalysisCoreError("insufficient rows for train/validation split")
    if time_field:
        if time_field not in data.columns:
            raise AnalysisCoreError(f"unknown time field: {time_field}")
        timestamps = pd.Series(pd.to_datetime(data.loc[:, time_field], errors="coerce", utc=True), index=data.index)
        if bool(timestamps.isna().to_numpy().any()):
            raise AnalysisCoreError("time field contains invalid timestamps")
        ordered = list(timestamps.sort_values(kind="stable").index)
        return ordered[:-test_count], ordered[-test_count:], "chronological"
    train = data.sample(frac=1 - test_fraction, random_state=seed).index.tolist()
    test = [index for index in data.index if index not in set(train)]
    return train, test, "seeded_random"


def supervised_model(data: pd.DataFrame, *, target: str, features: list[str], task: Task, model_family: ModelFamily, test_fraction: float = 0.2, seed: int = 42, time_field: str | None = None) -> dict[str, Any]:
    if target not in data.columns or target in features:
        raise AnalysisCoreError("target must exist and must not be included in features")
    x = _numeric_frame(data, features)
    y = data.loc[:, target]
    if not isinstance(y, pd.Series):
        raise AnalysisCoreError("target must be a scalar column")
    if task == "regression":
        y = pd.Series(pd.to_numeric(y, errors="coerce"), name=target)
    valid = y.notna()
    x, y = x.loc[valid], y.loc[valid]
    if len(y) < 20:
        raise AnalysisCoreError("supervised analysis requires at least 20 target rows")
    if task == "classification" and len(y.dropna().unique()) < 2:
        raise AnalysisCoreError("classification target requires at least two classes")
    split_frame = data.loc[y.index]
    train_index, test_index, split_strategy = _split_indices(split_frame, test_fraction=test_fraction, seed=seed, time_field=time_field)
    x_train, x_test, y_train, y_test = x.loc[train_index], x.loc[test_index], y.loc[train_index], y.loc[test_index]
    if task == "regression":
        estimator: Any = LinearRegression() if model_family == "linear" else RandomForestRegressor(n_estimators=200, random_state=seed, n_jobs=1)
    else:
        estimator = LogisticRegression(max_iter=1000, random_state=seed) if model_family == "linear" else RandomForestClassifier(n_estimators=200, random_state=seed, n_jobs=1, class_weight="balanced")
    preprocessing = ColumnTransformer([("numeric", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), features)], remainder="drop")
    pipeline = Pipeline([("preprocess", preprocessing), ("model", estimator)])
    pipeline.fit(x_train, y_train)
    predictions = pipeline.predict(x_test)
    if task == "regression":
        mse = scalar_float(mean_squared_error(y_test, predictions), "mean squared error")
        metrics = {"mae": scalar_float(mean_absolute_error(y_test, predictions), "mean absolute error"), "rmse": mse**0.5, "r2": scalar_float(r2_score(y_test, predictions), "r2")}
    else:
        metrics = {"accuracy": scalar_float(accuracy_score(y_test, predictions), "accuracy"), "balanced_accuracy": scalar_float(balanced_accuracy_score(y_test, predictions), "balanced accuracy"), "precision_weighted": scalar_float(precision_score(y_test, predictions, average="weighted", zero_division=0), "precision"), "recall_weighted": scalar_float(recall_score(y_test, predictions, average="weighted", zero_division=0), "recall"), "f1_weighted": scalar_float(f1_score(y_test, predictions, average="weighted", zero_division=0), "f1"), "confusion_matrix": confusion_matrix(y_test, predictions).tolist()}
    fitted = pipeline.named_steps["model"]
    raw_importance = getattr(fitted, "feature_importances_", None)
    if raw_importance is None:
        coefficients = getattr(fitted, "coef_", None)
        if coefficients is None:
            raise AnalysisCoreError("fitted model does not expose feature importance")
        values = coefficients[0] if getattr(coefficients, "ndim", 1) > 1 else coefficients
        raw_importance = [abs(scalar_float(value, "coefficient")) for value in values]
    importance = sorted(({"feature": feature, "importance": scalar_float(value, "feature importance")} for feature, value in zip(features, raw_importance, strict=True)), key=lambda item: item["importance"], reverse=True)
    prediction_rows = [{"index": str(index), "actual": y_test.loc[index].item() if hasattr(y_test.loc[index], "item") else y_test.loc[index], "predicted": prediction.item() if hasattr(prediction, "item") else prediction} for index, prediction in zip(test_index, predictions, strict=True)]
    return {"task": task, "model_family": model_family, "target": target, "features": features, "seed": seed, "split": {"strategy": split_strategy, "train_rows": len(train_index), "validation_rows": len(test_index), "test_fraction": test_fraction, "time_field": time_field}, "metrics": metrics, "feature_importance": importance, "predictions": prediction_rows, "validation": {"ok": True, "checks": ["explicit target/features", "target removed from features", "missing target rows removed", "imputation fitted inside pipeline", "held-out evaluation"]}, "assumptions": ["Selected features are available at prediction time."], "limitations": ["Metrics describe one held-out split and do not establish causal effects."]}
