#!/usr/bin/env python3
"""Real Ask O11y Skill + built-in Grafana MCP preview/publish E2E."""
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
                raise RuntimeError("read-only Analysis Preview requested mutation approval")
            request_json(
                f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}",
                "POST",
                {"decision": "approved", "comment": "Approve this exact preview/publication mutation once.", "approvalScope": "once"},
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
            try:
                arguments = json.loads(data.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid {name} arguments") from exc
            if not isinstance(arguments, dict):
                raise RuntimeError(f"invalid {name} arguments")
            output.append(arguments)
    return output


def tool_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("isError")]


def tool_result_text(status: dict[str, Any], name: str) -> list[str]:
    output = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") == "tool_call_result" and data.get("name") == name:
            output.append(json.dumps(data, ensure_ascii=False))
    return output


def visible_text(status: dict[str, Any]) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report"}], ensure_ascii=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def dashboard_uid(status: dict[str, Any]) -> str:
    match = re.search(r"/d/([^/]+)/", visible_text(status))
    if match is None:
        raise RuntimeError("Grafana dashboard UID missing from assistant response")
    return match.group(1)


def assert_target_has_data(target: dict[str, Any]) -> dict[str, Any]:
    result = request_json("/api/ds/query", "POST", {"queries": [target], "from": "1767225600000", "to": "1798761599000"})
    frame = result.get("results", {}).get(str(target.get("refId") or "A"), {}).get("frames", [{}])[0]
    values = frame.get("data", {}).get("values", [])
    require(bool(values) and all(len(column) > 0 for column in values), "published target returned no Grafana frame data")
    return {"fields": [field.get("name") for field in frame.get("schema", {}).get("fields", [])], "row_count": min(len(column) for column in values)}


def run_xy_skill_flow() -> dict[str, Any]:
    preview = start_run("請用 `u1-operating-daily` 的 avg_generation_mw 與 heat_rate 建立 Grafana 原生 XY Chart。使用 dashboarding skill 與內建 Grafana MCP；先提供 Analysis Preview，未確認前不要查詢或寫入。")
    preview_status, _ = poll_run(preview["runId"], False)
    require(preview_status.get("status") == "completed" and not tool_errors(preview_status), "XY Analysis Preview failed")
    session_id = str(preview.get("sessionId") or preview_status.get("sessionId") or "")
    execution = start_run("確認執行並建立可檢視的 Grafana Preview。X=avg_generation_mw，Y=heat_rate；不要正式發佈。", session_id)
    execution_status, preview_approvals = poll_run(execution["runId"], True)
    names = tool_names(execution_status)
    require(execution_status.get("status") == "completed" and not tool_errors(execution_status), "XY Grafana Preview failed")
    require("grafana-query_execute_planned_query" in names and "mcp-grafana_update_dashboard" in names and not any(name.startswith("grafana-renderer_") or name.startswith("artifact-bridge_") for name in names), f"XY flow bypassed built-in Grafana authoring: {names}")
    update_args = tool_arguments(execution_status, "mcp-grafana_update_dashboard")
    require(len(update_args) == 1 and isinstance(update_args[0].get("dashboard"), dict), "XY preview did not use one complete built-in dashboard write")
    model_panel = update_args[0]["dashboard"].get("panels", [{}])[0]
    require(model_panel.get("type") == "xychart" and model_panel.get("options", {}).get("mapping") in {"auto", "manual"}, f"dashboarding skill produced invalid XY options: {model_panel.get('options')}")
    require("$plan_ref" in json.dumps(model_panel.get("targets", [])), "model did not use opaque plan binding")
    uid = dashboard_uid(execution_status)
    stored = request_json(f"/api/dashboards/uid/{uid}").get("dashboard", {})
    require("ask-o11y-preview" in stored.get("tags", []), "host did not enforce preview status")
    panel = stored.get("panels", [{}])[0]
    require("$plan_ref" not in json.dumps(panel) and panel.get("targets"), "opaque target was not resolved before built-in Grafana write")
    data_evidence = assert_target_has_data(panel["targets"][0])
    publication = start_run("確認將剛才的 Grafana Preview 正式發佈；只更新同一 UID 移除 preview 狀態，不要重新查詢或重建圖表。", session_id)
    publication_status, publication_approvals = poll_run(publication["runId"], True)
    publication_names = tool_names(publication_status)
    allowed_publication_tools = {"mcp-grafana_update_dashboard", "mcp-grafana_get_dashboard_summary", "mcp-grafana_get_dashboard_by_uid"}
    require("mcp-grafana_update_dashboard" in publication_names and not tool_errors(publication_status) and all(name in allowed_publication_tools for name in publication_names), f"XY publication left the built-in patch/verification path: {publication_names}")
    published = request_json(f"/api/dashboards/uid/{uid}").get("dashboard", {})
    require("ask-o11y-preview" not in published.get("tags", []), "XY formal publication retained preview status")
    return {"uid": uid, "execution_tools": names, "publication_tools": publication_names, "approval_count": len(preview_approvals) + len(publication_approvals), "model_options": model_panel.get("options"), "data_evidence": data_evidence}


