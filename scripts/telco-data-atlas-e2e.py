#!/usr/bin/env python3
"""Telco E2E for ontology data-atlas PNG + Plotly output inside the sandbox image."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]
import shap  # type: ignore[reportMissingImports]
from sklearn.model_selection import train_test_split  # type: ignore[reportMissingImports]

from ml_autoresearch import run_classification_autoresearch  # type: ignore[reportMissingImports]
from ml_presentation import (  # type: ignore[reportMissingImports]
    build_manifest,
    build_plotly_figures,
    recommend_spec_values,
    render_assets,
    render_shap_summary,
    validate_manifest,
)

INPUT = Path("/data/telco-clean.csv")
OUTPUT = Path("/out")
SEED = 42


def main() -> int:
    def number(value: Any, where: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Telco E2E {where} is not numeric") from exc

    started = time.perf_counter()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    data = pd.read_csv(INPUT)
    target = "Churn"
    excluded = {target, "Satisfaction Score", "Total Revenue", "Latitude", "Longitude", "CLTV"}
    features = [str(column) for column in data.columns if column not in excluded]
    if len(features) != 33:
        raise RuntimeError(f"unexpected Telco feature count: {len(features)}")
    x = data[features]
    y = data[target].astype(int)
    x_train, x_hold, y_train, y_hold = train_test_split(x, y, test_size=0.2, random_state=SEED, stratify=y)

    result = run_classification_autoresearch(
        x_train, y_train, x_hold, y_hold,
        kind="xgboost", objective="pr_auc", seed=SEED,
        n_iter=2, cv_folds=3,
        cost_matrix={"false_negative": 3.0, "false_positive": 1.0},
    )
    probabilities = result["calibrated_probabilities"]
    baseline_accuracy = number((y_hold == 0).mean(), "baseline accuracy")
    fields_view = []
    for name in features:
        if pd.api.types.is_numeric_dtype(data[name]):
            semantic_kind = "measurement"
        else:
            semantic_kind = "treatment_candidate"
        unit = "month" if "Month" in name or "Tenure" in name else "USD" if any(word in name for word in ("Charge", "Refund", "Revenue")) else None
        fields_view.append({"name": name, "semantic_kind": semantic_kind, "unit": unit, "analysis_role": "feature"})
    fields_view.extend([
        {"name": "Churn", "semantic_kind": "label_target", "unit": None, "analysis_role": "target"},
        {"name": "Satisfaction Score", "semantic_kind": "target_proxy", "unit": None, "analysis_role": "forbidden", "reason": "post-outcome proxy"},
        {"name": "CLTV", "semantic_kind": "post_outcome", "unit": "USD", "analysis_role": "forbidden", "reason": "post-outcome value"},
        {"name": "Total Revenue", "semantic_kind": "post_outcome", "unit": "USD", "analysis_role": "forbidden", "reason": "post-outcome aggregate"},
    ])

    manifest = build_manifest(
        purpose="預測 Telco 客戶流失；先由 ontology 檢視資料形狀、集中性與相關性。",
        conclusion="資料地圖與模型驗證均已產出；營運前仍須確認 3:1 漏判成本假設。",
        identity={"run_id": "telco-data-atlas-e2e", "dataset_id": "telco", "ontology_snapshot_id": "telco-e2e", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": SEED},
        objective={"target": target, "task_kind": "binary_classification", "primary_metric": "pr_auc", "positive_class": 1, "threshold": result["operating_threshold"], "threshold_cost_approved": True, "cost_matrix": {"false_negative": 3.0, "false_positive": 1.0}},
        data={"rows": len(data), "features": len(features), "train_rows": len(x_train), "holdout_rows": len(x_hold), "split_kind": "stratified_holdout", "minority_rate": round(number(y.mean(), "minority rate"), 4), "excluded_fields": [{"name": name, "reason": "ontology forbidden"} for name in sorted(excluded - {target})]},
        process={"model_family": "xgboost", "search_budget": 2, "completed_trials": 2, "cv_folds": 3, "preprocessing_fit_scope": "training_only", "calibration_method": "isotonic", "best_params": result["best_params"]},
        baseline_metrics={"accuracy": baseline_accuracy},
        selected_metrics=result["metrics"],
        guards={**result["guards"], "verdict": result["verdict"]},
        trials=result["trials"],
        features=[{"name": item["name"], "importance": item["importance"], "explanation": "模型關聯，不代表因果"} for item in result["top_features"]],
        limitations=["觀察性資料，不能解讀為因果。", "本次 3:1 成本比僅供 E2E 驗證。"],
    )
    render_assets(
        manifest, OUTPUT, y_true=y_hold.tolist(), probabilities=probabilities,
        frame=data[features + [target]], target=target, fields_view=fields_view,
        evaluation_frame=x_hold, target_values=y.tolist(),
    )

    estimator = result["estimator"]
    preprocess = estimator.named_steps["preprocess"]
    model = estimator.named_steps["model"]
    sample = x_hold.sample(min(200, len(x_hold)), random_state=SEED)
    transformed = preprocess.transform(sample)
    if hasattr(transformed, "toarray"):
        transformed = transformed.toarray()
    shap_values = np.asarray(shap.TreeExplainer(model).shap_values(transformed))
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, -1]
    transformed_names = [str(name).split("__", 1)[-1] for name in preprocess.get_feature_names_out()]
    render_shap_summary(manifest, shap_values, transformed_names, transformed, OUTPUT)
    shap_by_column: dict[str, np.ndarray] = {}
    for index, name in enumerate(transformed_names):
        source = next((feature for feature in features if name == feature or name.startswith(feature + "_")), name)
        shap_by_column[source] = shap_by_column.get(source, np.zeros(len(shap_values))) + shap_values[:, index]
    recommend_spec_values(manifest, shap_by_column=shap_by_column, sample_frame=sample, top_n=3)

    figures = build_plotly_figures(
        manifest, y_true=y_hold.tolist(), probabilities=probabilities,
        frame=data[features + [target]], target=target,
        shap_values=shap_values, feature_names=transformed_names,
        sample_values=transformed, fields_view=fields_view,
        evaluation_frame=x_hold, target_values=y.tolist(),
    )
    for name, figure in figures.items():
        (OUTPUT / f"ml-plotly-{name}.json").write_text(json.dumps(figure, ensure_ascii=False), encoding="utf-8")
    (OUTPUT / "ml-presentation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    validate_manifest(manifest)

    png_names = {item["name"] for item in manifest["artifacts"]}
    expected = {"ontology_field_map.png", "distribution_small_multiples.png", "semantic_correlation.png", "data_profile.png", "feature_target_relationships.png", "error_slice_analysis.png", "baseline_error_comparison.png", "generalization_health.png", "feature_importance.png", "confusion_matrix.png", "roc_pr_curves.png", "calibration_curve.png", "threshold_cost_curve.png", "shap_summary.png"}
    removed = {"analysis_process.png", "trial_history.png", "per_1000_outcomes.png", "correlation_analysis.png"}
    if expected != png_names or any(not OUTPUT.joinpath(name).exists() for name in expected):
        raise RuntimeError(f"Telco PNG set mismatch: missing={sorted(expected - png_names)}, extra={sorted(png_names - expected)}")
    if png_names & removed:
        raise RuntimeError(f"deleted Telco assets returned: {sorted(png_names & removed)}")
    if {name.removesuffix(".png") for name in expected} != set(figures):
        raise RuntimeError(f"Telco Plotly set mismatch: {sorted(figures)}")

    summary = {
        "rows": len(data), "features": len(features), "artifacts": len(png_names),
        "plotly_figures": len(figures), "pr_auc": result["metrics"].get("pr_auc"),
        "threshold": result["operating_threshold"], "verdict": result["verdict"],
        "wall_seconds": round(time.perf_counter() - started, 1),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
