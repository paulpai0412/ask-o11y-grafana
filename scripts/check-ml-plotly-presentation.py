#!/usr/bin/env python3
"""Public-seam check: every ML asset gets a sanitized Plotly figure alongside its PNG."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

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
    presentation = load_module("plotly_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")
    contract = load_module("plotly_contract", ROOT / "ml_plotly_contract.py")
    assert hasattr(presentation, "build_plotly_figures"), "build_plotly_figures missing"

    manifest = presentation.build_manifest(
        purpose="驗證 Plotly-first 圖表產出。",
        conclusion="每張 PNG 都有對應的 bounded Plotly figure。",
        identity={"run_id": "plotly-all", "dataset_id": "d", "ontology_snapshot_id": "s", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={
            "target": "churn", "task_kind": "binary_classification", "primary_metric": "pr_auc",
            "positive_class": "1", "threshold": 0.4, "threshold_cost_approved": True,
            "cost_matrix": {"false_negative": 3.0, "false_positive": 1.0},
        },
        data={"rows": 40, "features": 3, "train_rows": 32, "holdout_rows": 8, "split_kind": "stratified_holdout", "minority_rate": 0.5, "excluded_fields": []},
        process={
            "model_family": "xgboost", "search_budget": 4, "completed_trials": 4, "cv_folds": 2,
            "preprocessing_fit_scope": "training_only", "calibration_method": "isotonic",
            "best_params": {"depth": 3},
        },
        baseline_metrics={"accuracy": 0.5},
        selected_metrics={"accuracy": 0.75, "pr_auc": 0.8, "roc_auc": 0.8},
        guards={"generalization_gap": 0.01, "importance_stability": 0.8, "max_psi": 0.01, "verdict": "accepted"},
        trials=[{"rank": rank, "cv_score": 0.8 - rank * 0.01, "params": {"trial": rank}} for rank in range(1, 6)],
        features=[{"name": name, "importance": score, "explanation": "關聯非因果"} for name, score in (("Contract", 0.4), ("Tenure", 0.3), ("Charge", 0.2))],
        limitations=["測試資料。"],
    )

    rng = np.random.default_rng(7)
    frame = pd.DataFrame({
        "churn": [0, 1] * 20,
        "Contract": rng.choice(["Month-to-Month", "Two Year"], 40),
        "Tenure": rng.integers(1, 72, 40),
        "Charge": rng.normal(60, 15, 40),
    })
    labels = [0, 1, 0, 1, 0, 1, 1, 0, 1, 0]
    probabilities = [0.1, 0.85, 0.2, 0.7, 0.3, 0.9, 0.55, 0.15, 0.8, 0.4]
    shap_values = rng.normal(0, 0.3, size=(30, 6))
    sample_values = rng.normal(50, 10, size=(30, 6))
    feature_names = ["Contract", "Tenure", "Charge", "num__Age", "cat__Contract_Two Year", "cat__Offer_None"]

    with tempfile.TemporaryDirectory() as tmp:
        presentation.render_assets(manifest, Path(tmp), y_true=labels, probabilities=probabilities, frame=frame, target="churn")
        png_names = {asset["name"] for asset in manifest["artifacts"]}

        figures = presentation.build_plotly_figures(
            manifest, y_true=labels, probabilities=probabilities, frame=frame, target="churn",
            shap_values=shap_values,
            feature_names=feature_names,
            sample_values=sample_values,
        )

        # Data-driven views: only charts where interactivity adds value.
        # Each view adapts the Plotly trace type to its data shape.
        expected = {
            "data_profile": "bar",
            "correlation_analysis": "heatmap",
            "trial_history": "scatter",
            "feature_importance": "bar",
            "confusion_matrix": "heatmap",
            "roc_pr_curves": "scatter",
            "calibration_curve": "scatter",
            "threshold_cost_curve": "scatter",
            "shap_summary": "scatter",
        }
        missing = expected.keys() - figures.keys()
        assert not missing, f"missing plotly figures: {sorted(missing)}"
        assert len(figures) <= contract.MAX_FIGURES

        for name, expected_type in expected.items():
            sanitized = figures[name]
            if set(sanitized) != {"data", "layout", "config"}:
                raise AssertionError(f"{name} figure keys invalid: {sorted(sanitized)}")
            encoded = json.dumps(sanitized, ensure_ascii=False).encode()
            if len(encoded) > contract.MAX_FIGURE_BYTES:
                raise AssertionError(f"{name} figure exceeds byte budget: {len(encoded)}")
            trace_types = {trace["type"] for trace in sanitized["data"]}
            if expected_type not in trace_types:
                raise AssertionError(f"{name} should adapt to {expected_type}, got {sorted(trace_types)}")

        # Determinism: same inputs → identical JSON.
        repeat = presentation.build_plotly_figures(
            manifest, y_true=labels, probabilities=probabilities, frame=frame, target="churn",
            shap_values=shap_values,
            feature_names=feature_names,
            sample_values=sample_values,
        )
        if json.dumps(figures, sort_keys=True) != json.dumps(repeat, sort_keys=True):
            raise AssertionError("plotly figures are not deterministic")

        # Threshold-cost figure carries the locked train-OOF threshold as annotation fact.
        try:
            threshold = float(manifest["objective"]["threshold"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AssertionError("manifest threshold missing") from exc
        encoded_cost = json.dumps(figures["threshold_cost_curve"])
        if f"{threshold:.3f}" not in encoded_cost and str(round(threshold, 3)) not in encoded_cost:
            raise AssertionError(f"locked threshold {threshold} not embedded in threshold_cost_curve")

        # PNG assets remain the fallback evidence set.
        assert {"confusion_matrix.png", "calibration_curve.png", "threshold_cost_curve.png", "roc_pr_curves.png"} <= png_names

    print("ok: plotly figures for all ML assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
