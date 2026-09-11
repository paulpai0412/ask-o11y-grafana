#!/usr/bin/env python3
"""Synthetic success-path demo for the regression dashboard presentation.

This deliberately uses deterministic synthetic data to show the accepted-model
and candidate-settings presentation. It never changes the production baseline gate
and is not evidence about U1.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt  # type: ignore[reportMissingImports]
import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch/regression-success-demo"
UID = "regression-success-demo"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def png_bytes(path: Path, figure: Any) -> str:
    try:
        figure.savefig(path, format="png", dpi=120, bbox_inches="tight")
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"demo figure could not be saved: {path}") from exc
    return base64.b64encode(data).decode("ascii")


def synthesis() -> dict[str, Any]:
    evidence = lambda ref: [{"fact_ref": ref, "format": "number_2"}]
    view = lambda artifact, headline, observation: {
        "artifact_id": artifact,
        "view_ids": ["image"],
        "view_narratives": [{
            "view_id": "image", "headline": headline, "data_observation": observation,
            "visual_observation": None, "interpretation": "這是展示成功路徑的合成資料，不代表真實機組效果。",
            "limitation": "此圖不能支持真實設備的因果或控制結論。",
            "next_step": "以實際資料重新通過相同驗證閘門後，再由工程人員設計受控試驗。",
            "evidence": evidence("selected_metrics.mae"),
        }],
        "headline": headline, "observation": observation,
        "interpretation": "此面板展示模型通過基準閘門後的報告形狀。",
        "cross_chart_context": "模型證據與候選設定仍須一起閱讀。",
        "limitation": "合成示例不是生產資料證據。",
        "next_step": "替換為真實授權資料並保留相同的 split 與 gate。",
        "evidence": evidence(ref="selected_metrics.mae"), "priority": "primary", "preferred_width": "full",
    }
    return {
        "format": "ask-o11y-report-synthesis-v1",
        "report_title": "迴歸成功路徑展示（合成示例）",
        "thesis": "模型通過基準與保留資料檢查後，系統才展示有支持度與不確定性的候選設定。",
        "thesis_evidence": [{"fact_ref": "guards.can_run_constrained_search", "format": "auto"}],
        "sections": [{
            "section_id": "accepted-model", "title": "模型通過驗證", "purpose": "展示 accepted model 與其保留資料證據。",
            "collapsed": False, "narrative_blocks": [], "panels": [view("demo_model_comparison", "模型通過基準閘門", "保留資料誤差低於簡單基準，因而進入成功展示路徑。")],
        }, {
            "section_id": "candidate-settings", "title": "觀察支持內的候選設定", "purpose": "展示模型通過後如何呈現候選設定與不確定性。",
            "collapsed": False, "narrative_blocks": [], "panels": [view("demo_candidate_settings", "候選設定附支持度與區間", "每個候選設定都限制在已觀察組合，並附上 bootstrap 不確定區間。")],
        }],
    }


def grafana_write(dashboard: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        "http://127.0.0.1:3000/api/dashboards/db",
        data=json.dumps({"dashboard": dashboard, "overwrite": True, "message": "Synthetic regression success-path demo"}, ensure_ascii=False).encode(),
        method="POST",
        headers={"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("Grafana demo dashboard write failed") from exc


def main() -> int:
    regression = load("demo_regression", ROOT / "sandbox-analysis-mcp/ml_regression.py")
    bridge = load("demo_bridge", ROOT / "artifact-bridge-mcp/server.py")
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    n_rows = 180
    control = np.tile(np.array([0.0, 1.0, 2.0, 3.0]), n_rows // 4 + 1)[:n_rows]
    context = rng.normal(50.0, 3.0, n_rows)
    source = np.array(["source-a", "source-b"] * (n_rows // 2))
    target = 100.0 - 8.0 * control + 0.5 * context + rng.normal(0.0, 0.3, n_rows)
    frame = pd.DataFrame({"control": control, "context": context, "source": source})
    target_series = pd.Series(target, name="target")
    train, holdout = frame.iloc[:144], frame.iloc[144:]
    train_target, holdout_target = target_series.iloc[:144], target_series.iloc[144:]
    result = regression.run_multi_model_regression(
        train, train_target, holdout, holdout_target,
        kinds=["ridge", "extra_trees"], seed=42, n_iter=4, cv_folds=3, bootstrap_samples=200,
    )
    assert result["beats_baseline"] and result["can_run_constrained_search"], result
    candidates = regression.search_candidate_settings(
        result["selected_estimator"], train,
        beats_baseline=True, target_direction="minimize", controllable_fields=["control"],
        support_group_fields=["source"], bounds={"control": [0.0, 3.0]}, minimum_support=10, top_k=4, seed=42, bootstrap_samples=200,
    )
    assert candidates["status"] == "candidate_settings" and candidates["candidate_settings"], candidates

    comparison = OUT / "demo_model_comparison.png"
    figure, axis = plt.subplots(figsize=(9, 4.8))
    axis.bar([row["kind"] for row in result["comparison"]], [row["cv_mae_mean"] for row in result["comparison"]])
    axis.set(title="Accepted regression model comparison", ylabel="CV MAE")
    figure.tight_layout()
    comparison_png = png_bytes(comparison, figure)
    plt.close(figure)

    candidate_path = OUT / "demo_candidate_settings.png"
    figure, axis = plt.subplots(figsize=(9, 4.8))
    rows = candidates["candidate_settings"]
    positions = list(range(len(rows)))
    predicted = [row["predicted_target"] for row in rows]
    lower = [row["uncertainty"]["lower"] for row in rows]
    upper = [row["uncertainty"]["upper"] for row in rows]
    axis.errorbar(positions, predicted, yerr=[[max(value - low, 0.0) for value, low in zip(predicted, lower)], [max(high - value, 0.0) for high, value in zip(upper, predicted)]], fmt="o")
    axis.set_xticks(positions, [str(row["settings"]) for row in rows], rotation=25, ha="right")
    axis.set(title="Observed-support candidate settings", ylabel="Predicted target")
    figure.tight_layout()
    candidate_png = png_bytes(candidate_path, figure)
    plt.close(figure)

    manifest = {
        "format": "ask-o11y-ml-regression-v1", "schema_version": "ask-o11y.ml-regression/v1",
        "purpose": "合成資料成功路徑 dashboard 展示，不是 U1 實證。", "conclusion": "模型通過閘門後才展示候選設定；這個結果不能直接用於生產。",
        "objective": {"target": "target", "task_kind": "regression", "primary_metric": "mae", "target_direction": "minimize"},
        "data": {"rows": n_rows, "train_rows": len(train), "holdout_rows": len(holdout), "features": len(frame.columns), "split_kind": "chronological_holdout", "split_field": "sequence"},
        "process": {"algorithms": ["dummy", "ridge", "extra_trees"], "search_budget": 4, "preprocessing_fit_scope": "training_only", "comparison": result["comparison"], "predictor_fields": list(frame.columns), "sample_weight_fields": []},
        "baseline_metrics": {"kind": "dummy", "cv_mae": result["baseline_cv_mae"], "holdout_mae": result["baseline_holdout_mae"]},
        "selected_model": {"kind": result["selected_kind"], "beats_baseline": result["beats_baseline"]}, "selected_metrics": result["holdout_metrics"],
        "guards": {"selected_cv_beats_baseline": result["selected_cv_beats_baseline"], "selected_holdout_beats_baseline": True, "can_run_constrained_search": True, "holdout_evaluations": 1},
        "weighting": {"used": False, "fields": [], "interpretation": "所有欄位均為一般 predictor；未使用 sample weights。"},
        "constrained_search": candidates, "population_filter": {},
        "artifacts": [
            {"name": "demo_model_comparison.png", "caption": "模型通過基準的比較圖", "alt_text": "合成資料的模型比較"},
            {"name": "demo_candidate_settings.png", "caption": "觀察支持內的候選設定", "alt_text": "候選設定與不確定區間"},
        ],
        "limitations": ["此為合成資料展示，不是 U1 實證。", "觀察性關聯不是因果效果。"],
    }
    context = {"org_id": "1", "user_id": "regression-success-demo"}
    run_id = bridge.ARTIFACTS.create_run(context)
    source = bridge.ml_presentation.build_report_source(manifest)
    results = [
        {"mime": {"application/json": json.dumps(source, ensure_ascii=False)}, "display_name": "report-source.json"},
        {"mime": {"image/png": comparison_png}, "display_name": "demo_model_comparison.png"},
        {"mime": {"image/png": candidate_png}, "display_name": "demo_candidate_settings.png"},
        {"mime": {"application/json": json.dumps(manifest, ensure_ascii=False)}, "display_name": "ml-regression.json"},
    ]
    execution_ref = bridge.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": results, "error": None})
    report_manifest = bridge.ml_report_contract.normalize_report_manifest(execution_ref=execution_ref, results=results)
    report_manifest_ref = bridge.ARTIFACTS.write_json(context, run_id, "report-manifest", report_manifest)
    bridge.ARTIFACTS.write_json(context, run_id, "sandbox-provenance", {"executor_kind": "profile_dataset", "trusted_ml_contract": False, "report_manifest_ref": report_manifest_ref})
    prepared = bridge.prepare_ml_report({"report_manifest_ref": report_manifest_ref, "_server_context": context})
    if not prepared.get("ok"):
        raise RuntimeError(f"demo report prepare failed: {prepared}")
    report_context_ref = prepared["refs"]["report_context_ref"]
    inspection = bridge.inspect_report_artifacts({"report_context_ref": report_context_ref, "artifact_ids": ["demo_model_comparison", "demo_candidate_settings"], "mode": "spec", "_server_context": context})
    if not inspection.get("ok"):
        raise RuntimeError(f"demo artifact inspection failed: {inspection}")
    composed = bridge.compose_ml_dashboard({"report_context_ref": report_context_ref, "inspection_refs": [inspection["refs"]["inspection_ref"]], "synthesis": synthesis(), "uid": UID, "title": "Regression Success Demo (synthetic; not U1 evidence)", "_server_context": context})
    if not composed.get("ok"):
        raise RuntimeError(f"demo dashboard composition failed: {composed}")
    resolved = bridge.resolve_dashboard_refs({"dashboard": {"$dashboard_ref": composed["refs"]["dashboard_ref"]}, "_server_context": context})
    if not resolved.get("ok"):
        raise RuntimeError(f"demo dashboard resolution failed: {resolved}")
    written = grafana_write(resolved["dashboard"])
    OUT.joinpath("manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT.joinpath("dashboard.json").write_text(json.dumps(resolved["dashboard"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "demo_only": True, "uid": UID, "url": written.get("url"), "selected_model": result["selected_kind"], "holdout_mae": result["holdout_metrics"]["mae"], "baseline_holdout_mae": result["baseline_holdout_mae"], "candidate_count": len(candidates["candidate_settings"]), "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
