#!/usr/bin/env python3
"""Public-seam check for optional CatBoost raw-category challenger support."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_autoresearch.py"
    spec = importlib.util.spec_from_file_location("catboost_challenger_research", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def frames() -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(42)
    n_train, n_hold = 150, 45
    train = pd.DataFrame({
        "Age": rng.integers(18, 80, n_train),
        "Monthly Charge": rng.normal(70, 18, n_train),
        "Contract": rng.choice(np.array(["Month-to-Month", "One Year", "Two Year", None], dtype=object), n_train),
        "Payment Method": rng.choice(["Credit Card", "Bank Withdrawal", "Mailed Check"], n_train),
    })
    holdout = pd.DataFrame({
        "Age": rng.integers(18, 80, n_hold),
        "Monthly Charge": rng.normal(70, 18, n_hold),
        "Contract": rng.choice(np.array(["Month-to-Month", "One Year", "Never-Seen Plan", None], dtype=object), n_hold),
        "Payment Method": rng.choice(["Credit Card", "Bank Withdrawal", "Crypto-New"], n_hold),
    })
    train_target = ((train["Contract"] == "Month-to-Month").astype(int) + (train["Monthly Charge"] > 72).astype(int) + rng.binomial(1, 0.15, n_train) >= 2).astype(int).to_numpy()
    hold_target = ((holdout["Contract"] == "Month-to-Month").astype(int) + (holdout["Monthly Charge"] > 72).astype(int) + rng.binomial(1, 0.15, n_hold) >= 2).astype(int).to_numpy()
    return train, train_target, holdout, hold_target


def main() -> int:
    research = load_module()
    if research.CatBoostClassifier is None:
        try:
            research._estimator("catboost", 42)
        except ValueError as exc:
            assert "unavailable" in str(exc), exc
            print("ok: CatBoost challenger fails closed when the optional package is unavailable")
            return 0
        raise AssertionError("CatBoost estimator did not fail closed")
    train, target, holdout, holdout_target = frames()
    outcome = research.run_multi_model_comparison(
        train, target, holdout, holdout_target,
        kinds=["catboost"], objective="pr_auc", seed=42, n_iter=1, cv_folds=3,
        cost_matrix={"false_negative": 3.0, "false_positive": 1.0}, minimum_recall=0.5,
    )

    assert outcome["best_kind"] == "catboost", outcome
    assert outcome["per_kind_budget"] == 1, outcome
    row = outcome["best_result"]
    assert row["kind"] == "catboost", row
    try:
        numeric_values = [float(value) for value in (row["cv_score"], row["metrics"]["pr_auc"], row["metrics"]["roc_auc"], *row["guards"].values())]
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"CatBoost metrics are not numeric: {row}") from exc
    assert all(math.isfinite(value) for value in numeric_values), row
    assert row["verdict"] in {"accepted", "overfit", "unstable", "drift", "below_objective"}, row
    assert row["top_features"] and {item["name"] for item in row["top_features"]} <= set(train.columns), row["top_features"]

    estimator = outcome["best_result"]["estimator"]
    transformed = estimator.named_steps["preprocess"].transform(holdout)
    assert isinstance(transformed, pd.DataFrame), type(transformed)
    assert list(transformed.columns) == list(train.columns), transformed.columns
    assert "Never-Seen Plan" in set(transformed["Contract"]), transformed["Contract"].unique()
    model = estimator.named_steps["model"]
    assert model.__class__.__name__ == "CatBoostAdapter", type(model)
    assert model.thread_count == 1, model.get_params()
    assert model.model_.__class__.__name__ == "CatBoostClassifier", type(model.model_)
    assert not model.model_.get_param("allow_writing_files"), model.model_.get_all_params()

    import shap  # type: ignore[reportMissingImports]

    shap_values = np.asarray(shap.TreeExplainer(model.model_).shap_values(transformed))
    expected_shape = (len(holdout), len(train.columns))
    if shap_values.shape != expected_shape:
        raise AssertionError(f"CatBoost SHAP shape {shap_values.shape} != {expected_shape}")

    print("ok: optional CatBoost raw-category challenger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
