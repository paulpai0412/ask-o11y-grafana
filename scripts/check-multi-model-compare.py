#!/usr/bin/env python3
"""Self-check for multi-model comparison with parallel-friendly autotune."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
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


def main() -> int:
    research = load_module("mmc_research", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
    presentation = load_module("mmc_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")

    values, target = make_classification(n_samples=400, n_features=6, n_informative=4, weights=[0.75, 0.25], random_state=42)
    frame = pd.DataFrame(values, columns=[f"f{i}" for i in range(values.shape[1])])
    x_tr, x_te, y_tr, y_te = train_test_split(frame, target, test_size=0.25, stratify=target, random_state=42)

    kinds = ["gradient_boosting", "random_forest_shap", "logistic_regression"]
    outcome = research.run_multi_model_comparison(
        x_tr, y_tr, x_te, y_te,
        kinds=kinds, objective="accuracy", seed=42, n_iter=3, cv_folds=3,
        cost_matrix={"false_negative": 3.0, "false_positive": 1.0},
    )

    # Comparison table: one row per kind, each with metrics + guards + verdict.
    assert len(outcome["comparison"]) == len(kinds), outcome["comparison"]
    for row in outcome["comparison"]:
        assert row["kind"] in kinds
        assert "accuracy" in row["metrics"]
        assert "verdict" in row
        assert "operating_threshold" in row

    # Best is selected by CV score among accepted models when available (verdict gate).
    assert outcome["best_kind"] in kinds
    best_row = next(r for r in outcome["comparison"] if r["kind"] == outcome["best_kind"])
    cv_scores = {r["kind"]: r["cv_score"] for r in outcome["comparison"]}
    eligible = set(outcome["eligible_kinds"])
    accepted_set = {r["kind"] for r in outcome["comparison"] if r["verdict"] == "accepted"}
    if outcome["selected_from_accepted"]:
        if eligible != accepted_set:
            raise AssertionError(f"eligible_kinds {eligible} != accepted set {accepted_set}")
        if best_row["verdict"] != "accepted":
            raise AssertionError(f"best_kind {outcome['best_kind']} not accepted: {best_row}")
        if cv_scores[outcome["best_kind"]] != max(cv_scores[k] for k in eligible):
            raise AssertionError(f"best not max among eligible: {cv_scores}")
    else:
        assert cv_scores[outcome["best_kind"]] == max(cv_scores.values()), cv_scores

    # Budget split: total n_iter ≤ original budget.
    per_kind_budget = outcome["per_kind_budget"]
    assert per_kind_budget * len(kinds) <= 40

    # Presentation: model_comparison chart.
    report = presentation.build_manifest(
        purpose="多模型比較", conclusion="選出最佳模型",
        identity={"run_id": "mmc", "dataset_id": "d", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "t", "task_kind": "binary_classification", "primary_metric": "accuracy", "positive_class": "1", "threshold": 0.5, "threshold_cost_approved": False},
        data={"rows": 400, "features": 6, "train_rows": 300, "holdout_rows": 100, "split_kind": "stratified_holdout", "excluded_fields": []},
        process={"model_family": "multi-model", "search_budget": 9, "completed_trials": 9, "cv_folds": 3, "preprocessing_fit_scope": "training_only", "best_params": {}},
        baseline_metrics={"accuracy": 0.75}, selected_metrics=best_row["metrics"],
        guards={**best_row["guards"], "verdict": best_row["verdict"]},
        trials=best_row["trials"], features=[{"name": "f0", "importance": 0.1, "explanation": "x"}],
        limitations=["觀察性資料。"],
    )
    report["model_comparison"] = [
        {"kind": r["kind"], "accuracy": r["metrics"]["accuracy"], "cv_score": r["cv_score"], "verdict": r["verdict"], "is_best": r["kind"] == outcome["best_kind"]}
        for r in outcome["comparison"]
    ]
    presentation.validate_manifest(report)

    # Render comparison chart.
    with tempfile.TemporaryDirectory() as tmp:
        produced = presentation.render_model_comparison(report, Path(tmp))
        assert (Path(tmp) / "model_comparison.png").exists()
        assert (Path(tmp) / "model_comparison.png").stat().st_size > 3000
        presentation.validate_manifest(report)

    print("ok: multi-model comparison")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