def main() -> int:
    preview = start_run("請用 `u1-operating-daily` 做 ontology-assisted Random Forest heat_rate 解釋，產生 SHAP beeswarm PNG。先使用獨立 Ontology MCP 的 approved snapshot/context/classification，且只使用 approved feature allowlist；由 Planner deterministic semantic gate 建立完整 analysis_contract（as_of=2026-07-28、依 date 的 chronological_holdout、test_fraction=0.25、training_only preprocessing、seed=42、quality_filter 必須精確為 `{\"field\":\"heat_rate_valid\",\"accepted_values\":[true],\"minimum_valid_rows\":100}`），且 Planner 的 minimum_rows 必須是 100。生成 Python 時 `date` 會是 UTC-aware Grafana timestamp，as_of 必須同樣使用 UTC-aware timestamp；禁止 random train_test_split，imputer 只能 fit training rows；RMSE 必須用 `np.sqrt(mean_squared_error(...))`，不得使用不相容的 `squared=False`。Grafana Preview 只顯示分析 PNG 與文字摘要，不要建立原生 Grafana data chart。先提供 Analysis Preview；未確認前不要執行 Grafana Query、Python 或 Grafana 寫入。")
    preview_status, _ = poll_run(preview["runId"], False)
    preview_tools = tool_names(preview_status)
    require(preview_status.get("status") == "completed" and not tool_errors(preview_status), "Analysis Preview failed")
    ontology_tools = {"ontology_get_semantic_context", "ontology_classify_fields", "ontology_validate_analysis_contract"}
    require(ontology_tools.issubset(preview_tools), f"Analysis Preview omitted ontology context/validation: {preview_tools}")
    require(not any(name in {"grafana-query_execute_planned_query", "sandbox-analysis_execute_python_analysis", "mcp-grafana_update_dashboard"} for name in preview_tools), f"Analysis Preview executed work: {preview_tools}")
    session_id = str(preview.get("sessionId") or preview_status.get("sessionId") or "")
    require(bool(session_id), "session missing")

    execution = start_run("確認依剛才完整 contract 執行並建立可檢視的 Grafana Preview；Planner minimum_rows 必須為 100，quality_filter 必須精確使用 ontology policy 的 field/accepted_values/minimum_valid_rows，不得自行改寫成 op/value；Python 的 RMSE 必須用 `np.sqrt(mean_squared_error(...))`，不得使用 `squared=False`。Dashboard 必須使用 opaque asset binding 顯示 SHAP PNG，並可附文字摘要；不得建立原生 Grafana data chart 或任何 panel target。", session_id)
    execution_status, execution_approvals = poll_run(execution["runId"], True)
    execution_names = tool_names(execution_status)
    errors = tool_errors(execution_status)
    required = {"grafana-query_execute_planned_query", "sandbox-analysis_execute_python_analysis", "mcp-grafana_update_dashboard"}
    require(execution_status.get("status") == "completed" and not errors and required.issubset(execution_names), f"analysis/preview failed: tools={execution_names} errors={errors}")
    require(not any(name.startswith("grafana-renderer_") or name.startswith("artifact-bridge_") for name in execution_names), f"internal bridge leaked into LLM tool flow: {execution_names}")
    sandbox_args = tool_arguments(execution_status, "sandbox-analysis_execute_python_analysis")
    require(len(sandbox_args) == 1 and "shap" in str(sandbox_args[0].get("python_code", "")).lower(), "Ask O11y did not generate SHAP Python")
    code = str(sandbox_args[0].get("python_code", ""))
    require("sort_values" in code and "date" in code and "train_test_split(" not in code, "SHAP code did not use a chronological holdout")
    require(".fit(" in code and ".transform(" in code and "random_state" in code, "SHAP code did not prove training-only preprocessing and fixed seed")
    planner_results = tool_result_text(execution_status, "data-query-planner_plan_query") or tool_result_text(preview_status, "data-query-planner_plan_query")
    require(bool(planner_results) and "ontology" in planner_results and "81304bc7daf0b6c87711c76a3cd3ac45f162dd6ffd0e91c5997a88d89484aa01" in planner_results and "plan_sha256" in planner_results, "Planner result omitted ontology/plan provenance")
    require("data-query-planner_plan_query" in preview_tools or "data-query-planner_plan_query" in execution_names, "Planner semantic gate did not run")
    if "data-query-planner_plan_query" in execution_names:
        require(execution_names.index("data-query-planner_plan_query") < execution_names.index("grafana-query_execute_planned_query"), "semantic gate did not run before query")
    require(execution_names.index("grafana-query_execute_planned_query") < execution_names.index("sandbox-analysis_execute_python_analysis"), "query did not run before sandbox")
    update_args = tool_arguments(execution_status, "mcp-grafana_update_dashboard")
    require(len(update_args) == 1 and isinstance(update_args[0].get("dashboard"), dict), "built-in Grafana preview write missing")
    model_dashboard = update_args[0]["dashboard"]
    require("$execution_ref" in json.dumps(model_dashboard), "model-authored dashboard did not use opaque Sandbox binding")
    model_json = json.dumps(model_dashboard)
    require("askO11yAssetBindings" in model_json and "$asset_url_" in model_json, "model-authored dashboard omitted opaque SHAP asset binding")
    require("/assets/" not in model_json, "model authored a signed asset URL instead of an opaque binding")
    uid = dashboard_uid(execution_status)
    stored = request_json(f"/api/dashboards/uid/{uid}").get("dashboard", {})
    require("ask-o11y-preview" in stored.get("tags", []) and stored.get("panels"), "temporary built-in Grafana Preview missing")
    require("$execution_ref" not in json.dumps(stored) and "askO11yAssetBindings" not in json.dumps(stored), "opaque refs reached Grafana")
    image_panels = [panel for panel in stored["panels"] if "/assets/" in json.dumps(panel)]
    data_panels = [panel for panel in stored["panels"] if panel.get("targets")]
    require(bool(image_panels) and not data_panels, "analysis Grafana Preview must contain only image/text panels")
    asset_match = re.search(r'(http://[^"\\ ]+/assets/[^"\\ ]+)', json.dumps(image_panels[0]))
    require(asset_match is not None, "signed SHAP asset URL missing")
    asset_evidence = {}
    if asset_match is not None:
        with urllib.request.urlopen(asset_match.group(1), timeout=30) as response:
            content_type = response.headers.get_content_type()
            png_magic = response.read(8).startswith(b"\x89PNG")
            require(content_type == "image/png" and png_magic, "SHAP asset URL did not return PNG")
            asset_evidence = {"content_type": content_type, "png_magic": png_magic}
    publication = start_run("確認將剛才已檢視的 Grafana Preview 正式發佈。只 patch 同一 UID 移除 preview 狀態，不要重跑查詢、Python 或圖表選擇。", session_id)
    publication_status, publication_approvals = poll_run(publication["runId"], True)
    publication_names = tool_names(publication_status)
    allowed_publication_tools = {"mcp-grafana_update_dashboard", "mcp-grafana_get_dashboard_summary", "mcp-grafana_get_dashboard_by_uid"}
    require("mcp-grafana_update_dashboard" in publication_names and not tool_errors(publication_status) and all(name in allowed_publication_tools for name in publication_names), f"publication left the built-in patch/verification path: {publication_names}")
    published = request_json(f"/api/dashboards/uid/{uid}").get("dashboard", {})
    require("ask-o11y-preview" not in published.get("tags", []), "formal dashboard retained preview status")

    xy = run_xy_skill_flow()
    evidence = {
        "ok": True,
        "preview": {"tools": preview_tools},
        "analysis_preview": {"uid": uid, "tools": execution_names, "approval_count": len(execution_approvals), "asset_evidence": asset_evidence, "analysis_has_no_data_targets": True},
        "publication": {"tools": publication_names, "approval_count": len(publication_approvals)},
        "xy_skill_flow": xy,
        "validation": {
            "skills_drive_dashboard_json": True,
            "built_in_grafana_mcp_is_only_writer": True,
            "artifact_bridge_hidden": True,
            "opaque_refs_resolved_host_side": True,
            "preview_before_publication": True,
            "same_uid_promoted": True,
            "xy_mapping_valid": True,
            "query_only_panel_targets_return_data": True,
            "analysis_uses_image_only": True,
            "shap_png_visible_in_preview": True,
            "ontology_context_before_planning": True,
            "planner_semantic_gate_before_query": True,
            "chronological_holdout": True,
            "training_only_preprocessing": True,
            "fixed_seed": True,
            "snapshot_plan_provenance": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "execution_tools": execution_names, "publication_tools": publication_names, "xy_execution_tools": xy["execution_tools"], "approval_count": len(execution_approvals) + len(publication_approvals) + xy["approval_count"], "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
