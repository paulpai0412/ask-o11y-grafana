"""Deterministic multi-model regression with chronological validation."""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml_execution import cv_splits, fit_counts, sum_fit_counts

import hashlib
import json
import math
from math import prod
from typing import Any

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]
from sklearn.compose import ColumnTransformer  # type: ignore[reportMissingImports]
from sklearn.dummy import DummyRegressor  # type: ignore[reportMissingImports]
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor  # type: ignore[reportMissingImports]
from sklearn.linear_model import Ridge  # type: ignore[reportMissingImports]
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score  # type: ignore[reportMissingImports]
from sklearn.model_selection import GroupKFold, RandomizedSearchCV, TimeSeriesSplit  # type: ignore[reportMissingImports]
from sklearn.pipeline import Pipeline  # type: ignore[reportMissingImports]
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # type: ignore[reportMissingImports]

try:
    from catboost import CatBoostRegressor  # type: ignore[reportMissingImports]
except (ImportError, OSError):
    CatBoostRegressor = None
try:
    from xgboost import XGBRegressor  # type: ignore[reportMissingImports]
except (ImportError, OSError):
    XGBRegressor = None

CORE_KINDS = ("dummy", "ridge", "random_forest", "extra_trees", "hist_gradient_boosting")
OPTIONAL_KINDS = ("catboost", "xgboost")
SUPPORTED_KINDS = CORE_KINDS + OPTIONAL_KINDS


def apply_population_filter(frame: pd.DataFrame, population_filter: dict[str, Any] | None = None) -> pd.DataFrame:
    """Return only rows matching exact observed population values."""
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("population filter requires a DataFrame")
    if population_filter in (None, {}):
        return frame.copy()
    if not isinstance(population_filter, dict) or any(not isinstance(name, str) or not name for name in population_filter):
        raise ValueError("population_filter must map field names to scalar values")
    work = frame.copy()
    for name, value in population_filter.items():
        if name not in work.columns:
            raise ValueError(f"population filter field is not in the authorized frame: {name}")
        if value is None or isinstance(value, (dict, list, tuple, set)) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"population filter value is not a finite scalar: {name}")
        work = work.loc[work[name] == value]
    if work.empty:
        raise ValueError("population filter has no observed rows")
    return work.reset_index(drop=True)


def available_regression_kinds() -> list[str]:
    kinds: list[str] = list(CORE_KINDS)
    if CatBoostRegressor is not None:
        kinds.append("catboost")
    if XGBRegressor is not None:
        kinds.append("xgboost")
    return kinds


def _preprocessor(frame: pd.DataFrame) -> ColumnTransformer:
    numeric = list(frame.select_dtypes(include=[np.number]).columns)
    categorical = [name for name in frame.columns if name not in numeric]
    transformers: list[tuple[str, Pipeline, list[str]]] = []
    if numeric:
        transformers.append(("numeric", Pipeline([
            ("scale", StandardScaler()),
        ]), numeric))
    if categorical:
        transformers.append(("categorical", Pipeline([
            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), categorical))
    if not transformers:
        raise ValueError("regression requires at least one feature")
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0)


