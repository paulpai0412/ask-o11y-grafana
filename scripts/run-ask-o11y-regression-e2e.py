#!/usr/bin/env python3
"""Fresh natural-language Ask O11y regression + constrained-search E2E."""
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
OUT = ROOT / ".scratch/u1-regression-e2e/ask-o11y-runtime.json"
GRAFANA = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")


def grafana_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    auth = base64.b64encode(b"admin:admin").decode()
    request = urllib.request.Request(GRAFANA + path, data=None if body is None else json.dumps(body, ensure_ascii=False).encode(), method=method, headers={"Authorization": "Basic " + auth, "Content-Type": "application/json", "X-Grafana-Org-Id": "1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}") from exc


def start_run(message: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "type": "chat"}
    if session_id:
        body["sessionId"] = session_id
    return grafana_json("/api/plugins/consensys-asko11y-app/resources/api/agent/run?" + urllib.parse.urlencode({"model": "large"}), "POST", body)


def poll_run(run_id: str, *, approve: bool) -> tuple[dict[str, Any], list[str]]:
    approvals: list[str] = []
    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        status = grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}")
        for event in status.get("events", []):
            if event.get("type") != "approval_request":
                continue
            approval_id = str((event.get("data") or {}).get("approvalId") or "")
            if not approval_id or approval_id in approvals:
                continue
            if not approve:
                raise RuntimeError("preview requested mutation approval")
            grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}", "POST", {"decision": "approved", "comment": "Approve this exact preview dashboard mutation once.", "approvalScope": "once"})
            approvals.append(approval_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status, approvals
        time.sleep(3)
    raise RuntimeError(f"Ask O11y run timed out: {run_id}")


def upload(session_id: str, actor_user_id: str) -> str:
    raw = SOURCE.read_bytes()
    token = os.environ.get("MCP_SHARED_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("MCP_SHARED_TOKEN is required")
    request = urllib.request.Request(
        "http://127.0.0.1:8772/uploads", data=raw, method="PUT",
        headers={
            "Authorization": "Bearer " + token,
            "X-Grafana-Org-Id": os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1"),
            "X-Grafana-User": os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y"),
            "X-Grafana-Actor-User-Id": actor_user_id,
            "X-Upload-Session-Id": session_id,
            "X-Upload-Filename": SOURCE.name,
            "Content-Type": "text/csv",
            "Content-Length": str(len(raw)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return str(json.loads(response.read())["dataset_id"])
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("session-bound U1 upload failed") from exc


def tool_names(status: dict[str, Any]) -> list[str]:
    return [str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_start"]


def tool_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("isError")]


def tool_arguments(status: dict[str, Any], name: str) -> list[dict[str, Any]]:
    values = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") == "tool_call_start" and data.get("name") == name:
            try:
                value = json.loads(data.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid {name} arguments") from exc
            if isinstance(value, dict):
                values.append(value)
    return values


def result_text(status: dict[str, Any], name: str) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("name") == name], ensure_ascii=False)


def visible_text(status: dict[str, Any]) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report"}], ensure_ascii=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    opened = start_run("我要對即將上傳的表格做連續目標迴歸與受限候選設定分析。請先建立分析工作階段並等待我提供 upload dataset id；此回合不要查資料或呼叫工具。")
    opened_status, _ = poll_run(opened["runId"], approve=False)
    session_id = str(opened.get("sessionId") or opened_status.get("sessionId") or "")
    require(bool(session_id) and opened_status.get("status") == "completed", "Ask O11y session creation failed")
    dataset_id = upload(session_id, str(opened_status.get("userId") or ""))

    preview = start_run(
        f"我已上傳資料集 `{dataset_id}`。請分析哪些可操作參數可能有助於降低連續目標『熱耗率』，比較適合資料特性的迴歸模型，並且只有在模型同時勝過簡單基準與未見資料驗證時，才在已觀察支持範圍內提出帶支持度和不確定性的候選設定。針對這份資料，我明確同意：若只有日期或熱耗率缺失／無法解析，僅排除這些 target/split 無效列（預期 139 筆變 138 筆）；不得填補、改寫或因 feature-only 缺失而刪除任何其他列。請把這個精確政策與列數證據放進 Analysis Preview，仍先等我確認。請自行從欄位語意區分可操作參數、背景條件、日期、常數、目標組成或代理欄位；後續 contract 只能逐字使用 inspect/classify 回傳的 physical_name，禁止翻譯、拼接或改寫欄位名稱；ontology validation 前必須提供 algorithms，並確保 features 等於 controllable_fields 加上 context_fields 但扣除 split field；使用者明確指定的 numeric、非全缺、非常數連續 target 以 validator 結果為準，不要只因 upload heuristic 的 identifier 或 target_candidate 標籤停止；禁止外推，也不要宣稱因果最佳。先提供 Analysis Preview，確認前不要執行 datasource query、Sandbox 或 Dashboard 寫入。",
        session_id,
    )
    preview_status, _ = poll_run(preview["runId"], approve=False)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.with_name("ask-o11y-preview-debug.json").write_text(json.dumps({"opened": opened_status, "preview": preview_status, "dataset_id": dataset_id}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preview_names = tool_names(preview_status)
    preview_errors = tool_errors(preview_status)
    require(preview_status.get("status") == "completed" and not preview_errors, f"regression preview failed: {preview_errors}")
    require("grafana-query_inspect_dataset" in preview_names and "ontology_classify_fields" in preview_names, f"preview omitted inspect/ontology classification: {preview_names}")
    forbidden_preview = {"grafana-query_execute_planned_query", "sandbox-analysis_execute_ml_contract", "sandbox-analysis_execute_python_analysis", "mcp-grafana_update_dashboard"}
    require(not forbidden_preview.intersection(preview_names), f"preview executed protected work: {preview_names}")
    execution = start_run("確認依剛才核准的 Analysis Preview 執行。所有 selected_fields 與 contract fields 必須逐字沿用已 inspect/classify 的 physical_name，不得翻譯或改名；contract 必須含 algorithms、明確 `optimization: {{direction: minimize}}`，並維持 features 等於 controllable_fields 加 context_fields 扣除 split field。不得修改 deterministic split、training-only preprocessing、holdout、baseline gate、observed-support search 規則。請完成整份 evidence-bound report synthesis 並建立可檢視但不要正式發佈的 Grafana Preview；Dashboard UID 必須使用本次 upload dataset id 尾碼產生新的唯一值，不得重用既有 heat-rate-regression-preview UID。", session_id)
    execution_status, approvals = poll_run(execution["runId"], approve=True)
    OUT.with_name("ask-o11y-execution-debug.json").write_text(json.dumps({"execution": execution_status}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    execution_names = tool_names(execution_status)
    errors = tool_errors(execution_status)
    require(execution_status.get("status") == "completed" and not errors, f"regression execution failed: {errors}")
    required = {"grafana-query_execute_planned_query", "sandbox-analysis_execute_ml_contract", "mcp-grafana_update_dashboard"}
    require(required.issubset(execution_names), f"regression capability chain incomplete: {execution_names}")
    require("sandbox-analysis_execute_python_analysis" not in execution_names, "Ask O11y bypassed the trusted regression template")
    planner_args = tool_arguments(execution_status, "data-query-planner_plan_query")
    require(bool(planner_args), "execution did not create a regression contract")
    contract = planner_args[-1].get("analysis_contract") or {}
    require(contract.get("task_kind") == "regression" and contract.get("target") == "熱耗率", f"Ask O11y did not select regression: {contract}")
    require(contract.get("split", {}).get("kind") in {"chronological_holdout", "grouped_holdout"}, "Ask O11y selected an unsafe split")
    require(bool(contract.get("controllable_fields")) and bool(contract.get("context_fields")) and bool(contract.get("forbidden_fields")), "Ask O11y omitted governed field roles")
    require(bool((contract.get("constrained_search") or {}).get("enabled")), "Ask O11y omitted constrained search capability")
    sandbox_args = tool_arguments(execution_status, "sandbox-analysis_execute_ml_contract")
    require(len(sandbox_args) == 1 and set(sandbox_args[0]) <= {"frame_ref", "contract_ref", "seed"}, f"structured executor arguments are invalid: {sandbox_args}")
    sandbox_result = result_text(execution_status, "sandbox-analysis_execute_ml_contract")
    constrained_statuses = {status for status in ("blocked_by_baseline", "insufficient_support", "candidate_settings") if status in sandbox_result}
    require(bool(constrained_statuses) and '"causal_claim": true' not in sandbox_result, "constrained search omitted a safe deterministic outcome")
    text = visible_text(execution_status)
    dashboard_uids = re.findall(r"/d/([A-Za-z0-9_-]{1,80})/", text)
    require(bool(dashboard_uids) and "候選設定" in text and "constrained search" in text, "final report omitted dashboard URL or constrained-search status")
    uid = dashboard_uids[-1] if dashboard_uids else ""
    dashboard = grafana_json(f"/api/dashboards/uid/{uid}").get("dashboard", {})
    serialized = json.dumps(dashboard, ensure_ascii=False)
    require("ask-o11y-preview" in dashboard.get("tags", []) and any(status in serialized for status in constrained_statuses) and "候選" in serialized and "最佳設定" not in serialized, "stored preview omitted deterministic constrained-search status or used unsafe candidate language")

    evidence = {
        "ok": True,
        "session_id": session_id,
        "dataset_id": dataset_id,
        "preview_tools": preview_names,
        "execution_tools": execution_names,
        "approval_count": len(approvals),
        "contract": contract,
        "uid": uid,
        "validation": {
            "natural_language_selected_regression": True,
            "preview_before_execution": True,
            "trusted_execute_ml_contract": True,
            "no_generated_training_code": True,
            "safe_constrained_search_outcome": sorted(constrained_statuses),
            "dynamic_evidence_report": True,
            "grafana_preview": True,
        },
    }
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "uid": uid, "preview_tools": preview_names, "execution_tools": execution_names, "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
