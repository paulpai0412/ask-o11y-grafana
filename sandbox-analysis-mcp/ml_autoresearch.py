"""Budgeted classification autoresearch with untouched holdout and generalization guards."""
from __future__ import annotations

from itertools import combinations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml_execution import GENERALIZATION_LIMITS, cv_splits, fit_counts, sum_fit_counts
from typing import Any, cast

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]
try:
    from lightgbm import LGBMClassifier  # type: ignore[reportMissingImports]
except (ImportError, OSError):
    LGBMClassifier = None
from sklearn.base import BaseEstimator, ClassifierMixin, TransformerMixin, clone  # type: ignore[reportMissingImports]
from sklearn.inspection import permutation_importance  # type: ignore[reportMissingImports]
from sklearn.compose import ColumnTransformer  # type: ignore[reportMissingImports]
from sklearn.isotonic import IsotonicRegression
from sklearn.impute import SimpleImputer  # type: ignore[reportMissingImports]
from sklearn.linear_model import LogisticRegression  # type: ignore[reportMissingImports]
from sklearn.metrics import accuracy_score, average_precision_score, roc_auc_score  # type: ignore[reportMissingImports]
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold  # type: ignore[reportMissingImports]
from sklearn.pipeline import Pipeline  # type: ignore[reportMissingImports]
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # type: ignore[reportMissingImports]
from sklearn.ensemble import RandomForestClassifier  # type: ignore[reportMissingImports]
try:
    from xgboost import XGBClassifier  # type: ignore[reportMissingImports]
except (ImportError, OSError):
    XGBClassifier = None
try:
    from catboost import CatBoostClassifier  # type: ignore[reportMissingImports]
except (ImportError, OSError):
    CatBoostClassifier = None

OBJECTIVE_SCORING = {"accuracy": "accuracy", "roc_auc": "roc_auc", "pr_auc": "average_precision"}


def _to_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not numeric") from exc


def _as_float(mapping: Any, key: str, default: float) -> float:
    try:
        return float(mapping.get(key, default))
    except (TypeError, ValueError, AttributeError):
        return default


def _to_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not an integer") from exc


def generalization_verdict(
    *,
    cv_score: float,
    holdout_score: float,
    importance_stability: float,
    max_psi: float,
    objective_minimum: float | None,
) -> str:
    if abs(cv_score - holdout_score) > GENERALIZATION_LIMITS["generalization_gap"]:
        return "overfit"
    if importance_stability < GENERALIZATION_LIMITS["importance_stability"]:
        return "unstable"
    if max_psi > GENERALIZATION_LIMITS["max_psi"]:
        return "drift"
    if objective_minimum is not None and holdout_score < objective_minimum:
        return "below_objective"
    return "accepted"


