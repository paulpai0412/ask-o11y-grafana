#!/usr/bin/env python3
"""Live guardrails for adaptive five-endpoint orchestration."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "orchestration-guardrails.json"


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


MCP_SHARED_TOKEN = required_env("MCP_SHARED_TOKEN")
MCP_HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {MCP_SHARED_TOKEN}", "X-Grafana-Org-Id": os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1"), "X-Grafana-User": os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y")}

SERVERS = {
    "ontology": required_env("ONTOLOGY_MCP_URL"),
    "data-query-planner": required_env("DATA_QUERY_PLANNER_MCP_URL"),
    "grafana-query": required_env("GRAFANA_QUERY_MCP_URL"),
    "sandbox-analysis": required_env("SANDBOX_ANALYSIS_MCP_URL"),
    "artifact-bridge": required_env("ARTIFACT_BRIDGE_MCP_URL"),
}


def rpc(server_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    request = urllib.request.Request(SERVERS[server_id], data=json.dumps(body).encode(), headers=MCP_HEADERS, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{server_id}.{method} failed") from exc


def unauthenticated_status(server_id: str, headers: dict[str, str]) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    request = urllib.request.Request(SERVERS[server_id], data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        return exc.code
    return 200


def mcp_call(server_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    envelope = rpc(server_id, "tools/call", {"name": tool_name, "arguments": arguments})
    try:
        return json.loads(envelope["result"]["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{server_id}.{tool_name} returned invalid payload") from exc


def tool_names(server_id: str) -> list[str]:
    envelope = rpc(server_id, "tools/list")
    try:
        return [str(tool["name"]) for tool in envelope["result"]["tools"]]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"{server_id}.tools/list returned invalid payload") from exc


def require(name: str, condition: bool, details: Any) -> dict[str, Any]:
    if not condition:
        raise RuntimeError(f"{name} failed: {details}")
    return {"name": name, "ok": True, "details": details}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load {path}") from exc


def main() -> int:
    checks = []
    unauthenticated = {server: unauthenticated_status(server, {}) for server in SERVERS}
    forged_identity = {server: unauthenticated_status(server, {"X-Grafana-Org-Id": "999", "X-Grafana-User": "attacker"}) for server in SERVERS}
    wrong_token = {server: unauthenticated_status(server, {"Authorization": "Bearer wrong", "X-Grafana-Org-Id": "1", "X-Grafana-User": "ask-o11y"}) for server in SERVERS}
    valid_token_forged_identity = {server: unauthenticated_status(server, {"Authorization": f"Bearer {MCP_SHARED_TOKEN}", "X-Grafana-Org-Id": "999", "X-Grafana-User": "attacker"}) for server in SERVERS}
    checks.append(require("live_mcp_requires_service_authentication", set(unauthenticated.values()) == {401} and set(forged_identity.values()) == {401} and set(wrong_token.values()) == {401} and set(valid_token_forged_identity.values()) == {401}, {"no_token": unauthenticated, "forged_identity_without_token": forged_identity, "wrong_token": wrong_token, "valid_token_forged_identity": valid_token_forged_identity}))
    live_tools = {server: tool_names(server) for server in SERVERS}
    expected = {"ontology": ["list_snapshots", "get_relation_paths", "resolve_concepts", "get_semantic_context", "classify_fields", "validate_analysis_contract"], "data-query-planner": ["plan_query", "search_wferp_schema", "plan_wferp_query", "validate_query"], "grafana-query": ["discover_datasets", "inspect_dataset", "execute_planned_query"], "sandbox-analysis": ["execute_python_analysis", "execute_python_preprocessing", "list_python_analyses", "inspect_python_analysis", "revise_python_analysis"], "artifact-bridge": ["resolve_dashboard_refs"]}
    checks.append(require("live_endpoint_toolsets", live_tools == expected, live_tools))
    unknown_property_results = {f"{server}.{tool}": mcp_call(server, tool, {"unexpected": True}) for server, tools in live_tools.items() for tool in tools}
    checks.append(require("all_live_tools_reject_unknown_properties", all(not result.get("ok") and "unsupported tool arguments" in result.get("error", "") for result in unknown_property_results.values()), unknown_property_results))

    ontology_raw = mcp_call("ontology", "get_semantic_context", {"dataset_id": "u1-operating-daily", "intent": "x", "sql": "select *"})
    checks.append(require("ontology_rejects_raw_query", not ontology_raw.get("ok") and "unsupported tool arguments" in ontology_raw.get("error", ""), ontology_raw))
    checks.append(require("ontology_has_no_mutation_or_graph_tools", not any(name in live_tools["ontology"] for name in ["mutate", "raw_query", "dump_graph"]), live_tools["ontology"]))
    planner_missing = mcp_call("data-query-planner", "plan_query", {})
    checks.append(require("planner_requires_metadata_ref", not planner_missing.get("ok") and "metadata_ref" in planner_missing.get("error", ""), planner_missing))
    planner_direct = mcp_call("data-query-planner", "plan_query", {"dataset_metadata_ref": "artifact://run_fake/dataset-metadata", "selected_fields": ["x"], "rawSql": "select *"})
    checks.append(require("planner_rejects_direct_query", not planner_direct.get("ok") and "unsupported tool arguments" in planner_direct.get("error", ""), planner_direct))
    query_missing = mcp_call("grafana-query", "execute_planned_query", {})
    checks.append(require("grafana_query_requires_plan_ref", not query_missing.get("ok"), query_missing))
    query_raw = mcp_call("grafana-query", "execute_planned_query", {"plan_ref": "artifact://run_fake/query-plan", "query": {}})
    checks.append(require("grafana_query_rejects_raw_query", not query_raw.get("ok") and "unsupported" in query_raw.get("error", ""), query_raw))
    sandbox_raw = mcp_call("sandbox-analysis", "execute_python_analysis", {"frame_ref": "artifact://run_fake/grafana-frame", "python_code": "df.describe()", "frames": []})
    checks.append(require("sandbox_rejects_raw_frame", not sandbox_raw.get("ok") and "unsupported" in sandbox_raw.get("error", ""), sandbox_raw))
    bridge_missing = mcp_call("artifact-bridge", "resolve_dashboard_refs", {})
    checks.append(require("artifact_bridge_requires_dashboard", not bridge_missing.get("ok", False) and "dashboard" in bridge_missing.get("error", ""), bridge_missing))
    bridge_tools = live_tools["artifact-bridge"]
    checks.append(require("artifact_bridge_has_no_grafana_write_tools", len(bridge_tools) == 1 and bridge_tools[0] == "resolve_dashboard_refs", bridge_tools))

    inspected = mcp_call("grafana-query", "inspect_dataset", {"dataset_id": "u1-operating-daily"})
    bounded_plan = mcp_call("data-query-planner", "plan_query", {"dataset_metadata_ref": inspected["dataset_metadata_ref"], "selected_fields": ["date", "heat_rate"], "minimum_rows": 1, "maximum_rows": 100})
    oversized = mcp_call("grafana-query", "execute_planned_query", {"plan_ref": bounded_plan["plan_ref"]})
    bounded_run = bounded_plan["plan_ref"].split("/", 3)[2]
    bounded_run_dir = ROOT / ".analysis-artifacts" / "runs" / bounded_run
    persisted_names = sorted(path.name for path in bounded_run_dir.glob("*.json"))
    checks.append(require("live_query_rejects_oversized_frame_before_persistence", not oversized.get("ok") and "row_count 365 exceeds maximum_rows 100" in oversized.get("evidence", {}).get("validation", {}).get("errors", []) and "response_persisted" in oversized.get("evidence", {}) and not oversized.get("evidence", {}).get("response_persisted") and "grafana-query-response.json" not in persisted_names and "grafana-frame.json" not in persisted_names, {"result": oversized, "persisted": persisted_names}))

    settings = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-workflow-tools-settings.json")
    servers = settings.get("mcpServers") or settings.get("jsonData", {}).get("mcpServers") or []
    settings_blob = json.dumps(settings, ensure_ascii=False).lower()
    checks.append(require("main_settings_five_endpoints", len(servers) == 5 and all(server in settings_blob for server in ["ontology", "data-query-planner", "grafana-query", "sandbox-analysis", "artifact-bridge"]), servers))
    settings_json = settings.get("jsonData", settings) if isinstance(settings, dict) else {}
    checks.append(require("ask_o11y_builtin_grafana_capabilities_enabled", bool(settings_json.get("useBuiltInMCP")), settings_json.get("useBuiltInMCP")))
    checks.append(require("legacy_fixed_tools_not_registered", all(marker not in settings_blob for marker in ["scientific-method", "thermal-power-analysis", "analyze_process_variation"]), settings_blob[:500]))
    out = {"ok": True, "checks": checks, "live_tools": live_tools}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "checks": [item["name"] for item in checks], "live_tools": live_tools}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
