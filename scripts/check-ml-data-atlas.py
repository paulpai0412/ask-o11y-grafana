#!/usr/bin/env python3
"""Public-seam check for ontology-driven data atlas PNG and Plotly output."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_presentation.py"
    spec = importlib.util.spec_from_file_location("ml_data_atlas_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def report(module):
    return module.build_manifest(
        purpose="先用 ontology 理解資料形狀，再評估模型。",
        conclusion="資料品質與語義關係已呈現；模型證據另列。",
        identity={"run_id": "atlas", "dataset_id": "telco", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "churn", "task_kind": "binary_classification", "primary_metric": "pr_auc", "positive_class": "1", "threshold": 0.5, "threshold_cost_approved": False},
        data={"rows": 5, "features": 4, "train_rows": 4, "holdout_rows": 1, "split_kind": "stratified_holdout", "excluded_fields": [{"name": "leak", "reason": "target proxy"}]},
        process={"model_family": "xgboost", "search_budget": 2, "completed_trials": 2, "cv_folds": 2, "preprocessing_fit_scope": "training_only", "best_params": {}},
        baseline_metrics={"accuracy": 0.6}, selected_metrics={"accuracy": 0.8},
        guards={"generalization_gap": 0.01, "importance_stability": 0.8, "max_psi": 0.02, "verdict": "accepted"},
        trials=[], features=[{"name": "temp", "importance": 0.6, "explanation": "關聯非因果"}], limitations=["測試資料。"],
    )


def main() -> int:
    module = load_module()
    frame = pd.DataFrame({
        "churn": [0, 0, 1, 0, 1],
        "temp": [10.0, 20.0, 30.0, 40.0, None],
        "flow": [1.0, 2.0, 3.0, 4.0, 5.0],
        "contract": ["A", "A", "A", "A", "B"],
        "constant": [7, 7, 7, 7, 7],
    })
    fields_view = [
        {"name": "churn", "semantic_kind": "label_target", "unit": None, "analysis_role": "target"},
        {"name": "temp", "semantic_kind": "measurement", "unit": "C", "analysis_role": "feature"},
        {"name": "flow", "semantic_kind": "measurement", "unit": "L/s", "analysis_role": "feature"},
        {"name": "contract", "semantic_kind": "treatment_candidate", "unit": None, "analysis_role": "feature"},
        {"name": "constant", "semantic_kind": "measurement", "unit": "count", "analysis_role": "feature"},
        {"name": "leak", "semantic_kind": "target_proxy", "unit": None, "analysis_role": "forbidden", "reason": "post-outcome proxy"},
    ]

    atlas = module.build_data_atlas(frame, target="churn", fields_view=fields_view)
    by_name = {item["name"]: item for item in atlas["fields"]}
    assert by_name["temp"]["missing_rate"] == 0.2, by_name["temp"]
    assert by_name["contract"]["categorical"]["top_share"] == 0.8, by_name["contract"]
    assert by_name["constant"]["flag"] == "low_variance", by_name["constant"]
    assert not by_name["leak"]["available"] and by_name["leak"]["role"] == "forbidden", by_name["leak"]
    assert atlas["correlation"]["columns"] == ["temp", "flow", "constant"], atlas["correlation"]
    assert len(atlas["correlation"]["matrix"]) == 3

    manifest = report(module)
    with tempfile.TemporaryDirectory() as tmp:
        assets = module.render_data_atlas_assets(
            manifest, Path(tmp), frame=frame, target="churn", fields_view=fields_view,
        )
        names = {item["name"] for item in assets}
        expected = {"ontology_field_map.png", "distribution_small_multiples.png", "semantic_correlation.png"}
        assert expected <= names, names
        for name in expected:
            path = Path(tmp) / name
            assert path.exists() and path.stat().st_size > 3000, path
        assert manifest["data_atlas"]["warnings"] == ["low_variance:constant"], manifest["data_atlas"]
        module.validate_manifest(manifest)

    inferred = module.build_data_atlas(frame, target="churn", fields_view=[])
    inferred_by_name = {item["name"]: item for item in inferred["fields"]}
    assert inferred["metadata_source"] == "inferred", inferred
    assert inferred_by_name["temp"]["semantic_kind"] == "measurement" and inferred_by_name["temp"]["unit"] == "°C", inferred_by_name["temp"]
    assert inferred_by_name["contract"]["semantic_kind"] == "categorical", inferred_by_name["contract"]
    assert all(item["metadata_source"] == "inferred" for item in inferred["fields"]), inferred

    figures = module.build_plotly_figures(manifest, frame=frame, target="churn", fields_view=fields_view)
    expected_plotly = {"ontology_field_map", "distribution_small_multiples", "semantic_correlation", "baseline_error_comparison"}
    assert expected_plotly <= set(figures), sorted(figures)
    removed = {"analysis_process", "trial_history", "per_1000_outcomes", "correlation_analysis"}
    assert not (set(figures) & removed), sorted(set(figures) & removed)

    wide = pd.DataFrame({**{f"f{index}": range(20) for index in range(12)}, "churn": [0, 1] * 10})
    wide_figures = module.build_plotly_figures(report(module), frame=wide, target="churn", fields_view=[])
    wide_layout = wide_figures["distribution_small_multiples"]["layout"]
    assert "xaxis12" in wide_layout and "yaxis12" in wide_layout, sorted(wide_layout)

    print("ok: ontology-driven ML data atlas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
