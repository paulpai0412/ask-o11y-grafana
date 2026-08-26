#!/usr/bin/env python3
"""Self-check for cost-based threshold optimization, calibration, and scenarios."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]
from sklearn.datasets import make_classification  # type: ignore[reportMissingImports]
from sklearn.model_selection import train_test_split  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(research, cost_matrix=None, minimum_recall=None):
    values, target = make_classification(n_samples=500, n_features=8, n_informative=6, weights=[0.75, 0.25], class_sep=1.0, random_state=7)
    frame = pd.DataFrame(values, columns=[f"f{i}" for i in range(values.shape[1])])
    x_tr, x_te, y_tr, y_te = train_test_split(frame, target, test_size=0.25, stratify=target, random_state=42)
    return research.run_classification_autoresearch(
        x_tr, y_tr, x_te, y_te, kind="gradient_boosting", objective="accuracy", seed=42,
        n_iter=2, cv_folds=3, cost_matrix=cost_matrix, minimum_recall=minimum_recall,
    )


def main() -> int:
    research = load_module("cost_research", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")

    # scale_pos_weight is now part of the declared search space.
    _, space = research._estimator("gradient_boosting", 42)
    # scale_pos_weight is now part of the declared LightGBM search space.
    assert "model__scale_pos_weight" in space, list(space)

    recall_first = run(research, cost_matrix={"false_negative": 5.0, "false_positive": 1.0})
    precision_first = run(research, cost_matrix={"false_negative": 1.0, "false_positive": 5.0})

    # A high missed-positive cost pushes the operating threshold down (recall up).
    assert recall_first["operating_threshold"] < 0.5, recall_first["operating_threshold"]
    # A high false-alarm cost pushes it up.
    assert precision_first["operating_threshold"] > 0.5, precision_first["operating_threshold"]
    # Recall-oriented operating point has fewer missed positives per 1,000 than the balanced point.
    balanced = next(s for s in recall_first["operating_scenarios"] if s["name"] == "balanced")
    chosen = next(s for s in recall_first["operating_scenarios"] if s["name"] == "cost_optimal")
    assert chosen["fn_per_1000"] <= balanced["fn_per_1000"], f"chosen={chosen}, balanced={balanced}"

    # minimum_recall constraint is respected by the chosen scenario.
    constrained = run(research, minimum_recall=0.8)
    chosen_c = next(s for s in constrained["operating_scenarios"] if s["name"] == "cost_optimal")
    assert chosen_c["recall"] >= 0.8, chosen_c

    # Calibrated probabilities are valid probabilities.
    probs = np.asarray(recall_first["calibrated_probabilities"], dtype=float)
    assert len(probs) == len(recall_first["probabilities"]) and probs.min() >= 0 and probs.max() <= 1

    # Final holdout metrics reflect the selected recall-leaning operating point.
    assert recall_first["metrics"]["recall_at_threshold"] >= balanced["recall"] - 0.02

    # Manifest accepts an optional operating_scenarios section.
    presentation = load_module("cost_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")
    manifest = presentation.build_manifest(
        purpose="p", conclusion="c",
        identity={"run_id": "r", "dataset_id": "d", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "t", "task_kind": "binary_classification", "primary_metric": "accuracy", "positive_class": "1", "threshold": recall_first["operating_threshold"], "threshold_cost_approved": True},
        data={"rows": 500, "features": 8, "train_rows": 375, "holdout_rows": 125, "split_kind": "stratified_holdout", "excluded_fields": []},
        process={"model_family": "LightGBM", "search_budget": 2, "completed_trials": 2, "cv_folds": 3, "preprocessing_fit_scope": "training_only", "best_params": {}},
        baseline_metrics={"accuracy": 0.75}, selected_metrics=recall_first["metrics"],
        guards={**recall_first["guards"], "verdict": "accepted"},
        trials=recall_first["trials"], features=[{"name": "f0", "importance": 0.1, "explanation": "x"}],
        limitations=["觀察性資料。"],
    )
    manifest["operating_scenarios"] = recall_first["operating_scenarios"]
    presentation.validate_manifest(manifest)

    print("ok: cost-based threshold optimization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
