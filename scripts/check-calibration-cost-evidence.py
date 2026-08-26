#!/usr/bin/env python3
"""Public-seam check for calibration and threshold-cost evidence in ML presentation output."""
from __future__ import annotations

import copy
import importlib.util
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_presentation.py"
    spec = importlib.util.spec_from_file_location("calibration_cost_presentation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def report(module):
    return module.build_manifest(
        purpose="驗證校正與 3:1 成本門檻證據。",
        conclusion="門檻由 train OOF 選擇，holdout 只作一次評估。",
        identity={"run_id": "calibration-cost", "dataset_id": "d", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={
            "target": "churn", "task_kind": "binary_classification", "primary_metric": "pr_auc",
            "positive_class": "1", "threshold": 0.7, "threshold_cost_approved": True,
            "cost_matrix": {"false_negative": 3.0, "false_positive": 1.0},
        },
        data={"rows": 8, "features": 2, "train_rows": 32, "holdout_rows": 8, "split_kind": "stratified_holdout", "minority_rate": 0.5, "excluded_fields": []},
        process={
            "model_family": "xgboost", "search_budget": 2, "completed_trials": 2, "cv_folds": 2,
            "preprocessing_fit_scope": "training_only", "calibration_method": "isotonic", "best_params": {},
        },
        baseline_metrics={"accuracy": 0.5},
        selected_metrics={"accuracy": 0.75, "pr_auc": 0.8, "roc_auc": 0.8},
        guards={"generalization_gap": 0.01, "importance_stability": 0.8, "max_psi": 0.01, "verdict": "accepted"},
        trials=[], features=[], limitations=["測試資料。"],
    )


def main() -> int:
    module = load_module()
    manifest = report(module)
    labels = [0, 0, 0, 0, 1, 1, 1, 1]
    calibrated_probabilities = [0.05, 0.2, 0.4, 0.6, 0.3, 0.65, 0.8, 0.95]

    with tempfile.TemporaryDirectory() as tmp:
        assets = module.render_assets(
            manifest, Path(tmp), y_true=labels, probabilities=calibrated_probabilities,
        )
        by_name = {item["name"]: item for item in assets}
        for name in ("calibration_curve.png", "threshold_cost_curve.png"):
            assert name in by_name, by_name
            path = Path(tmp) / name
            assert path.exists() and path.stat().st_size > 3000, path
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), path
            assert by_name[name]["caption"].strip() and by_name[name]["alt_text"].strip(), by_name[name]

    evidence = manifest["evaluation_evidence"]
    calibration = evidence["calibration"]
    assert calibration["selected_on"] == "train_oof", calibration
    assert calibration["evaluated_on"] == "holdout", calibration
    assert calibration["method"] == "isotonic", calibration
    assert calibration["bins"] == 8, calibration
    assert math.isclose(calibration["brier_score"], 0.1521875, rel_tol=0, abs_tol=1e-8), calibration

    cost = evidence["threshold_cost"]
    assert cost["selected_on"] == "train_oof", cost
    assert cost["evaluated_on"] == "holdout", cost
    assert math.isclose(cost["threshold"], 0.7), cost
    assert math.isclose(cost["fn_per_1000"], 250.0), cost
    assert math.isclose(cost["fp_per_1000"], 0.0), cost
    assert math.isclose(cost["weighted_cost_per_1000"], 750.0), cost
    module.validate_manifest(manifest)

    invalid = copy.deepcopy(manifest)
    invalid["evaluation_evidence"]["calibration"]["selected_on"] = "holdout"
    try:
        module.validate_manifest(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted holdout-selected calibration evidence")

    print("ok: calibration + threshold-cost evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
