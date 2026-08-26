#!/usr/bin/env python3
"""Verdict gate: prefer accepted models, fall back to global CV winner only when none pass."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]
KINDS = ["gradient_boosting", "random_forest_shap", "logistic_regression"]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fake_runner(rows: dict[str, tuple[float, str]]):
    def run(*args: Any, kind: str, **kwargs: Any) -> dict[str, Any]:
        score, verdict = rows[kind]
        return {
            "kind": kind,
            "cv_score": score,
            "metrics": {"accuracy": score},
            "guards": {"generalization_gap": 0.01, "importance_stability": 0.9, "max_psi": 0.01},
            "verdict": verdict,
            "operating_threshold": 0.5,
            "operating_scenarios": [],
            "best_params": {},
            "trials": [],
            "top_features": [],
            "calibrated_probabilities": [],
            "estimator": None,
        }
    return run


def compare(research: Any, rows: dict[str, tuple[float, str]]) -> dict[str, Any]:
    research.run_classification_autoresearch = fake_runner(rows)
    empty = pd.DataFrame()
    return research.run_multi_model_comparison(empty, [], empty, [], kinds=KINDS, n_iter=3, cv_folds=3)


def main() -> int:
    research = load_module("vgate_research", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")

    # The global CV winner is unstable; choose the highest-scoring accepted model instead.
    gated = compare(research, {
        "gradient_boosting": (0.95, "unstable"),
        "random_forest_shap": (0.82, "accepted"),
        "logistic_regression": (0.80, "accepted"),
    })
    assert gated["selected_from_accepted"], gated
    assert gated["eligible_kinds"] == ["random_forest_shap", "logistic_regression"], gated
    assert gated["best_kind"] == "random_forest_shap", gated
    assert gated["best_result"]["verdict"] == "accepted", gated

    # If every model fails guards, preserve deterministic fallback to the global CV winner.
    fallback = compare(research, {
        "gradient_boosting": (0.95, "unstable"),
        "random_forest_shap": (0.82, "overfit"),
        "logistic_regression": (0.80, "below_objective"),
    })
    assert not fallback["selected_from_accepted"], fallback
    assert fallback["eligible_kinds"] == KINDS, fallback
    assert fallback["best_kind"] == "gradient_boosting", fallback

    print("ok: verdict gate (accepted-first + all-failed fallback)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