def _estimator(kind: str, seed: int) -> tuple[Any, dict[str, list[Any]], int]:
    if kind == "dummy":
        return DummyRegressor(), {"model__strategy": ["mean", "median"]}, -1
    if kind == "ridge":
        return Ridge(), {"model__alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}, -1
    if kind == "random_forest":
        return RandomForestRegressor(random_state=seed, n_jobs=1), {
            "model__n_estimators": [150, 300],
            "model__max_depth": [8, 16, None],
            "model__min_samples_leaf": [1, 3, 8],
            "model__max_features": ["sqrt", 1.0],
        }, -1
    if kind == "extra_trees":
        return ExtraTreesRegressor(random_state=seed, n_jobs=1), {
            "model__n_estimators": [150, 300],
            "model__max_depth": [8, 16, None],
            "model__min_samples_leaf": [1, 3, 8],
            "model__max_features": ["sqrt", 1.0],
        }, -1
    if kind == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(random_state=seed), {
            "model__max_iter": [100, 200],
            "model__learning_rate": [0.03, 0.1],
            "model__max_leaf_nodes": [15, 31],
            "model__min_samples_leaf": [10, 20],
            "model__l2_regularization": [0.0, 1.0],
        }, -1
    if kind == "catboost":
        if CatBoostRegressor is None:
            raise ValueError("catboost is unavailable in the sandbox image")
        return CatBoostRegressor(random_seed=seed, thread_count=4, verbose=False, allow_writing_files=False), {
            "model__iterations": [150, 300],
            "model__depth": [4, 6, 8],
            "model__learning_rate": [0.03, 0.1],
            "model__l2_leaf_reg": [3.0, 10.0],
        }, 1
    if kind == "xgboost":
        if XGBRegressor is None:
            raise ValueError("xgboost is unavailable in the sandbox image")
        return XGBRegressor(random_state=seed, n_jobs=1, verbosity=0, objective="reg:squarederror"), {
            "model__n_estimators": [150, 300],
            "model__max_depth": [3, 6, 9],
            "model__learning_rate": [0.03, 0.1],
            "model__subsample": [0.8, 1.0],
            "model__colsample_bytree": [0.8, 1.0],
        }, -1
    raise ValueError(f"unsupported regression kind: {kind}")


def _bootstrap_mae_interval(actual: np.ndarray, predicted: np.ndarray, *, seed: int, samples: int) -> list[float]:
    if isinstance(samples, bool) or not isinstance(samples, int) or not 100 <= samples <= 10_000:
        raise ValueError("bootstrap_samples must be between 100 and 10000")
    if actual.size == 0 or actual.size != predicted.size:
        raise ValueError("bootstrap MAE inputs must have equal non-zero lengths")
    try:
        errors = np.abs(actual - predicted)
        if not np.isfinite(errors).all():
            raise ValueError("bootstrap MAE inputs must be finite")
        rng = np.random.default_rng(seed)
        bootstrapped = [float(errors[rng.integers(0, len(errors), size=len(errors))].mean()) for _ in range(samples)]
        interval = [float(value) for value in np.quantile(bootstrapped, [0.025, 0.975])]
        if not all(math.isfinite(value) for value in interval):
            raise ValueError("bootstrap MAE interval is non-finite")
        return interval
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError("bootstrap MAE interval could not be calculated") from exc


def search_candidate_settings(
    estimator: Pipeline,
    frame: pd.DataFrame,
    *,
    beats_baseline: bool,
    target_direction: str,
    controllable_fields: list[str],
    support_group_fields: list[str] | None = None,
    fixed_context: dict[str, Any] | None = None,
    bounds: dict[str, list[float]] | None = None,
    bounds_approved: bool = False,
    minimum_support: int = 5,
    top_k: int = 5,
    seed: int = 42,
    bootstrap_samples: int = 500,
) -> dict[str, Any]:
    """Rank only observed, sufficiently-supported settings; never extrapolate."""
    if not beats_baseline:
        raise ValueError("model must beat the dummy baseline before constrained search")
    if target_direction not in {"minimize", "maximize"}:
        raise ValueError("target_direction must be minimize or maximize")
    if not controllable_fields or len(set(controllable_fields)) != len(controllable_fields):
        raise ValueError("controllable_fields must be non-empty and unique")
    if not 2 <= minimum_support <= len(frame):
        raise ValueError("minimum_support is outside available rows")
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    if not 100 <= bootstrap_samples <= 10_000:
        raise ValueError("bootstrap_samples must be between 100 and 10000")
    support_group_fields = list(support_group_fields or [])
    if len(set(support_group_fields)) != len(support_group_fields) or set(support_group_fields) & set(controllable_fields):
        raise ValueError("support_group_fields must be unique context fields")
    feature_columns = [str(name) for name in getattr(estimator, "feature_names_in_", [])]
    required = set(feature_columns) | set(controllable_fields) | set(support_group_fields) | set((fixed_context or {}).keys())
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("candidate support frame lacks fields: " + ", ".join(missing))
    if any(name not in feature_columns for name in controllable_fields):
        raise ValueError("controllable fields must be model features")
    candidate_fields = list(dict.fromkeys([*feature_columns, *controllable_fields, *support_group_fields, *(fixed_context or {})]))
    if bool(frame[candidate_fields].isna().to_numpy().any()):
        raise ValueError("candidate settings require complete feature/context values; imputation and feature-row dropping are not supported")

    work = frame.copy()
    for name, value in (fixed_context or {}).items():
        observed = set(work[name].dropna().tolist())
        if value not in observed:
            raise ValueError(f"unseen fixed context value for {name}")
        work = work.loc[work[name] == value]
    for name, requested in (bounds or {}).items():
        if name not in controllable_fields or len(requested) != 2:
            raise ValueError("bounds must contain two values for controllable fields only")
        numeric = pd.Series(pd.to_numeric(work[name], errors="coerce")).dropna()
        if numeric.empty:
            raise ValueError(f"bounds require numeric observed support for {name}")
        try:
            numeric_values = numeric.to_numpy(dtype=float)
            observed_low, observed_high = float(np.min(numeric_values)), float(np.max(numeric_values))
            low, high = float(requested[0]), float(requested[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bounds for {name} are not numeric") from exc
        if low > high:
            raise ValueError(f"bounds for {name} are invalid")
        if low < observed_low or high > observed_high:
            if not bounds_approved:
                raise ValueError(f"bounds for {name} exceed observed support")
            low, high = max(low, observed_low), min(high, observed_high)
        if low > high:
            return {"status": "insufficient_support", "candidate_settings": [], "rejected_sparse_groups": 0}
        work = work.loc[pd.Series(pd.to_numeric(work[name], errors="coerce"), index=work.index).between(low, high)]
    if work.empty:
        return {"status": "insufficient_support", "candidate_settings": [], "rejected_sparse_groups": 0}

    rng = np.random.default_rng(seed)
    candidates: list[dict[str, Any]] = []
    sparse = 0
    grouped_fields = [*support_group_fields, *controllable_fields]
    grouped = work.groupby(grouped_fields, dropna=False, sort=True)
    for raw_key, group in grouped:
        if len(group) < minimum_support:
            sparse += 1
            continue
        try:
            predictions = np.asarray(estimator.predict(group[feature_columns]), dtype=float)
            bootstrap_means = [float(predictions[rng.integers(0, len(predictions), size=len(predictions))].mean()) for _ in range(bootstrap_samples)]
            interval = [float(value) for value in np.quantile(bootstrap_means, [0.025, 0.975])]
            predicted = float(predictions.mean())
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("candidate prediction uncertainty could not be calculated") from exc
        keys = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        values = {name: value.item() if isinstance(value, np.generic) else value for name, value in zip(grouped_fields, keys, strict=True)}
        settings = {name: values[name] for name in controllable_fields}
        context = {**dict(fixed_context or {}), **{name: values[name] for name in support_group_fields}}
        candidates.append({
            "settings": settings,
            "context": context,
            "support": len(group),
            "predicted_target": predicted,
            "uncertainty": {"method": "bootstrap_observed_predictions", "lower": interval[0], "upper": interval[1], "samples": bootstrap_samples},
            "interpretation": "此設定只是在已觀察支持範圍內的預測候選，並非因果最佳。",
            "causal_claim": False,
        })
    candidates.sort(key=lambda row: row["predicted_target"], reverse=target_direction == "maximize")
    selected = candidates[:top_k]
    return {
        "status": "candidate_settings" if selected else "insufficient_support",
        "candidate_settings": selected,
        "rejected_sparse_groups": sparse,
        "observed_combinations_evaluated": len(candidates),
        "experiment_recommendation": "先做受控試驗，逐項確認設定、量測與安全界線。",
        "stop_conditions": ["結果超出工程安全界線", "實測反應落在候選不確定區間之外", "資料或設備狀態發生漂移"],
    }


def run_multi_model_regression(
    train: pd.DataFrame,
    target: Any,
    holdout: pd.DataFrame,
    holdout_target: Any,
    *,
    kinds: list[str],
    seed: int = 42,
    n_iter: int = 20,
    cv_folds: int = 5,
    bootstrap_samples: int = 1000,
    groups: Any | None = None,
    times: Any | None = None,
    selection_only: bool = False,
) -> dict[str, Any]:
    """Compare models on shared chronological folds, then evaluate holdout once."""
    if not kinds or len(set(kinds)) != len(kinds):
        raise ValueError("kinds must declare unique regression candidates")
    requested = list(dict.fromkeys(["dummy", *kinds]))
    if any(kind not in SUPPORTED_KINDS for kind in requested):
        raise ValueError("kinds contains an unsupported regression algorithm")
    if not 1 <= n_iter <= 40:
        raise ValueError("n_iter must be between 1 and 40")
    if not 2 <= cv_folds <= 10:
        raise ValueError("cv_folds must be between 2 and 10")
    if not 100 <= bootstrap_samples <= 10_000:
        raise ValueError("bootstrap_samples must be between 100 and 10000")
    if list(train.columns) != list(holdout.columns):
        raise ValueError("train/holdout feature columns must match")
    if bool(train.isna().to_numpy().any()) or bool(holdout.isna().to_numpy().any()):
        raise ValueError("regression feature values must be complete; imputation and feature-row dropping are not supported")

    y_train = pd.Series(pd.to_numeric(pd.Series(target).reset_index(drop=True), errors="coerce"))
    x_train = train.reset_index(drop=True)
    if len(x_train) != len(y_train):
        raise ValueError("feature and target row counts must match")
    if not np.isfinite(y_train.to_numpy(dtype=float)).all():
        raise ValueError("regression target must be numeric and non-missing")
    if y_train.nunique() < 2:
        raise ValueError("regression target must not be constant")

    group_values = None if groups is None else pd.Series(groups).reset_index(drop=True)
    if group_values is not None and (len(group_values) != len(x_train) or group_values.isna().any() or group_values.nunique() < cv_folds):
        raise ValueError("groups must cover training rows with at least cv_folds distinct values")
    cv, folds = cv_splits("chronological_holdout" if group_values is None else "grouped_holdout", y_train, cv_folds, seed, times=times, groups=group_values)
    fold_signature = hashlib.sha256(json.dumps(folds, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    available = set(available_regression_kinds())
    unavailable = [kind for kind in requested if kind not in available]
    if unavailable:
        raise ValueError("requested regression algorithms are unavailable: " + ", ".join(unavailable))
    comparison: list[dict[str, Any]] = []
    fitted: dict[str, Pipeline] = {}
    tuned_count = sum(kind != "dummy" and kind in available for kind in requested)
    if len(requested) > n_iter:
        raise ValueError("global trial budget must cover all requested candidates including the baseline")
    per_kind_budget = (n_iter - 1) // max(tuned_count, 1)

    scoring = {"mae": "neg_mean_absolute_error", "rmse": "neg_root_mean_squared_error", "r2": "r2"}
    for kind in requested:
        model, search_space, _jobs = _estimator(kind, seed)
        # ponytail: serial search avoids nested joblib/estimator workers in the bounded sandbox; increase after memory profiling.
        jobs = 1
        pipeline = Pipeline([("preprocess", _preprocessor(x_train)), ("model", model)])
        trials = min(1 if kind == "dummy" else per_kind_budget, prod(len(values) for values in search_space.values()))
        search = RandomizedSearchCV(
            pipeline,
            search_space,
            n_iter=trials,
            scoring=scoring,
            refit="mae",  # pyright: ignore[reportArgumentType] -- sklearn supports named multi-metric refit; local stub only declares bool.
            cv=cv,
            random_state=seed,
            n_jobs=jobs,
            return_train_score=False,
            error_score="raise",  # pyright: ignore[reportArgumentType] -- sklearn supports fail-fast fits.
        )
        search.fit(x_train, y_train)
        try:
            index = int(search.best_index_)
            comparison.append({
                "kind": kind,
                "cv_mae_mean": float(-search.cv_results_["mean_test_mae"][index]),
                "cv_mae_std": float(search.cv_results_["std_test_mae"][index]),
                "cv_rmse_mean": float(-search.cv_results_["mean_test_rmse"][index]),
                "cv_r2_mean": float(search.cv_results_["mean_test_r2"][index]),
                "best_params": search.best_params_,
                "search_trials": len(search.cv_results_["params"]),
                "fit_counts": fit_counts(len(search.cv_results_["params"]), len(cv), baseline=kind == "dummy"),
                "cv_fold_signature": fold_signature,
            })
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{kind} cross-validation metrics are invalid") from exc
        if not all(math.isfinite(comparison[-1][key]) for key in ("cv_mae_mean", "cv_mae_std", "cv_rmse_mean", "cv_r2_mean")):
            raise ValueError(f"{kind} cross-validation did not produce finite metrics")
        fitted[kind] = search.best_estimator_

    if not comparison:
        raise ValueError("none of the requested regression algorithms are available")
    selected = min(comparison, key=lambda row: row["cv_mae_mean"])
    baseline = next(row for row in comparison if row["kind"] == "dummy")
    selected_cv_beats_baseline = selected["kind"] != "dummy" and selected["cv_mae_mean"] < baseline["cv_mae_mean"]
    candidate = {
        "comparison": comparison, "selected_kind": selected["kind"],
        "selected_estimator": fitted[selected["kind"]], "baseline_estimator": fitted["dummy"],
        "baseline_kind": "dummy", "baseline_cv_mae": baseline["cv_mae_mean"],
        "selected_cv_beats_baseline": selected_cv_beats_baseline,
        "split_kind": "chronological" if group_values is None else "grouped",
        "cv_folds": folds, "cv_fold_signature": fold_signature,
        "unavailable_kinds": unavailable, "per_kind_budget": per_kind_budget, "seed": seed,
        "completed_trials": sum(row["search_trials"] for row in comparison),
        "baseline_trials": 1, "execution_mode": "sequential",
        "fit_counts": sum_fit_counts([row["fit_counts"] for row in comparison]),
    }
    if selection_only:
        return candidate
    return evaluate_regression_candidate(candidate, holdout, holdout_target, bootstrap_samples=bootstrap_samples)


def evaluate_regression_candidate(candidate: dict[str, Any], holdout: pd.DataFrame, holdout_target: Any, *, bootstrap_samples: int = 1000) -> dict[str, Any]:
    """Consume final holdout only after the model and feature set are locked."""
    seed = candidate["seed"]
    selected_cv_beats_baseline = candidate["selected_cv_beats_baseline"]
    try:
        predictions = np.asarray(candidate["selected_estimator"].predict(holdout), dtype=float)
        baseline_predictions = np.asarray(candidate["baseline_estimator"].predict(holdout), dtype=float)
        actual = np.asarray(holdout_target, dtype=float)
        baseline_holdout_mae = float(mean_absolute_error(actual, baseline_predictions))
        holdout_metrics = {
            "mae": float(mean_absolute_error(actual, predictions)),
            "rmse": float(mean_squared_error(actual, predictions) ** 0.5),
            "r2": float(r2_score(actual, predictions)),
            "mae_interval": _bootstrap_mae_interval(actual, predictions, seed=seed, samples=bootstrap_samples),
        }
        baseline_holdout_mae_interval = _bootstrap_mae_interval(actual, baseline_predictions, seed=seed + 1, samples=bootstrap_samples)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("holdout metrics could not be calculated") from exc
    uncertainty = {
        "method": "nonparametric_bootstrap_fixed_holdout_predictions",
        "confidence": 0.95,
        "samples": bootstrap_samples,
        "metrics": ["mae"],
        "limitations": {
            "model_selection": "The interval conditions on the locked selected model and does not include model-selection uncertainty.",
            "calibration": "Regression predictions are held fixed; the bootstrap does not refit or recalibrate the estimator.",
            "holdout": "The interval resamples one fixed holdout and is not a causal or future-performance guarantee.",
            "multiple_comparisons": "Candidate scores are selected by training CV; no inferential p-value or multiplicity claim is made.",
        },
    }
    beats_baseline = selected_cv_beats_baseline and holdout_metrics["mae"] < baseline_holdout_mae
    return {
        **candidate,
        "baseline_holdout_mae": baseline_holdout_mae,
        "baseline_holdout_mae_interval": baseline_holdout_mae_interval,
        "uncertainty": uncertainty,
        "selected_cv_beats_baseline": selected_cv_beats_baseline,
        "beats_baseline": beats_baseline,
        "can_run_constrained_search": beats_baseline,
        "holdout_metrics": holdout_metrics,
        "holdout_evaluations": 1,
    }
