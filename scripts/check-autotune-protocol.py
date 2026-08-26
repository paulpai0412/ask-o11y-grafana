#!/usr/bin/env python3
"""Self-check for Workstream G: budgeted autoresearch with generalization guards."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd  # type: ignore[reportMissingImports]
from sklearn.datasets import make_classification  # type: ignore[reportMissingImports]
from sklearn.model_selection import train_test_split  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_autoresearch.py"
    spec = importlib.util.spec_from_file_location("ml_autoresearch", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    research = load_module()
    values, target = make_classification(n_samples=600, n_features=12, n_informative=8, weights=[0.75, 0.25], class_sep=1.2, random_state=42)
    frame = pd.DataFrame(values, columns=[f"f{index}" for index in range(values.shape[1])])
    x_train, x_test, y_train, y_test = train_test_split(frame, target, test_size=0.2, stratify=target, random_state=42)

    result = research.run_classification_autoresearch(
        x_train, y_train, x_test, y_test,
        kind="gradient_boosting", objective="accuracy", seed=42, n_iter=4, cv_folds=3,
        objective_minimum=0.75,
    )
    assert 1 <= len(result["trials"]) <= 5
    assert result["best_params"]
    assert not result["holdout_used_during_search"]
    assert 0 <= result["metrics"]["accuracy"] <= 1
    assert result["guards"]["generalization_gap"] <= 1
    assert result["verdict"] in {"accepted", "overfit", "unstable", "drift", "below_objective"}

    forced = research.generalization_verdict(cv_score=0.96, holdout_score=0.70, importance_stability=0.9, max_psi=0.05, objective_minimum=0.5)
    assert forced == "overfit"
    below = research.generalization_verdict(cv_score=0.72, holdout_score=0.70, importance_stability=0.9, max_psi=0.05, objective_minimum=0.8)
    assert below == "below_objective"

    print("ok: autoresearch tuning + generalization guards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
