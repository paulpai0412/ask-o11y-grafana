"""Deterministic, training-only preprocessing for Ask O11y sandbox ML templates."""
from __future__ import annotations

from typing import Any

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]
from sklearn.compose import ColumnTransformer  # type: ignore[reportMissingImports]
from sklearn.impute import SimpleImputer  # type: ignore[reportMissingImports]
from sklearn.pipeline import Pipeline  # type: ignore[reportMissingImports]
from sklearn.preprocessing import OneHotEncoder, StandardScaler, TargetEncoder  # type: ignore[reportMissingImports]
from statsmodels.stats.outliers_influence import variance_inflation_factor  # type: ignore[reportMissingImports]


def safe_membership_mask(series: pd.Series, allowed: set[Any]) -> pd.Series:
    """Return a non-nullable bool mask (safe for pandas StringDtype + pd.NA)."""
    return series.isin(allowed).fillna(False).astype(bool)


def prune_numeric_vif(frame: pd.DataFrame, threshold: float = 10.0) -> tuple[list[str], list[str]]:
    """Iteratively remove one numeric field from each VIF>threshold group."""
    numeric = list(frame.select_dtypes(include=[np.number]).columns)
    nonnumeric = [name for name in frame.columns if name not in numeric]
    removed: list[str] = []
    working = frame[numeric].copy()
    for name in list(working.columns):
        if working[name].nunique(dropna=True) <= 1:
            working = working.drop(columns=[name])
            removed.append(name)
    if working.empty:
        return nonnumeric, removed
    working = working.fillna(working.median(numeric_only=True))
    while len(working.columns) > 1:
        values = working.to_numpy(dtype=float)
        try:
            scores = [float(variance_inflation_factor(values, index)) for index in range(values.shape[1])]
        except (ValueError, ZeroDivisionError, np.linalg.LinAlgError):
            scores = [float("inf")] * values.shape[1]
        worst = max(scores)
        if np.isfinite(worst) and worst <= threshold:
            break
        index = scores.index(worst)
        name = str(working.columns[index])
        working = working.drop(columns=[name])
        removed.append(name)
    kept = [name for name in frame.columns if name not in removed]
    return kept, removed


def _build_preprocessor(frame: pd.DataFrame, target: pd.Series, high_cardinality_threshold: int) -> tuple[ColumnTransformer, dict[str, list[str]]]:
    numeric = list(frame.select_dtypes(include=[np.number]).columns)
    categorical = [name for name in frame.columns if name not in numeric]
    high = [name for name in categorical if frame[name].nunique(dropna=True) > high_cardinality_threshold]
    low = [name for name in categorical if name not in high]
    transformers = []
    if numeric:
        transformers.append(("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), numeric))
    if low:
        transformers.append(("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), low))
    if high:
        try:
            minimum_class_count = int(target.value_counts(dropna=False).min())
        except (TypeError, ValueError) as exc:
            raise ValueError("target class counts are unavailable") from exc
        if minimum_class_count < 2:
            raise ValueError("target encoding requires at least two training examples per class")
        target_cv = min(5, minimum_class_count)
        transformers.append(("high_cardinality", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", TargetEncoder(target_type="binary", smooth="auto", cv=target_cv)),
        ]), high))
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0), {
        "numeric_fields": numeric,
        "low_cardinality_fields": low,
        "high_cardinality_fields": high,
    }


def fit_transform_train_test(
    train: pd.DataFrame,
    target: pd.Series,
    test: pd.DataFrame,
    *,
    high_cardinality_threshold: int = 32,
) -> dict[str, Any]:
    """Fit only on train, then transform train/test with one reusable processor."""
    if list(train.columns) != list(test.columns):
        raise ValueError("train/test feature columns must match in order")
    processor, fields = _build_preprocessor(train, target, high_cardinality_threshold)
    train_matrix = np.asarray(processor.fit_transform(train, target), dtype=float)
    test_matrix = np.asarray(processor.transform(test), dtype=float)
    return {
        "train": train_matrix,
        "test": test_matrix,
        "processor": processor,
        "fit_scope": "training_only",
        **fields,
    }
