#!/usr/bin/env python3
"""Verify adaptive preview/confirm execution for the exact correlation prompt."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "ask-o11y-correlation-preview-e2e.json"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
ORG_ID = os.environ.get("GRAFANA_ORG_ID", "1")
PROMPT = "分析一號機不同參數的相關係數，以熱力圖呈現並說明，可先 preview"
CONFIRMATION = "確認執行剛才的 Analysis Preview。請使用已選定的資料與欄位完成相關係數分析及熱力圖 dashboard；若需要實質更改計畫，請先重新 preview，不要自行加入其他分析方法。"


def headers() -> dict[str, str]:
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json", "X-Grafana-Org-Id": ORG_ID}


def request_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(GRAFANA_URL + path, data=None if body is None else json.dumps(body, ensure_ascii=False).encode(), headers=headers(), method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Grafana API {method} {path} failed HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana API {method} {path} failed: {exc}") from exc


def start_run(message: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "type": "chat"}
    if session_id:
        body["sessionId"] = session_id
    return request_json("/api/plugins/consensys-asko11y-app/resources/api/agent/run?" + urllib.parse.urlencode({"model": "large"}), "POST", body)


def poll_run(run_id: str, approve_renderer: bool) -> tuple[dict[str, Any], list[str]]:
    approvals = []
    deadline = time.time() + 900
    status: dict[str, Any] = {}
    while time.time() < deadline:
        status = request_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}")
        for event in status.get("events", []):
            if event.get("type") != "approval_request":
                continue
            approval_id = str((event.get("data") or {}).get("approvalId") or "")
            if not approval_id or approval_id in approvals:
                continue
            if not approve_renderer:
                raise RuntimeError("preview requested a write approval before user confirmation")
            request_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}", "POST", {"decision": "approved", "comment": "User confirmed this Analysis Preview; approve this dashboard write once.", "approvalScope": "once"})
            approvals.append(approval_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status, approvals
        time.sleep(3)
    raise RuntimeError(f"Ask O11y run timed out: {run_id}")


def starts(status: dict[str, Any]) -> list[str]:
    return [str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_start"]


def results(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result"]


def visible_text(status: dict[str, Any]) -> str:
    selected = [event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report"}]
    return json.dumps(selected, ensure_ascii=False)


def tool_arguments(status: dict[str, Any], name: str) -> list[dict[str, Any]]:
    output = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") != "tool_call_start" or data.get("name") != name:
            continue
        try:
            output.append(json.loads(data.get("arguments") or "{}"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid arguments for {name}: {exc}") from exc
    return output


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if key not in {"userId", "orgId"}}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def artifact_json(ref: str, name: str) -> dict[str, Any]:
    parts = ref.split("/")
    if len(parts) < 4 or not parts[2].startswith("run_"):
        raise RuntimeError(f"invalid artifact ref: {ref}")
    try:
        value = json.loads((ROOT / ".analysis-artifacts" / "runs" / parts[2] / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be an object")
    return value


def main() -> int:
    preview_start = start_run(PROMPT)
    preview_status, preview_approvals = poll_run(preview_start["runId"], approve_renderer=False)
    preview_tools = starts(preview_status)
    preview_results = results(preview_status)
    preview_text = visible_text(preview_status)
    allowed_preview = {"grafana-query_discover_datasets", "grafana-query_inspect_dataset", "data-query-planner_plan_query"}
    require(preview_status.get("status") == "completed", f"preview run failed: {preview_status.get('status')}")
    require(not [result for result in preview_results if result.get("isError")], "preview contains tool errors")
    require(bool(preview_tools) and set(preview_tools).issubset(allowed_preview), f"preview used execution tools: {preview_tools}")
    require("grafana-query_execute_planned_query" not in preview_tools and "engineering-analysis_analyze_correlation" not in preview_tools and not any(name.startswith("grafana-renderer_") for name in preview_tools), "preview executed data, analysis, or render")
    require(not preview_approvals, "preview unexpectedly requested approval")
    for marker in ["Preview", "相關", "熱力圖", "方法選擇理由", "Validation / Evaluation 計畫"]:
        require(marker.lower() in preview_text.lower(), f"preview missing required contract section: {marker}")
    require("pearson" in preview_text.lower() and any(marker in preview_text for marker in ["線性", "連續數值", "連續型"]), "preview did not explain why Pearson fits the requested data/objective")
    require(any(marker in preview_text for marker in ["缺失", "有效資料", "最少筆數"]) and any(marker in preview_text for marker in ["對稱", "對角線", "[-1, 1]", "[-1,1]", "樣本數"]), "preview lacks a concrete data-quality and output-integrity validation plan")
    require(any(marker in preview_text for marker in ["確認", "confirm"]), "preview did not ask for confirmation")

    session_id = str(preview_start.get("sessionId") or preview_status.get("sessionId") or "")
    require(bool(session_id), "preview did not return a reusable sessionId")
    execution_start = start_run(CONFIRMATION, session_id=session_id)
    execution_status, approvals = poll_run(execution_start["runId"], approve_renderer=True)
    execution_tools = starts(execution_status)
    execution_results = results(execution_status)
    execution_text = visible_text(execution_status)
    require(execution_status.get("status") == "completed", f"execution run failed: {execution_status.get('status')}")
    require(not [result for result in execution_results if result.get("isError")], "execution contains tool errors")
    for required in ["grafana-query_execute_planned_query", "engineering-analysis_analyze_correlation", "grafana-renderer_prepare_dashboard_write", "grafana-renderer_create_dashboard_from_analysis"]:
        require(required in execution_tools, f"execution missing required capability: {required}")
    require("engineering-analysis_analyze_profile" not in execution_tools, "correlation intent invoked unselected profile/EDA")
    require(not any(name.startswith("finance-analysis_") for name in execution_tools), "correlation intent incorrectly called Finance")
    require(not any(name.startswith("scientific-method_") or name.startswith("thermal-power-analysis_") for name in execution_tools), "legacy fixed-flow tool was called")
    engineering_args = tool_arguments(execution_status, "engineering-analysis_analyze_correlation")
    require(len(engineering_args) == 1 and len(engineering_args[0].get("fields", [])) >= 2, f"correlation fields were not selected: {engineering_args}")
    require(engineering_args[0].get("method") in {"pearson", "spearman"}, f"correlation method missing: {engineering_args}")
    prepare_args = tool_arguments(execution_status, "grafana-renderer_prepare_dashboard_write")
    create_args = tool_arguments(execution_status, "grafana-renderer_create_dashboard_from_analysis")
    require(len(prepare_args) == 1 and len(create_args) == 1 and create_args[0].get("analysis_result_ref") == prepare_args[0].get("analysis_result_ref") and isinstance(create_args[0].get("approval_ref"), str) and "approval_confirmed" not in create_args[0], "renderer did not use a server-issued approval capability")
    method = artifact_json(engineering_args[0]["frame_ref"], "method-engineering-correlation")
    validity = method.get("validity") or {}
    require(validity.get("input_rows") == 365 and validity.get("valid_rows") == 172 and validity.get("excluded_rows") == 193, f"correlation validity filtering mismatch: {validity}")
    require(len(approvals) >= 1, f"expected host approval for renderer write, got {len(approvals)}")
    require("http://" in execution_text and "因果" in execution_text, "final answer missing dashboard URL or correlation caution")

    out = {
        "ok": True,
        "prompt": PROMPT,
        "preview": {"runId": preview_start["runId"], "sessionId": session_id, "status": preview_status.get("status"), "tool_call_starts": preview_tools, "approvals": preview_approvals, "visible_text": preview_text},
        "confirmation": CONFIRMATION,
        "execution": {"runId": execution_start["runId"], "status": execution_status.get("status"), "tool_call_starts": execution_tools, "engineering_arguments": engineering_args, "validity": validity, "approval_count": len(approvals), "result_errors": [result for result in execution_results if result.get("isError")], "visible_text": execution_text},
        "validation": {"preview_no_query_analysis_render": True, "same_session_continuation": True, "selected_domain": "engineering", "selected_method": "pairwise_correlation", "finance_not_called": True, "legacy_fixed_flow_not_called": True, "server_verified_one_time_approval": True, "renderer_write_host_approved": True, "final_has_dashboard_and_caution": True},
        "raw": {"preview_status": sanitize(preview_status), "execution_status": sanitize(execution_status)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "preview_run": preview_start["runId"], "execution_run": execution_start["runId"], "preview_tools": preview_tools, "execution_tools": execution_tools, "engineering_arguments": engineering_args, "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
