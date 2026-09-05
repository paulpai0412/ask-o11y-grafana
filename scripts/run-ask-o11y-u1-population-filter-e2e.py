#!/usr/bin/env python3
"""Fresh Ask O11y E2E for an observed population-filtered U1 regression."""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/u1_by_date.csv"
OUT = ROOT / ".scratch/u1-regression-e2e/ask-o11y-population-filter.json"
QUALITY_FEATURES = [
    *(f"熱值_kcalkg_保證_{position}" for position in "ABCD"),
    *(f"揮發份_pct_保證_{position}" for position in "ABCD"),
    *(f"總水份_pct_保證_{position}" for position in "ABCD"),
    *(f"灰份_pct_保證_{position}" for position in "ABCD"),
]
COAL_USAGE_FEATURES = [f"用煤量_保證_{position}" for position in "ABCD"]
TREATMENT_FEATURES = ["燃燒器角度_度", "火上風門開度_FCP", "火上風門開度_FCS", "火上風門開度_FCT", "火上風門開度_FCF"]
EXPECTED_FEATURES = set(QUALITY_FEATURES + COAL_USAGE_FEATURES + TREATMENT_FEATURES)
EXPECTED_FILTER = {"煤源_保證_A": "澳洲", "煤源_保證_B": "澳洲", "煤源_保證_C": "印尼", "煤源_保證_D": "印尼"}
EXPECTED_FEATURE_SETS = [
    {"id": "quality", "features": QUALITY_FEATURES, "controllable_fields": [], "context_fields": QUALITY_FEATURES},
    {"id": "quality_plus_usage", "features": QUALITY_FEATURES + COAL_USAGE_FEATURES, "controllable_fields": [], "context_fields": QUALITY_FEATURES + COAL_USAGE_FEATURES},
    {"id": "quality_plus_usage_control", "features": QUALITY_FEATURES + COAL_USAGE_FEATURES + TREATMENT_FEATURES, "controllable_fields": TREATMENT_FEATURES, "context_fields": QUALITY_FEATURES + COAL_USAGE_FEATURES},
]


