#!/usr/bin/env python3
"""A failed final holdout must not trigger selection of an accepted runner-up."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KINDS = ["gradient_boosting", "random_forest_shap", "logistic_regression"]


def main() -> int:
    spec = importlib.util.spec_from_file_location("verdict_gate", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("autoresearch module unavailable")
    research = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = research
    spec.loader.exec_module(research)
    scores = dict(zip(KINDS, [0.95, 0.82, 0.80], strict=True))
    evaluated = []
    def select(*args: Any, kind: str, **kwargs: Any):
        assert kwargs["selection_only"]
        return {"kind": kind, "cv_score": scores[kind], "n_iter": 1, "search": SimpleNamespace(best_params_={}), "fit_counts": research.fit_counts(1, 3), "cv_receipts": []}
    def evaluate(candidate, *args, **kwargs):
        evaluated.append(candidate["kind"])
        return {**candidate, "verdict": "unstable"}
    setattr(research, "run_classification_autoresearch", select)
    setattr(research, "evaluate_classification_candidate", evaluate)
    empty = pd.DataFrame()
    outcome = research.run_multi_model_comparison(empty, [], empty, [], kinds=KINDS, n_iter=3, cv_folds=3)
    assert outcome["best_kind"] == KINDS[0]
    assert evaluated == [KINDS[0]], "holdout was exposed for multiple candidates"
    assert outcome["best_result"]["verdict"] == "unstable"
    assert outcome["selection_basis"] == "training_cv" and outcome["holdout_evaluations"] == 1
    assert "selected_from_accepted" not in outcome
    assert all("verdict" not in row for row in outcome["comparison"])
    print("ok: failed final gate preserves CV winner; no accepted-first runner-up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
