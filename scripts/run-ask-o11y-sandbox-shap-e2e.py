#!/usr/bin/env python3
"""Run a real Ask O11y preview/confirm/SHAP/Sandbox/Renderer E2E."""
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


def main() -> int:
    preview = start_run("請用 `u1-operating-daily` 最新授權 metadata（365 筆、heat_rate_valid 約 172 筆有效）做 Random Forest heat_rate 解釋與時間切分評估，產生 SHAP beeswarm Matplotlib PNG，並在確認後建立 Grafana dashboard。先提供 Analysis Preview；未確認前不要執行資料查詢、Python 或 Grafana 寫入。")
    preview_status, preview_approvals = poll_run(preview["runId"], False)
    preview_tools = tool_names(preview_status)
    require(preview_status.get("status") == "completed" and not tool_errors(preview_status), "preview failed")
    require(not any("execute" in name or name.startswith("sandbox-analysis_") or name.startswith("grafana-renderer_") for name in preview_tools), f"preview executed tools: {preview_tools}")
    session_id = str(preview.get("sessionId") or preview_status.get("sessionId") or "")
    require(bool(session_id), "preview session missing")

    execution = start_run("確認依剛才 Analysis Preview 執行，包括最新 metadata、validity filtering、選定工程特徵、Random Forest、時間切分評估、SHAP Matplotlib PNG，並在同一 run 呼叫 Renderer preview 及 write，讓 host approval UI 要求我核准。", session_id)
    execution_status, approvals = poll_run(execution["runId"], True)
    names = tool_names(execution_status)
    errors = tool_errors(execution_status)
    required = {
        "grafana-query_execute_planned_query",
        "sandbox-analysis_execute_python_analysis",
        "grafana-renderer_prepare_dashboard_write",
        "grafana-renderer_create_dashboard_from_artifacts",
    }
    require(execution_status.get("status") == "completed" and not errors, f"execution failed: {errors}")
    require(required.issubset(names), f"missing tools: {sorted(required - set(names))}")
    sandbox_args = tool_arguments(execution_status, "sandbox-analysis_execute_python_analysis")
    prepare_args = tool_arguments(execution_status, "grafana-renderer_prepare_dashboard_write")
    create_args = tool_arguments(execution_status, "grafana-renderer_create_dashboard_from_artifacts")
    require(len(sandbox_args) == len(prepare_args) == len(create_args) == 1, "expected one Sandbox/prepare/create call")
    code = str(sandbox_args[0].get("python_code") or "")
    require("import shap" in code and "RandomForestRegressor" in code and "emit(" in code, "Ask O11y did not generate SHAP Python")
    require(isinstance(sandbox_args[0].get("frame_ref"), str) and "frame" not in sandbox_args[0], "Sandbox did not receive only an opaque frame ref")
    require(isinstance(prepare_args[0].get("execution_ref"), str) and set(create_args[0]) == {"approval_ref"}, "Renderer did not use opaque refs")
    text = visible_text(execution_status)
    require("http://" in text and bool(approvals), "approved dashboard URL missing")
    evidence = {
        "ok": True,
        "preview": {"run_id": preview["runId"], "tools": preview_tools, "approval_count": len(preview_approvals), "text": visible_text(preview_status)},
        "execution": {"run_id": execution["runId"], "tools": names, "approval_count": len(approvals), "sandbox_arguments": sandbox_args[0], "renderer_prepare_arguments": prepare_args[0], "renderer_create_arguments": create_args[0], "text": text},
        "validation": {"ask_o11y_generated_shap_python": True, "opaque_refs_only": True, "host_approval": True, "dashboard_created": True},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "preview_tools": preview_tools, "execution_tools": names, "approval_count": len(approvals), "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