def auth_headers(session_id: str | None = None, actor: str | None = None) -> dict[str, str]:
    token = os.environ.get("MCP_SHARED_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("MCP_SHARED_TOKEN is required")
    headers = {
        "Authorization": "Bearer " + token,
        "X-Grafana-Org-Id": os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1"),
        "X-Grafana-User": os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y"),
        "Content-Type": "text/csv",
    }
    if session_id:
        headers["X-Grafana-Session-Id"] = session_id
    if actor:
        headers["X-Grafana-Actor-User-Id"] = actor
    return headers


def grafana_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    auth = base64.b64encode(b"admin:admin").decode()
    request = urllib.request.Request(
        "http://127.0.0.1:3000" + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
        method=method,
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json", "X-Grafana-Org-Id": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}") from exc


def start_run(message: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "type": "chat"}
    if session_id:
        body["sessionId"] = session_id
    return grafana_json("/api/plugins/consensys-asko11y-app/resources/api/agent/run?model=large", "POST", body)


def poll(run_id: str, *, approve: bool) -> dict[str, Any]:
    deadline = time.time() + 30 * 60
    approved: set[str] = set()
    while time.time() < deadline:
        status = grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}")
        for event in status.get("events", []):
            if event.get("type") != "approval_request":
                continue
            approval_id = str((event.get("data") or {}).get("approvalId") or "")
            if approval_id in approved:
                continue
            if not approve:
                raise RuntimeError("preview requested protected execution")
            grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}", "POST", {"decision": "approved", "comment": "Approve the exact filtered regression execution once.", "approvalScope": "once"})
            approved.add(approval_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(3)
    raise RuntimeError("Ask O11y run timed out")


def names(status: dict[str, Any]) -> list[str]:
    return [str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_start"]


def errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("isError")]


def arguments(status: dict[str, Any], tool: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") == "tool_call_start" and data.get("name") == tool:
            try:
                value = json.loads(data.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid {tool} arguments") from exc
            if isinstance(value, dict):
                out.append(value)
    return out


def result_payloads(status: dict[str, Any], tool: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") != "tool_call_result" or data.get("name") != tool:
            continue
        try:
            value = json.loads(str(data.get("content") or "{}"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid {tool} result") from exc
        if isinstance(value, dict) and value.get("ok"):
            out.append(value)
    return out


def visible(status: dict[str, Any]) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report"}], ensure_ascii=False)


def upload(session_id: str, actor: str) -> str:
    raw = SOURCE.read_bytes()
    request = urllib.request.Request(
        "http://127.0.0.1:8772/uploads", data=raw, method="PUT",
        headers={**auth_headers(session_id, actor), "X-Upload-Session-Id": session_id, "X-Upload-Filename": SOURCE.name, "Content-Length": str(len(raw))},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return str(json.loads(response.read())["dataset_id"])
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("U1 upload failed") from exc


def main() -> int:
    opened = start_run("請建立一個新的分析 session，稍後我會提供一個 upload dataset id；本回合不要呼叫工具。")
    opened_status = poll(opened["runId"], approve=False)
    session_id = str(opened.get("sessionId") or opened_status.get("sessionId") or "")
    actor = str(opened_status.get("userId") or "")
    if not session_id or not actor:
        raise RuntimeError("Ask O11y session identity missing")
    dataset_id = upload(session_id, actor)

    preview = start_run(
        f"請使用 upload dataset `{dataset_id}` 做一個固定母體的 regression analysis。先 inspect 與 ontology classify，固定 population_filter 為四個 exact source fields：煤源_保證_A=澳洲、煤源_保證_B=澳洲、煤源_保證_C=印尼、煤源_保證_D=印尼。filter fields 只作母體篩選，不可成為 model features。目標是 熱耗率；features 必須且只能包含每個位置 A/B/C/D 的 exact physical_name 煤質欄位（熱值_kcalkg_保證_A～D、揮發份_pct_保證_A～D、總水份_pct_保證_A～D、灰份_pct_保證_A～D）、每個位置 A/B/C/D 的 exact physical_name 用煤量欄位（用煤量_保證_A～D），與這五個 exact process/treatment fields（燃燒器角度_度、火上風門開度_FCP/FCS/FCT/FCF），不可加入其它欄位。請建立一個 feature_sets array，且只能包含三組 nested sets：quality（16 個煤質欄位）、quality_plus_usage（16 個煤質加 4 個位置用煤量）、quality_plus_usage_control（再加 5 個 process/treatment fields）。用煤量欄位與五個 process/treatment fields 都是一般 predictor features，不是 sample weights；用煤量欄位只作 context，不作 controllable；第三組的五個 process/treatment fields 必須全部放入 controllable_fields；contract 與 template 不得設定或傳入 sample_weight。所有欄位只能使用 inspect/classify 回傳的 exact physical_name。這是 retrospective association，不是因果或控制建議。請先產生 Analysis Preview，確認前不要 query、Sandbox 或 Dashboard。",
        session_id,
    )
    preview_status = poll(preview["runId"], approve=False)
    preview_names = names(preview_status)
    if preview_status.get("status") != "completed" or errors(preview_status):
        raise RuntimeError(f"population-filter preview failed: {errors(preview_status)}")
    if "grafana-query_inspect_dataset" not in preview_names or "ontology_classify_fields" not in preview_names:
        raise RuntimeError(f"population-filter preview omitted metadata/ontology: {preview_names}")
    if {"grafana-query_execute_planned_query", "sandbox-analysis_execute_ml_contract", "mcp-grafana_update_dashboard"}.intersection(preview_names):
        raise RuntimeError(f"population-filter preview executed protected work: {preview_names}")

    execution = start_run(
        "確認依剛才的 fixed-population Analysis Preview 執行。請讓 Planner 建立一個完整 regression contract，保留 exact population_filter，filter fields 不得進 features；在同一 contract 內執行三個 feature_sets，三者共用同一 split、preprocessing、algorithms、seed、budget 與 baseline/holdout gate；執行一次 authorized Grafana Query 與一次 trusted execute_ml_contract。固定母體後的資料若 support 不足，請 fail closed；完成所有 artifacts 的 prepare/inspect，產生完整 evidence-bound story synthesis 與一個新的、包含本次 upload 尾碼的 Grafana Preview UID；Dashboard 要呈現 feature-set 比較、baseline/holdout gate、模型限制、非加權 predictor 語意與候選設定狀態，不得把關聯寫成因果控制建議。`synthesis` 必須嚴格只使用 report contract schema 的欄位：root 只可有 format/report_title/thesis/thesis_evidence/sections；section 只可有 section_id/title/purpose/collapsed/narrative_blocks/panels；panel 只可有 artifact_id/view_ids/view_narratives/headline/observation/interpretation/cross_chart_context/limitation/next_step/evidence/priority/preferred_width；不得加入 title、id、order、summary、width 等別名。",
        session_id,
    )
    execution_status = poll(execution["runId"], approve=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.with_name("ask-o11y-population-filter-debug.json").write_text(json.dumps({"preview": preview_status, "execution": execution_status}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    execution_names = names(execution_status)
    execution_errors = errors(execution_status)
    writer_succeeded = any(event.get("type") == "tool_call_result" and (event.get("data") or {}).get("name") == "mcp-grafana_update_dashboard" and not (event.get("data") or {}).get("isError") for event in execution_status.get("events", []))
    recoverable_story_errors = {"artifact-bridge_compose_ml_dashboard", "mcp-grafana_update_dashboard"}
    non_recoverable_errors = [item for item in execution_errors if item.get("name") not in recoverable_story_errors]
    if execution_status.get("status") != "completed" or non_recoverable_errors or (execution_errors and not writer_succeeded):
        raise RuntimeError(f"population-filter execution failed: {execution_errors}")
    if execution_names.count("grafana-query_execute_planned_query") != 1 or execution_names.count("sandbox-analysis_execute_ml_contract") != 1:
        raise RuntimeError(f"unexpected protected execution chain: {execution_names}")
    required_story_tools = {"artifact-bridge_prepare_ml_report", "artifact-bridge_inspect_report_artifacts", "artifact-bridge_compose_ml_dashboard", "mcp-grafana_update_dashboard"}
    if not required_story_tools.issubset(set(execution_names)):
        raise RuntimeError(f"complete report story was not composed: {execution_names}")
    if "sandbox-analysis_execute_python_analysis" in execution_names:
        raise RuntimeError(f"unsafe execution path: {execution_names}")

    plan_args = arguments(execution_status, "data-query-planner_plan_query")
    if not plan_args:
        raise RuntimeError("population-filter plan missing")
    contract = plan_args[-1].get("analysis_contract")
    if not isinstance(contract, dict):
        raise RuntimeError("population-filter contract missing")
    expected_filter = EXPECTED_FILTER
    if contract.get("population_filter") != expected_filter:
        raise RuntimeError(f"population filter drifted: {contract.get('population_filter')}")
    if set(contract.get("features") or []) & set(expected_filter):
        raise RuntimeError("population filter fields leaked into model features")
    if set(contract.get("features") or []) != EXPECTED_FEATURES:
        raise RuntimeError(f"feature set drifted: {contract.get('features')}")
    if set(contract.get("controllable_fields") or []) != set(TREATMENT_FEATURES):
        raise RuntimeError("treatment feature set drifted")
    feature_sets = contract.get("feature_sets")
    if not isinstance(feature_sets, list) or len(feature_sets) != len(EXPECTED_FEATURE_SETS):
        raise RuntimeError("feature-set story is missing")
    for expected, actual in zip(EXPECTED_FEATURE_SETS, feature_sets, strict=True):
        if not isinstance(actual, dict) or actual.get("id") != expected["id"] or set(actual.get("features") or []) != set(expected["features"]) or set(actual.get("controllable_fields") or []) != set(expected["controllable_fields"]) or set(actual.get("context_fields") or []) != set(expected["context_fields"]):
            raise RuntimeError(f"feature-set story drifted: {actual}")
    if not set(COAL_USAGE_FEATURES).issubset(set(contract.get("features") or [])):
        raise RuntimeError("position coal-usage features are missing")
    if contract.get("target") != "熱耗率" or contract.get("task_kind") != "regression":
        raise RuntimeError("regression target/task drifted")
    if "sample_weight" in json.dumps(contract.get("features") or [], ensure_ascii=False).lower():
        raise RuntimeError("coal usage/control predictors must not become sample weights")
    payloads = result_payloads(execution_status, "sandbox-analysis_execute_ml_contract")
    if len(payloads) != 1:
        raise RuntimeError("sandbox regression result missing")
    inline = payloads[0].get("output_summary", {}).get("inline_results", [])
    manifest = next((item.get("value") for item in inline if isinstance(item, dict) and item.get("display_name") == "ml-regression.json"), None)
    if not isinstance(manifest, dict):
        raise RuntimeError("filtered regression manifest missing")
    if manifest.get("data", {}).get("rows") != 76 or manifest.get("data", {}).get("population_filter") != expected_filter:
        raise RuntimeError(f"filtered population rows/filter missing: {manifest.get('data')}")
    if manifest.get("process", {}).get("sample_weight_fields") != [] or bool(manifest.get("weighting", {}).get("used")):
        raise RuntimeError("coal usage and control variables must remain ordinary predictors, not weights")
    dashboard_uids = re.findall(r"/d/([A-Za-z0-9_-]{1,80})/", visible(execution_status))
    if not dashboard_uids:
        raise RuntimeError("Grafana Preview URL missing from story")
    dashboard_uid = dashboard_uids[-1]
    dashboard = grafana_json(f"/api/dashboards/uid/{dashboard_uid}").get("dashboard", {})
    dashboard_text = json.dumps(dashboard, ensure_ascii=False)
    if "ask-o11y-preview" not in dashboard.get("tags", []) or "feature" not in dashboard_text.lower() or "blocked" not in dashboard_text.lower() and "candidate" not in dashboard_text.lower():
        raise RuntimeError("story dashboard omitted feature/gate evidence")

    evidence = {
        "ok": True,
        "session_id": session_id,
        "dataset_id": dataset_id,
        "preview_tools": preview_names,
        "execution_tools": execution_names,
        "recoverable_story_retries": len(execution_errors),
        "contract": contract,
        "manifest_summary": {"rows": manifest["data"]["rows"], "train_rows": manifest["data"]["train_rows"], "holdout_rows": manifest["data"]["holdout_rows"], "selected_model": manifest.get("selected_model"), "selected_metrics": manifest.get("selected_metrics"), "baseline_metrics": manifest.get("baseline_metrics"), "guards": manifest.get("guards")},
        "dashboard_uid": dashboard_uid,
        "validation": {"natural_language_population_filter": True, "exact_filter_fields": True, "filter_fields_not_features": True, "population_rows_verified": True, "nested_feature_set_story": True, "coal_usage_and_controls_are_unweighted_predictors": True, "no_generated_training_code": True, "dynamic_story_dashboard": True, "preview_dashboard": True},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "dataset_id": dataset_id, "population_rows": manifest["data"]["rows"], "execution_tools": execution_names, "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