def _preprocessor(frame: pd.DataFrame) -> ColumnTransformer:
    numeric = list(frame.select_dtypes(include=[np.number]).columns)
    categorical = [name for name in frame.columns if name not in numeric]
    transformers = []
    if numeric:
        transformers.append(("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), numeric))
    if categorical:
        transformers.append(("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), categorical))
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0)


class CatBoostFramePreprocessor(BaseEstimator, TransformerMixin):
    """Preserve raw columns while making categorical missing values explicit strings."""

    def __init__(self, categorical_columns: tuple[str, ...]):
        self.categorical_columns = categorical_columns

    def fit(self, frame: pd.DataFrame, target: Any = None) -> "CatBoostFramePreprocessor":
        self.feature_names_in_ = np.asarray(frame.columns, dtype=object)
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        columns = [str(name) for name in self.feature_names_in_]
        transformed = frame.loc[:, columns].copy()
        for name in self.categorical_columns:
            transformed[name] = transformed[name].where(transformed[name].notna(), "__MISSING__").astype(str)
        return transformed

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.asarray(self.feature_names_in_, dtype=object)


class CatBoostAdapter(ClassifierMixin, BaseEstimator):
    """Cloneable sklearn adapter that creates CatBoost with fold-local categorical metadata."""

    def __init__(
        self,
        categorical_columns: tuple[str, ...],
        *,
        random_seed: int = 42,
        thread_count: int = 4,
        iterations: int = 250,
        depth: int = 6,
        learning_rate: float = 0.05,
        l2_leaf_reg: float = 3.0,
        random_strength: float = 1.0,
        scale_pos_weight: float = 1.0,
    ):
        self.categorical_columns = categorical_columns
        self.random_seed = random_seed
        self.thread_count = thread_count
        self.iterations = iterations
        self.depth = depth
        self.learning_rate = learning_rate
        self.l2_leaf_reg = l2_leaf_reg
        self.random_strength = random_strength
        self.scale_pos_weight = scale_pos_weight

    def fit(self, frame: pd.DataFrame, target: Any) -> "CatBoostAdapter":
        if CatBoostClassifier is None:
            raise ValueError("catboost is unavailable in the sandbox image")
        self.model_ = CatBoostClassifier(
            cat_features=list(self.categorical_columns), random_seed=self.random_seed,
            thread_count=self.thread_count, iterations=self.iterations, depth=self.depth,
            learning_rate=self.learning_rate, l2_leaf_reg=self.l2_leaf_reg,
            random_strength=self.random_strength, scale_pos_weight=self.scale_pos_weight,
            verbose=False, allow_writing_files=False, loss_function="Logloss",
        )
        self.model_.fit(frame, target)
        self.classes_ = np.asarray(self.model_.classes_)
        self.feature_importances_ = np.asarray(self.model_.feature_importances_, dtype=float)
        self.n_features_in_ = frame.shape[1]
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model_.predict_proba(frame), dtype=float)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model_.predict(frame), dtype=int).reshape(-1)


def _estimator(kind: str, seed: int, frame: pd.DataFrame | None = None) -> tuple[Any, dict[str, list[Any]]]:
    if kind == "gradient_boosting":
        if LGBMClassifier is not None:
            model = LGBMClassifier(random_state=seed, n_jobs=1, verbosity=-1)
            space = {
                "model__n_estimators": [150, 250, 400, 600],
                "model__learning_rate": [0.02, 0.04, 0.06, 0.1],
                "model__num_leaves": [15, 31, 47, 63],
                "model__min_child_samples": [10, 20, 40, 80],
                "model__subsample": [0.8, 0.9, 1.0],
                "model__colsample_bytree": [0.8, 0.9, 1.0],
                "model__reg_lambda": [0.0, 1.0, 5.0, 10.0],
                "model__scale_pos_weight": [1.0, 2.0, 4.0],
            }
            return model, space
        raise ValueError("lightgbm is unavailable; the requested algorithm was not substituted")
    if kind == "logistic_regression":
        model = LogisticRegression(max_iter=3000, random_state=seed)
        return model, {
            "model__C": [0.01, 0.1, 0.5, 1.0, 5.0, 10.0],
            "model__class_weight": [None, "balanced"],
        }
    if kind == "random_forest_shap":
        model = RandomForestClassifier(random_state=seed, n_jobs=1)
        return model, {
            "model__n_estimators": [200, 400, 600],
            "model__max_depth": [8, 12, 16, None],
            "model__min_samples_leaf": [5, 10, 20],
            "model__max_features": ["sqrt", 0.5],
            "model__class_weight": [None, "balanced"],
        }
    if kind == "catboost":
        if CatBoostClassifier is None:
            raise ValueError("catboost is unavailable in the sandbox image")
        categorical = tuple() if frame is None else tuple(str(name) for name in frame.columns if not pd.api.types.is_numeric_dtype(frame[name]))
        model = CatBoostAdapter(categorical_columns=categorical, random_seed=seed, thread_count=1)
        return model, {
            "model__iterations": [150, 250, 400],
            "model__depth": [4, 6, 8],
            "model__learning_rate": [0.03, 0.05, 0.1],
            "model__l2_leaf_reg": [3.0, 5.0, 10.0],
            "model__random_strength": [0.0, 1.0],
            "model__scale_pos_weight": [1.0, 2.0, 4.0],
        }
    if kind == "xgboost":
        if XGBClassifier is not None:
            model = XGBClassifier(random_state=seed, n_jobs=1, verbosity=0, eval_metric="logloss")
            space = {
                "model__n_estimators": [150, 250, 400, 600],
                "model__max_depth": [3, 5, 7, 9],
                "model__learning_rate": [0.02, 0.05, 0.1],
                "model__min_child_weight": [1, 5, 10],
                "model__subsample": [0.8, 0.9, 1.0],
                "model__colsample_bytree": [0.8, 0.9, 1.0],
                "model__reg_lambda": [1.0, 5.0, 10.0],
                "model__scale_pos_weight": [1.0, 2.0, 4.0],
            }
            return model, space
        raise ValueError("xgboost is unavailable; the requested algorithm was not substituted")
    raise ValueError(f"unsupported autoresearch kind: {kind}")


def _original_feature_name(name: str, columns: list[str]) -> str:
    """Map a transformed feature name (num__Age / cat__Contract_Month-to-Month) back to its source column."""
    stripped = name.split("__", 1)[-1]
    if stripped in columns:
        return stripped
    for column in sorted(columns, key=len, reverse=True):
        if stripped.startswith(column + "_"):
            return column
    return stripped


def aggregate_importance_to_original(names: Any, values: Any, columns: Any) -> dict[str, float]:
    """Sum transformed-feature importances back onto original dataset columns."""
    column_list = [str(c) for c in (columns if columns is not None else [])]
    grouped: dict[str, float] = {}
    try:
        pairs = [(str(n), float(v)) for n, v in zip(names, values)]
    except (TypeError, ValueError) as exc:
        raise ValueError("importance names/values are invalid") from exc
    for name, value in pairs:
        key = _original_feature_name(name, column_list)
        grouped[key] = grouped.get(key, 0.0) + value
    return grouped


def _importance_stability(best: Pipeline, frame: pd.DataFrame, target: Any, cv: Any, top_k: int = 10) -> float:
    columns = [str(c) for c in getattr(best, "feature_names_in_", getattr(best.steps[0][1], "feature_names_in_", []))]
    top_sets: list[set[str]] = []
    for train_index, test_index in (cv.split(frame, target) if hasattr(cv, "split") else cv):
        estimator = cast(Pipeline, clone(best))
        estimator.fit(frame.iloc[train_index], np.asarray(target)[train_index])
        preprocess = estimator.named_steps["preprocess"]
        model = estimator.named_steps["model"]
        if hasattr(model, "feature_importances_"):
            names = np.asarray(preprocess.get_feature_names_out(), dtype=str)
            values = np.asarray(model.feature_importances_, dtype=float)
        elif hasattr(model, "coef_"):
            names = np.asarray(preprocess.get_feature_names_out(), dtype=str)
            values = np.abs(np.asarray(model.coef_, dtype=float)).reshape(-1)
        else:
            importance = permutation_importance(estimator, frame.iloc[test_index], np.asarray(target)[test_index], n_repeats=2, random_state=42, n_jobs=1)
            names = np.asarray(frame.columns, dtype=str)
            values = np.asarray(importance["importances_mean"], dtype=float)
        grouped = aggregate_importance_to_original(names, values, columns)
        grouped_names = np.asarray(list(grouped.keys()), dtype=str)
        grouped_values = np.asarray(list(grouped.values()), dtype=float)
        order = np.argsort(grouped_values)[::-1][: min(top_k, len(grouped_values))]
        top_sets.append(set(grouped_names[order].tolist()))
    scores = [len(left & right) / max(len(left | right), 1) for left, right in combinations(top_sets, 2)]
    if not scores:
        return 1.0
    try:
        return float(np.mean(scores))
    except (TypeError, ValueError) as exc:
        raise ValueError("importance stability could not be calculated") from exc


def _top_features(best: Pipeline, holdout: pd.DataFrame, holdout_target: np.ndarray, *, n_repeats: int, seed: int, n_jobs: int = -1) -> list[dict[str, Any]]:
    """Original-column permutation importance on the untouched holdout."""
    try:
        from sklearn.inspection import permutation_importance  # type: ignore[reportMissingImports]

        outcome = permutation_importance(best, holdout, holdout_target, n_repeats=n_repeats, random_state=seed, n_jobs=n_jobs)
        pairs = sorted(zip([str(name) for name in holdout.columns], [float(value) for value in outcome["importances_mean"]]), key=lambda pair: -pair[1])
        return [{"name": name, "importance": max(value, 0.0)} for name, value in pairs[:20] if value > 0]
    except (TypeError, ValueError) as exc:
        raise ValueError("feature importance could not be calculated") from exc


def select_operating_threshold(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    *,
    cost_matrix: dict[str, float] | None = None,
    minimum_recall: float | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Sweep thresholds on training-CV probabilities; return cost-optimal point and three scenarios."""
    fn_cost = _as_float(cost_matrix or {}, "false_negative", 1.0)
    fp_cost = _as_float(cost_matrix or {}, "false_positive", 1.0)
    total = max(len(y_true), 1)
    try:
        positives = max(int(y_true.sum()), 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold labels are invalid") from exc
    candidates = np.unique(np.quantile(probabilities, np.linspace(0.02, 0.98, 49)))
    table: list[dict[str, Any]] = []
    for threshold in candidates:
        predicted = (probabilities >= threshold).astype(int)
        try:
            tp = int(((y_true == 1) & (predicted == 1)).sum())
            fn = int(((y_true == 1) & (predicted == 0)).sum())
            fp = int(((y_true == 0) & (predicted == 1)).sum())
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold labels are invalid") from exc
        recall = tp / positives
        if minimum_recall is not None and recall < minimum_recall:
            continue
        try:
            table.append({
                "threshold": float(threshold),
                "recall": recall,
                "precision": float(tp / max(tp + fp, 1)),
                "fn_per_1000": round(fn / total * 1000, 1),
                "fp_per_1000": round(fp / total * 1000, 1),
                "cost_per_1000": round((fn * fn_cost + fp * fp_cost) / total * 1000, 1),
            })
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError("threshold table row is invalid") from exc
    if not table:
        raise ValueError("no threshold satisfies the declared minimum recall")
    cost_optimal = min(table, key=lambda row: row["cost_per_1000"])
    balanced = min(table, key=lambda row: abs(row["threshold"] - 0.5))
    recall_priority = max(table, key=lambda row: row["recall"])
    scenarios = []
    for name, row, note in (
        ("cost_optimal", cost_optimal, "依申報成本比選出；漏判與誤判的加權總成本最低"),
        ("balanced", balanced, "接近 0.5 門檻的傳統操作點"),
        ("recall_priority", recall_priority, "漏判最少的方案；誤攔會明顯增加"),
    ):
        scenarios.append({
            "name": name, "threshold": row["threshold"], "recall": row["recall"],
            "precision": row["precision"], "fn_per_1000": row["fn_per_1000"],
            "fp_per_1000": row["fp_per_1000"], "cost_per_1000": row["cost_per_1000"],
            "note": note,
        })
    try:
        return float(cost_optimal["threshold"]), scenarios
    except (KeyError, TypeError) as exc:
        raise ValueError("cost-optimal threshold is invalid") from exc


def _calibrate_probabilities(best: Pipeline, train: pd.DataFrame, target: Any, cv: Any, *, n_jobs: int = -1) -> tuple[np.ndarray, IsotonicRegression]:
    """Out-of-fold isotonic calibration; returns (oof_calibrated, calibrator)."""
    from sklearn.model_selection import cross_val_predict  # type: ignore[reportMissingImports]

    y_array = np.asarray(target)
    oof = np.asarray(cross_val_predict(best, train, y_array, cv=cv, method="predict_proba", n_jobs=n_jobs))[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(oof, y_array)
    return np.asarray(calibrator.predict(oof), dtype=float), calibrator


def _max_numeric_psi(train: pd.DataFrame, test: pd.DataFrame) -> float:
    scores = []
    for name in train.select_dtypes(include=[np.number]).columns:
        reference = train[name].dropna().to_numpy(dtype=float)
        current = test[name].dropna().to_numpy(dtype=float)
        if len(reference) < 10 or len(current) < 10:
            continue
        edges = np.unique(np.quantile(reference, np.linspace(0, 1, 11)))
        if len(edges) < 3:
            continue
        ref_counts, _ = np.histogram(reference, bins=edges)
        cur_counts, _ = np.histogram(current, bins=edges)
        ref = np.maximum(ref_counts / max(ref_counts.sum(), 1), 1e-6)
        cur = np.maximum(cur_counts / max(cur_counts.sum(), 1), 1e-6)
        try:
            score = float(np.sum((cur - ref) * np.log(cur / ref)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"PSI could not be calculated for {name}") from exc
        scores.append(score)
    return max(scores, default=0.0)


def run_classification_autoresearch(
    train: pd.DataFrame,
    target: Any,
    holdout: pd.DataFrame,
    holdout_target: Any,
    *,
    kind: str,
    objective: str = "roc_auc",
    seed: int = 42,
    n_iter: int = 20,
    cv_folds: int = 5,
    objective_minimum: float | None = None,
    cost_matrix: dict[str, float] | None = None,
    minimum_recall: float | None = None,
    selection_only: bool = False,
    imbalance_strategy: str | None = None,
) -> dict[str, Any]:
    if not 1 <= n_iter <= 40:
        raise ValueError("n_iter must be between 1 and 40")
    if list(train.columns) != list(holdout.columns):
        raise ValueError("train/holdout feature columns must match")
    model, search_space = _estimator(kind, seed, train)
    if imbalance_strategy not in {None, "none", "balanced"}:
        raise ValueError("unsupported class imbalance strategy")
    if imbalance_strategy == "balanced":
        if "model__class_weight" in search_space:
            search_space["model__class_weight"] = ["balanced"]
        else:
            labels = np.asarray(target)
            positives = np.count_nonzero(labels == 1)
            if not 0 < positives < len(labels):
                raise ValueError("balanced weighting requires both training classes")
            search_space["model__scale_pos_weight"] = [(len(labels) - positives) / positives]
    if kind == "catboost":
        categorical = tuple(str(name) for name in train.columns if not pd.api.types.is_numeric_dtype(train[name]))
        preprocessor: Any = CatBoostFramePreprocessor(categorical)
        parallel_jobs = 1
    else:
        preprocessor = _preprocessor(train)
        parallel_jobs = 1
    pipeline = Pipeline([("preprocess", preprocessor), ("model", model)])
    cv, receipts = cv_splits("stratified_holdout", target, cv_folds, seed)
    if objective not in OBJECTIVE_SCORING:
        raise ValueError(f"unsupported objective: {objective}")
    search = RandomizedSearchCV(pipeline, search_space, n_iter=n_iter, scoring=OBJECTIVE_SCORING[objective], cv=cv, random_state=seed, n_jobs=parallel_jobs, refit=True, return_train_score=False, error_score=cast(Any, "raise"))
    search.fit(train, target)  # holdout is intentionally not passed to search
    trials_done = len(search.cv_results_["params"])
    candidate = {"kind": kind, "cv_score": _to_float(search.best_score_, "best CV score"), "search": search, "n_iter": trials_done,
                 "cv_receipts": receipts, "cv_indices": cv, "fit_counts": fit_counts(trials_done, len(cv))}
    if selection_only:
        return candidate
    return evaluate_classification_candidate(candidate, train, target, holdout, holdout_target, objective=objective, seed=seed, cv_folds=cv_folds, objective_minimum=objective_minimum, cost_matrix=cost_matrix, minimum_recall=minimum_recall)


def evaluate_classification_candidate(
    candidate: dict[str, Any], train: pd.DataFrame, target: Any,
    holdout: pd.DataFrame, holdout_target: Any, *,
    objective: str = "roc_auc", seed: int = 42, cv_folds: int = 5,
    objective_minimum: float | None = None, cost_matrix: dict[str, float] | None = None,
    minimum_recall: float | None = None,
) -> dict[str, Any]:
    """Final evaluation only, after the caller has locked the CV winner."""
    search = candidate["search"]
    kind, n_iter = candidate["kind"], candidate["n_iter"]
    cv = candidate["cv_indices"]
    parallel_jobs = 1
    probabilities = search.best_estimator_.predict_proba(holdout)[:, 1]
    try:
        probability_list = [float(value) for value in probabilities]
    except (TypeError, ValueError) as exc:
        raise ValueError("holdout probabilities are not numeric") from exc
    y_array_hold = np.asarray(holdout_target)
    oof_calibrated, calibrator = _calibrate_probabilities(search.best_estimator_, train, target, cv, n_jobs=parallel_jobs)
    operating_threshold, scenarios = select_operating_threshold(
        np.asarray(target), oof_calibrated, cost_matrix=cost_matrix, minimum_recall=minimum_recall,
    )
    calibrated_holdout = np.asarray(calibrator.predict(probabilities), dtype=float)
    calibrated_list = [_to_float(value, "calibrated probability") for value in calibrated_holdout]
    final_predictions = (calibrated_holdout >= operating_threshold).astype(int)
    try:
        metrics = {
            "accuracy": float(accuracy_score(holdout_target, final_predictions)),
            "pr_auc": float(average_precision_score(holdout_target, calibrated_holdout)),
            "roc_auc": float(roc_auc_score(holdout_target, calibrated_holdout)),
            "recall_at_threshold": float((final_predictions[y_array_hold == 1] == 1).mean()) if int((y_array_hold == 1).sum()) else 0.0,
            "precision_at_threshold": float((y_array_hold[final_predictions == 1] == 1).mean()) if int(final_predictions.sum()) else 0.0,
            "fn_per_1000": round(float(((y_array_hold == 1) & (final_predictions == 0)).sum()) / max(len(y_array_hold), 1) * 1000, 1),
            "fp_per_1000": round(float(((y_array_hold == 0) & (final_predictions == 1)).sum()) / max(len(y_array_hold), 1) * 1000, 1),
        }
    except (TypeError, ValueError) as exc:
        raise ValueError("holdout metrics could not be calculated") from exc
    holdout_score = _to_float(metrics.get(objective, 0.0), f"holdout {objective}") if objective in metrics else _to_float(accuracy_score(holdout_target, final_predictions), "holdout accuracy")
    top_features = _top_features(search.best_estimator_, holdout, np.asarray(holdout_target), n_repeats=3, seed=seed, n_jobs=parallel_jobs)
    stability = _importance_stability(search.best_estimator_, train, target, cv)
    max_psi = _max_numeric_psi(train, holdout)
    cv_score = _to_float(search.best_score_, "best CV score")
    gap = abs(cv_score - holdout_score)
    verdict = generalization_verdict(cv_score=cv_score, holdout_score=holdout_score, importance_stability=stability, max_psi=max_psi, objective_minimum=objective_minimum)

    results = search.cv_results_
    order = np.argsort(results["rank_test_score"])[:5]
    trials = []
    for index in order:
        trials.append({
            "rank": _to_int(results["rank_test_score"][index], "trial rank"),
            "cv_score": _to_float(results["mean_test_score"][index], "trial CV score"),
            "params": results["params"][index],
        })
    return {
        "kind": kind,
        "cv_receipts": candidate["cv_receipts"],
        "fit_counts": {**candidate["fit_counts"], "calibration_oof_fits": len(cv), "calibrator_fits": 1, "stability_fits": len(cv)},
        "objective": objective,
        "best_params": search.best_params_,
        "cv_score": cv_score,
        "metrics": metrics,
        "trials": trials,
        "guards": {"generalization_gap": gap, "importance_stability": stability, "max_psi": max_psi},
        "verdict": verdict,
        "probabilities": probability_list,
        "calibrated_probabilities": calibrated_list,
        "operating_threshold": operating_threshold,
        "operating_scenarios": scenarios,
        "estimator": search.best_estimator_,
        "top_features": top_features,
        "holdout_used_during_search": False,
        "holdout_evaluations": 1,
        "selection_basis": "training_cv",
        "seed": seed,
        "n_iter": n_iter,
    }

def run_multi_model_comparison(
    train: pd.DataFrame,
    target: Any,
    holdout: pd.DataFrame,
    holdout_target: Any,
    *,
    kinds: list[str],
    objective: str = "roc_auc",
    seed: int = 42,
    n_iter: int = 20,
    cv_folds: int = 5,
    cost_matrix: dict[str, float] | None = None,
    minimum_recall: float | None = None,
    objective_minimum: float | None = None,
    imbalance_strategy: str | None = None,
) -> dict[str, Any]:
    """Compare on training CV only; evaluate the locked winner on holdout."""
    if not kinds or len(set(kinds)) != len(kinds) or not all(k in ("catboost", "gradient_boosting", "random_forest_shap", "logistic_regression", "xgboost") for k in kinds):
        raise ValueError("kinds must be a non-empty unique list of supported algorithm names")
    if isinstance(n_iter, bool) or not len(kinds) <= n_iter <= 40:
        raise ValueError("global trial budget must cover the candidate count and be at most 40")
    per_kind_budget = n_iter // len(kinds)
    comparison: list[dict[str, Any]] = []
    for kind in kinds:
        result = run_classification_autoresearch(
            train, target, holdout, holdout_target,
            kind=kind, objective=objective, seed=seed,
            n_iter=per_kind_budget, cv_folds=cv_folds,
            cost_matrix=cost_matrix, minimum_recall=minimum_recall,
            objective_minimum=objective_minimum,
            selection_only=True, imbalance_strategy=imbalance_strategy,
        )
        comparison.append(result)
    winner = max(comparison, key=lambda row: row["cv_score"])
    best = evaluate_classification_candidate(winner, train, target, holdout, holdout_target, objective=objective, seed=seed, cv_folds=cv_folds, objective_minimum=objective_minimum, cost_matrix=cost_matrix, minimum_recall=minimum_recall)
    return {
        "comparison": [{"kind": row["kind"], "cv_score": row["cv_score"], "best_params": row["search"].best_params_, "search_trials": row["n_iter"]} for row in comparison],
        "best_kind": best["kind"],
        "best_result": best,
        "selection_basis": "training_cv",
        "holdout_evaluations": 1,
        "completed_trials": sum(row["n_iter"] for row in comparison),
        "fit_counts": sum_fit_counts([row["fit_counts"] if row is not winner else best["fit_counts"] for row in comparison]),
        "cv_receipts": best["cv_receipts"],
        "baseline_kind": "constant_negative_no_fit",
        "execution_mode": "sequential",
        "eligible_kinds": [row["kind"] for row in comparison],
        "per_kind_budget": per_kind_budget,
        "objective": objective,
        "seed": seed,
    }
