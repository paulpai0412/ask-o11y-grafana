#!/usr/bin/env python3
"""Public-seam TDD check for feature-target relationships and holdout error slices."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_presentation.py"
    spec = importlib.util.spec_from_file_location("ml_data_story_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def report(module):
    return module.build_manifest(
        purpose="用資料本身解釋訊號，再呈現模型錯誤集中在哪裡。",
        conclusion="描述性訊號與 holdout 錯誤切片已產出，不作因果或公平性結論。",
        identity={"run_id": "story", "dataset_id": "d", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "churn", "task_kind": "binary_classification", "primary_metric": "pr_auc", "positive_class": "1", "threshold": 0.5, "threshold_cost_approved": False},
        data={"rows": 12, "features": 2, "train_rows": 8, "holdout_rows": 4, "split_kind": "stratified_holdout", "excluded_fields": []},
        process={"model_family": "xgboost", "search_budget": 2, "completed_trials": 2, "cv_folds": 2, "preprocessing_fit_scope": "training_only", "best_params": {}},
        baseline_metrics={"accuracy": 0.5, "pr_auc": 0.45, "roc_auc": 0.55}, selected_metrics={"accuracy": 0.75, "pr_auc": 0.7, "roc_auc": 0.8},
        guards={"generalization_gap": 0.01, "importance_stability": 0.8, "max_psi": 0.02, "verdict": "accepted"},
        trials=[],
        features=[
            {"name": "tenure", "importance": 0.6, "explanation": "關聯非因果"},
            {"name": "contract", "importance": 0.4, "explanation": "關聯非因果"},
        ],
        limitations=["測試資料。"],
    )


def main() -> int:
    module = load_module()
    frame = pd.DataFrame({
        "tenure": list(range(1, 13)),
        "contract": ["month"] * 6 + ["annual"] * 6,
        "churn": [1, 1, 1, 1, 0, 0, 1, 0, 0, 0, 0, 0],
    })
    relationships = module.build_feature_target_relationships(frame, target="churn", features=["tenure", "contract"])
    assert [item["feature"] for item in relationships] == ["tenure", "contract"], relationships
    contract = relationships[1]
    by_label = {item["label"]: item for item in contract["groups"]}
    assert by_label["month"]["positive_rate"] == 0.6667, by_label
    assert by_label["annual"]["positive_rate"] == 0.1667, by_label

    evaluation = pd.DataFrame({"tenure": [1, 2, 10, 11, 3, 12], "contract": ["month", "month", "annual", "annual", "month", "annual"]})
    labels = [1, 1, 0, 0, 1, 0]
    probabilities = [0.8, 0.2, 0.7, 0.1, 0.9, 0.6]
    slices = module.build_error_slices(
        evaluation, y_true=labels, probabilities=probabilities, threshold=0.5,
        features=["contract", "tenure"],
    )
    contract_slice = next(item for item in slices if item["feature"] == "contract")
    slice_by_label = {item["label"]: item for item in contract_slice["groups"]}
    assert slice_by_label["month"]["fn"] == 1 and slice_by_label["annual"]["fp"] == 2, slice_by_label

    manifest = report(module)
    with tempfile.TemporaryDirectory() as tmp:
        assets = module.render_data_story_assets(
            manifest, Path(tmp), frame=frame, target="churn", evaluation_frame=evaluation,
            y_true=labels, probabilities=probabilities,
        )
        names = {item["name"] for item in assets}
        expected = {"feature_target_relationships.png", "error_slice_analysis.png"}
        assert expected <= names, names
        for name in expected:
            path = Path(tmp) / name
            assert path.exists() and path.stat().st_size > 3000, path
        module.validate_manifest(manifest)

    figures = module.build_plotly_figures(
        manifest, frame=frame, target="churn", evaluation_frame=evaluation,
        y_true=labels, probabilities=probabilities,
    )
    assert {"feature_target_relationships", "error_slice_analysis"} <= set(figures), sorted(figures)
    print("ok: ML data story relationships + holdout error slices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
