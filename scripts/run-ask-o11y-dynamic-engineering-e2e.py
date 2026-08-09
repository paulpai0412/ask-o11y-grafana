#!/usr/bin/env python3
"""Run fresh preview/confirm Ask O11y E2E for time-series and supervised intents."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "ask-o11y-dynamic-engineering-e2e.json"


def load_base() -> Any:
    path = ROOT / "scripts" / "run-ask-o11y-correlation-preview-e2e.py"
    spec = importlib.util.spec_from_file_location("ask_o11y_e2e_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Ask O11y E2E helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ask_o11y_e2e_base"] = module
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def artifact_json(ref: str, name: str) -> dict[str, Any]:
    parts = ref.split("/")
    if len(parts) < 4 or not parts[2].startswith("run_"):
        raise RuntimeError(f"invalid artifact ref: {ref}")
    try:
        value = json.loads((ROOT / ".analysis-artifacts" / "runs" / parts[2] / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {name} for {ref}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} artifact must be an object")
    return value


def run_case(base: Any, case: dict[str, Any]) -> dict[str, Any]:
    preview_start = base.start_run(case["prompt"])
    preview_status, preview_approvals = base.poll_run(preview_start["runId"], approve_renderer=False)
    preview_tools = base.starts(preview_status)
    preview_results = base.results(preview_status)
    preview_text = base.visible_text(preview_status)
    allowed_preview = {"grafana-query_discover_datasets", "grafana-query_inspect_dataset", "data-query-planner_plan_query"}
    require(preview_status.get("status") == "completed", f"{case['id']} preview failed")
    require(not [result for result in preview_results if result.get("isError")], f"{case['id']} preview contained a tool error/retry")
    require(bool(preview_tools) and set(preview_tools).issubset(allowed_preview), f"{case['id']} preview used execution tools: {preview_tools}")
    require(not preview_approvals, f"{case['id']} preview requested write approval")
    require("preview" in preview_text.lower() and any(marker in preview_text for marker in ["確認", "confirm"]), f"{case['id']} preview/confirmation text missing")
    for marker in case["preview_markers"]:
        choices = marker if isinstance(marker, tuple) else (marker,)
        require(any(choice.lower() in preview_text.lower() for choice in choices), f"{case['id']} preview missing one of {choices}")

    session_id = str(preview_start.get("sessionId") or preview_status.get("sessionId") or "")
    require(bool(session_id), f"{case['id']} preview sessionId missing")
    execution_start = base.start_run(case["confirmation"], session_id=session_id)
    execution_status, approvals = base.poll_run(execution_start["runId"], approve_renderer=True)
    execution_tools = base.starts(execution_status)
    execution_results = base.results(execution_status)
    execution_text = base.visible_text(execution_status)
    require(execution_status.get("status") == "completed", f"{case['id']} execution failed")
    require(not [result for result in execution_results if result.get("isError")], f"{case['id']} execution tool error")
    for required in ["grafana-query_execute_planned_query", case["tool"], "grafana-renderer_prepare_dashboard_write", "grafana-renderer_create_dashboard_from_analysis"]:
        require(required in execution_tools, f"{case['id']} missing {required}: {execution_tools}")
    other_engineering = set(case["forbidden_tools"])
    require(not other_engineering.intersection(execution_tools), f"{case['id']} invoked unselected methods: {other_engineering.intersection(execution_tools)}")
    require(not any(name.startswith("finance-analysis_") or name.startswith("scientific-method_") or name.startswith("thermal-power-analysis_") for name in execution_tools), f"{case['id']} invoked wrong/legacy domain")
    arguments = base.tool_arguments(execution_status, case["tool"])
    require(len(arguments) == 1, f"{case['id']} expected one selected analysis call: {arguments}")
    case["argument_check"](arguments[0])
    method_name = "method-engineering-timeseries" if case["id"] == "anomaly_forecast" else "method-engineering-predictive"
    method = artifact_json(arguments[0]["frame_ref"], method_name)
    validity = method.get("validity") or {}
    require(validity.get("input_rows") == 365 and validity.get("valid_rows") == 172 and validity.get("excluded_rows") == 193, f"{case['id']} validity filtering mismatch: {validity}")
    if case["id"] == "anomaly_forecast":
        history = method.get("results", {}).get("forecast", {}).get("history", [])
        require(history and all(str(row.get("time", "")).startswith("2026-") for row in history), f"time-series contains non-2026 timestamps: {history[:2]}")
    else:
        predictions = method.get("predictions") or []
        require(isinstance(predictions, list) and bool(predictions), "supervised predictions are missing")
        try:
            actuals = [float(row.get("actual", 0)) for row in predictions if isinstance(row, dict)]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"supervised predictions are invalid: {exc}") from exc
        require(len(actuals) == len(predictions) and all(value > 0 for value in actuals), "supervised evaluation contains invalid zero heat-rate rows")
    prepare_args = base.tool_arguments(execution_status, "grafana-renderer_prepare_dashboard_write")
    renderer_args = base.tool_arguments(execution_status, "grafana-renderer_create_dashboard_from_analysis")
    require(len(prepare_args) == 1 and len(renderer_args) == 1 and renderer_args[0].get("analysis_result_ref") == prepare_args[0].get("analysis_result_ref") and isinstance(renderer_args[0].get("approval_ref"), str) and "approval_confirmed" not in renderer_args[0], f"{case['id']} server-issued renderer approval capability missing")
    require(len(approvals) >= 1, f"{case['id']} expected host approval for renderer write, got {len(approvals)}")
    require("http://" in execution_text, f"{case['id']} final dashboard URL missing")
    for marker in case["final_markers"]:
        choices = marker if isinstance(marker, tuple) else (marker,)
        require(any(choice.lower() in execution_text.lower() for choice in choices), f"{case['id']} final explanation missing one of {choices}")
    return {"id": case["id"], "prompt": case["prompt"], "preview": {"runId": preview_start["runId"], "sessionId": session_id, "tools": preview_tools, "text": preview_text, "errors": []}, "confirmation": case["confirmation"], "execution": {"runId": execution_start["runId"], "tools": execution_tools, "analysis_arguments": arguments[0], "validity": validity, "renderer_arguments": renderer_args[0], "approval_count": len(approvals), "text": execution_text, "errors": []}, "raw": {"preview_status": base.sanitize(preview_status), "execution_status": base.sanitize(execution_status)}}


def check_timeseries(arguments: dict[str, Any]) -> None:
    require(arguments.get("target") == "heat_rate" and arguments.get("time_field") == "date", f"time-series target/time mismatch: {arguments}")
    require(set(arguments.get("operations") or []) == {"anomaly", "forecast"}, f"time-series selected operations mismatch: {arguments}")
    require("heat_rate" in (arguments.get("anomaly_features") or []), f"time-series anomaly fields mismatch: {arguments}")


def check_supervised(arguments: dict[str, Any]) -> None:
    require(arguments.get("task") == "regression" and arguments.get("target") == "heat_rate", f"supervised target/task mismatch: {arguments}")
    require(arguments.get("model_family") in {"linear", "random_forest"}, f"supervised model missing: {arguments}")
    require(isinstance(arguments.get("features"), list) and len(arguments["features"]) >= 2 and "heat_rate" not in arguments["features"], f"supervised features invalid: {arguments}")


def main() -> int:
    base = load_base()
    cases = [
        {"id": "anomaly_forecast", "prompt": "請分析一號機熱耗率時間序列的異常，並預測未來 14 天，以時間序列圖呈現；先 preview，未經確認不要查詢或建立 dashboard。", "confirmation": "確認執行剛才的 Analysis Preview。只執行已預覽的熱耗率 anomaly 與 forecast，使用 preview 中已授權的 dataset id `u1-operating-daily` 與真實 Grafana 資料，並建立預覽中的時間序列 dashboard；若要實質改變方法或欄位，先重新 preview。", "preview_markers": ["異常", "預測", "14"], "tool": "engineering-analysis_analyze_timeseries", "forbidden_tools": ["engineering-analysis_analyze_profile", "engineering-analysis_analyze_correlation", "engineering-analysis_analyze_predictive", "engineering-analysis_analyze_patterns"], "argument_check": check_timeseries, "final_markers": ["異常", ("預測", "forecast", "未來")]},
        {"id": "supervised_regression", "prompt": "請用一號機的其他工程參數做 linear regression 預測熱耗率，執行 held-out evaluation 和 feature importance，並以實際值對預測值散點圖及重要度長條圖呈現；先 preview。", "confirmation": "確認執行剛才的 Analysis Preview。只執行已預覽的 supervised regression、held-out evaluation 與 feature importance，使用 preview 中已授權的 dataset id `u1-operating-daily` 與真實 Grafana 資料，並建立散點圖和重要度 dashboard；若要實質改變方法或欄位，先重新 preview。", "preview_markers": ["regression", ("held-out", "holdout", "驗證集"), ("importance", "重要度", "特徵重要性")], "tool": "engineering-analysis_analyze_predictive", "forbidden_tools": ["engineering-analysis_analyze_profile", "engineering-analysis_analyze_correlation", "engineering-analysis_analyze_patterns", "engineering-analysis_analyze_timeseries"], "argument_check": check_supervised, "final_markers": ["R²", "重要度"]},
    ]
    results = [run_case(base, case) for case in cases]
    require(results[0]["preview"]["text"] != results[1]["preview"]["text"], "different intents produced identical previews")
    require(results[0]["execution"]["tools"] != results[1]["execution"]["tools"], "different intents produced identical tool traces")
    evidence = {"ok": True, "cases": results, "validation": {"preview_before_execution": True, "same_session_confirmation": True, "real_grafana_query": True, "selected_methods_only": True, "dynamic_tool_traces": True, "approval_gated_renderer": True, "finance_real_e2e": False}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "cases": [{"id": item["id"], "preview_run": item["preview"]["runId"], "execution_run": item["execution"]["runId"], "tools": item["execution"]["tools"], "arguments": item["execution"]["analysis_arguments"]} for item in results], "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
