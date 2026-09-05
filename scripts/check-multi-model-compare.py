#!/usr/bin/env python3
"""Synthetic check: one global budget, training-CV selection, one final holdout."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.datasets import make_classification
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    research = load_module("mmc_research", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
    presentation = load_module("mmc_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")
    values, target = make_classification(n_samples=400, n_features=6, n_informative=4, weights=[0.75, 0.25], random_state=42)
    values = np.asarray(values)
    frame = pd.DataFrame(values, columns=[f"f{i}" for i in range(values.shape[1])])
    x_tr, x_te, y_tr, y_te = train_test_split(frame, target, test_size=0.25, stratify=target, random_state=42)
    kinds = ["random_forest_shap", "logistic_regression"]
    evaluated = []
    original = research.evaluate_classification_candidate
    def evaluate(candidate, *args, **kwargs):
        evaluated.append(candidate["kind"])
        return original(candidate, *args, **kwargs)
    setattr(research, "evaluate_classification_candidate", evaluate)
    try:
        outcome = research.run_multi_model_comparison(x_tr, y_tr, x_te, y_te, kinds=kinds, objective="accuracy", seed=42, n_iter=2, cv_folds=3)
    finally:
        setattr(research, "evaluate_classification_candidate", original)
    rows = outcome["comparison"]
    assert {row["kind"] for row in rows} == set(kinds)
    assert all(not ({"metrics", "verdict", "operating_threshold"} & set(row)) for row in rows)
    assert outcome["best_kind"] == max(rows, key=lambda row: row["cv_score"])["kind"]
    assert evaluated == [outcome["best_kind"]]
    assert outcome["selection_basis"] == "training_cv" and outcome["holdout_evaluations"] == 1
    assert sum(row["search_trials"] for row in rows) == outcome["completed_trials"] <= 2
    assert outcome["execution_mode"] == "sequential"
    try:
        research.run_multi_model_comparison(x_tr, y_tr, x_te, y_te, kinds=kinds, n_iter=1)
    except ValueError as exc:
        assert "budget" in str(exc)
    else:
        raise AssertionError("candidate count exceeded the global budget")
    best = outcome["best_result"]
    report = presentation.build_manifest(
        purpose="多模型比較", conclusion="CV 鎖定候選後再檢查 holdout",
        identity={"run_id": "mmc", "dataset_id": "d", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "t", "task_kind": "binary_classification", "primary_metric": "accuracy", "positive_class": "1", "threshold": best["operating_threshold"], "threshold_cost_approved": False},
        data={"rows": 400, "source_rows": 400, "features": 6, "train_rows": 300, "holdout_rows": 100, "explained_rows": 100, "split_kind": "stratified_holdout", "excluded_fields": []},
        process={"model_family": outcome["best_kind"], "search_budget": 2, "completed_trials": outcome["completed_trials"], "cv_folds": 3, "preprocessing_fit_scope": "training_only", "best_params": best["best_params"]},
        baseline_metrics={"accuracy": 0.75}, selected_metrics=best["metrics"],
        guards={**best["guards"], "verdict": best["verdict"]}, trials=best["trials"],
        features=[{"name": "f0", "importance": 0.1, "explanation": "synthetic check"}], limitations=["Synthetic, not real-data acceptance."],
    )
    report["model_comparison"] = [{**row, "is_best": row["kind"] == outcome["best_kind"]} for row in rows]
    with tempfile.TemporaryDirectory() as temporary:
        presentation.render_model_comparison(report, Path(temporary))
        assert (Path(temporary) / "model_comparison.png").stat().st_size > 3000
        presentation.validate_manifest(report)
    print("ok: CV-only multi-model selection, final holdout, global budget, and comparison chart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
