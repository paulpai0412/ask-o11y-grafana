#!/usr/bin/env python3
"""Run a real Ask O11y preview/confirm/SHAP/Sandbox/Renderer E2E."""
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
OUT = ROOT / ".scratch" / "poc" / "ask-o11y-sandbox-shap-e2e.json"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")


def request_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        GRAFANA_URL + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "X-Grafana-Org-Id": "1"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Grafana API {method} {path} failed HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}") from exc


def start_run(message: str, session_id: str | None = None) -> dict[str, Any]:
    body = {"message": message, "type": "chat"}
    if session_id:
        body["sessionId"] = session_id
    return request_json("/api/plugins/consensys-asko11y-app/resources/api/agent/run?" + urllib.parse.urlencode({"model": "large"}), "POST", body)


def poll_run(run_id: str, approve: bool) -> tuple[dict[str, Any], list[str]]:
    approvals: list[str] = []
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        status = request_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}")
        for event in status.get("events", []):
            if event.get("type") != "approval_request":
                continue
            approval_id = str((event.get("data") or {}).get("approvalId") or "")
            if not approval_id or approval_id in approvals:
                continue
            if not approve:
                raise RuntimeError("preview requested execution/write approval")
            request_json(
                f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}",
                "POST",
                {"decision": "approved", "comment": "User confirmed the preview; approve this exact call once.", "approvalScope": "once"},
            )
            approvals.append(approval_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status, approvals
        time.sleep(3)
    raise RuntimeError(f"Ask O11y run timed out: {run_id}")


def tool_names(status: dict[str, Any]) -> list[str]:
    return [str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_start"]


def tool_arguments(status: dict[str, Any], name: str) -> list[dict[str, Any]]:
    output = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") == "tool_call_start" and data.get("name") == name:
            output.append(json.loads(data.get("arguments") or "{}"))
    return output


def tool_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("isError")]


def visible_text(status: dict[str, Any]) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report"}], ensure_ascii=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_native_flow() -> dict[str, Any]:
    preview = start_run("請用 `u1-operating-daily` 的 `date` 與 `heat_rate` 規劃一個 Grafana 原生時間序列圖，不需 Python 計算。先提供 Analysis Preview；未確認前不要查詢或寫入 Grafana。")
    preview_status, _ = poll_run(preview["runId"], False)
    require(preview_status.get("status") == "completed" and not tool_errors(preview_status), "native preview failed")
    session_id = str(preview.get("sessionId") or preview_status.get("sessionId") or "")
    execution = start_run("確認執行查詢。完成後只提供 Result Preview，不要寫入 Grafana；等我確認正式發佈。", session_id)
    execution_status, _ = poll_run(execution["runId"], False)
    execution_names = tool_names(execution_status)
    require(execution_status.get("status") == "completed" and not tool_errors(execution_status), "native execution failed")
    require("grafana-query_execute_planned_query" in execution_names and not any(name.startswith("sandbox-analysis_") or name.startswith("grafana-renderer_") for name in execution_names), f"native Result Preview used wrong capabilities: {execution_names}")
    require("Result Preview" in visible_text(execution_status), "native Result Preview missing")
    publication = start_run("確認正式發佈剛才的 Result Preview。請重用既有 plan_ref，不要重新查詢；使用目前安裝的原生 Grafana visualization capability，讓 host approval UI 要求我核准。", session_id)
    publication_status, approvals = poll_run(publication["runId"], True)
    publication_names = tool_names(publication_status)
    require(publication_status.get("status") == "completed" and not tool_errors(publication_status), "native publication failed")
    required = {"grafana-renderer_list_visualization_capabilities", "grafana-renderer_prepare_dashboard_write", "grafana-renderer_create_dashboard_from_artifacts"}
    require(required.issubset(publication_names), f"native publication missing tools: {sorted(required - set(publication_names))}")
    require(not any(name.startswith("grafana-query_") or name.startswith("sandbox-analysis_") for name in publication_names), f"native publication reran successful work: {publication_names}")
    prepare_args = tool_arguments(publication_status, "grafana-renderer_prepare_dashboard_write")
    require(len(prepare_args) == 1 and isinstance(prepare_args[0].get("plan_ref"), str) and isinstance(prepare_args[0].get("visualizations"), list) and "execution_ref" not in prepare_args[0], "native Renderer did not use plan_ref plus visualization specs")
    text = visible_text(publication_status)
    match = re.search(r"/d/([^/]+)/", text)
    require(match is not None and bool(approvals), "native published dashboard UID missing")
    if match is None:
        raise RuntimeError("native published dashboard UID missing")
    dashboard_uid = match.group(1)
    dashboard = request_json(f"/api/dashboards/uid/{dashboard_uid}")
    panels = dashboard.get("dashboard", {}).get("panels", [])
    require(bool(panels) and all(panel.get("type") != "text" and panel.get("targets") for panel in panels), "published native dashboard has text/no-target panels")
    return {"preview_run_id": preview["runId"], "execution_run_id": execution["runId"], "publication_run_id": publication["runId"], "execution_tools": execution_names, "publication_tools": publication_names, "approval_count": len(approvals), "dashboard_uid": dashboard_uid, "panel_types": [panel.get("type") for panel in panels], "renderer_prepare_arguments": prepare_args[0]}


def main() -> int:
    preview = start_run("請用 `u1-operating-daily` 最新授權 metadata（365 筆、heat_rate_valid 約 172 筆有效）做 Random Forest heat_rate 解釋與時間切分評估，產生 SHAP beeswarm Matplotlib PNG，並在確認後建立 Grafana dashboard。先提供 Analysis Preview；未確認前不要執行資料查詢、Python 或 Grafana 寫入。")
    preview_status, preview_approvals = poll_run(preview["runId"], False)
    preview_tools = tool_names(preview_status)
    require(preview_status.get("status") == "completed" and not tool_errors(preview_status), "preview failed")
    require(not any("execute" in name or name.startswith("sandbox-analysis_") or name.startswith("grafana-renderer_") for name in preview_tools), f"preview executed tools: {preview_tools}")
    session_id = str(preview.get("sessionId") or preview_status.get("sessionId") or "")
    require(bool(session_id), "preview session missing")

    execution = start_run("確認依剛才 Analysis Preview 執行，包括最新 metadata、validity filtering、選定工程特徵、Random Forest、時間切分評估與 SHAP Matplotlib PNG。執行完成後只提供 Result Preview，不要寫入 Grafana；等我另行確認正式發佈。", session_id)
    execution_status, execution_approvals = poll_run(execution["runId"], False)
    execution_names = tool_names(execution_status)
    execution_errors = tool_errors(execution_status)
    required_execution = {"grafana-query_execute_planned_query", "sandbox-analysis_execute_python_analysis"}
    require(execution_status.get("status") == "completed" and not execution_errors, f"execution failed: {execution_errors}")
    require(required_execution.issubset(execution_names), f"missing execution tools: {sorted(required_execution - set(execution_names))}")
    require(not any(name.startswith("grafana-renderer_") for name in execution_names), f"Result Preview run wrote/prepared Grafana: {execution_names}")
    sandbox_args = tool_arguments(execution_status, "sandbox-analysis_execute_python_analysis")
    require(len(sandbox_args) == 1, "expected one Sandbox execution")
    code = str(sandbox_args[0].get("python_code") or "")
    require("shap" in code.lower() and "randomforest" in code.lower(), "Ask O11y did not generate Random Forest SHAP Python")
    require(isinstance(sandbox_args[0].get("frame_ref"), str) and "frame" not in sandbox_args[0], "Sandbox did not receive only an opaque frame ref")
    execution_text = visible_text(execution_status)
    require("Result Preview" in execution_text and "正式" in execution_text, "post-execution Result Preview/publication prompt missing")

    publication = start_run("確認正式發佈剛才的 Result Preview。請重用既有成功 refs，不要重新查詢或重新執行 Python；綁定精確輸出後讓 host approval UI 要求我核准。", session_id)
    publication_status, publication_approvals = poll_run(publication["runId"], True)
    publication_names = tool_names(publication_status)
    publication_errors = tool_errors(publication_status)
    required_publication = {"grafana-renderer_prepare_dashboard_write", "grafana-renderer_create_dashboard_from_artifacts"}
    require(publication_status.get("status") == "completed" and not publication_errors, f"publication failed: {publication_errors}")
    require(required_publication.issubset(publication_names), f"missing publication tools: {sorted(required_publication - set(publication_names))}")
    require(not any(name.startswith("grafana-query_") or name.startswith("sandbox-analysis_") for name in publication_names), f"publication reran successful work: {publication_names}")
    prepare_args = tool_arguments(publication_status, "grafana-renderer_prepare_dashboard_write")
    create_args = tool_arguments(publication_status, "grafana-renderer_create_dashboard_from_artifacts")
    require(len(prepare_args) == len(create_args) == 1, "expected one prepare/create call")
    require(isinstance(prepare_args[0].get("execution_ref"), str) and set(create_args[0]) == {"approval_ref"}, "Renderer did not use opaque refs")
    publication_text = visible_text(publication_status)
    require("http://" in publication_text and bool(publication_approvals), "approved dashboard URL missing")
    native = run_native_flow()
    evidence = {
        "ok": True,
        "preview": {"run_id": preview["runId"], "tools": preview_tools, "approval_count": len(preview_approvals), "text": visible_text(preview_status)},
        "execution": {"run_id": execution["runId"], "tools": execution_names, "approval_count": len(execution_approvals), "sandbox_arguments": sandbox_args[0], "text": execution_text},
        "publication": {"run_id": publication["runId"], "tools": publication_names, "approval_count": len(publication_approvals), "renderer_prepare_arguments": prepare_args[0], "renderer_create_arguments": create_args[0], "text": publication_text},
        "native": native,
        "validation": {"ask_o11y_generated_shap_python": True, "result_preview_before_write": True, "same_session_refs_reused": True, "opaque_refs_only": True, "host_approval": True, "artifact_dashboard_created": True, "native_dashboard_has_query_targets": True},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "preview_tools": preview_tools, "execution_tools": execution_names, "publication_tools": publication_names, "native_publication_tools": native["publication_tools"], "approval_count": len(publication_approvals) + native["approval_count"], "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
