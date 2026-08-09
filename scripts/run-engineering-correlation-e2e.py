#!/usr/bin/env python3
"""Run the real Grafana-backed Engineering correlation vertical.

This proves the deterministic domain/rendering path only. Adaptive Ask O11y
preview/confirmation is verified separately and must not be inferred here.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow_node import parse_artifact_ref  # noqa: E402
OUT = ROOT / ".scratch" / "poc" / "engineering-correlation-e2e.json"
ARTIFACT_ROOT = Path(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
MCP_SHARED_TOKEN = os.environ.get("MCP_SHARED_TOKEN", "")
MCP_URLS = {
    "planner": os.environ.get("DATA_QUERY_PLANNER_MCP_URL", "http://127.0.0.1:8768/mcp"),
    "query": os.environ.get("GRAFANA_QUERY_MCP_URL", "http://127.0.0.1:8772/mcp"),
    "engineering": os.environ.get("ENGINEERING_ANALYSIS_MCP_URL", "http://127.0.0.1:8775/mcp"),
    "renderer": os.environ.get("GRAFANA_RENDERER_MCP_URL", "http://127.0.0.1:8773/mcp"),
}
REQUEST = "分析一號機不同參數的相關係數，以熱力圖呈現並說明，可先 preview"
FIELDS = [
    "heat_rate",
    "raw_coal_consumption_g",
    "avg_generation_mw",
    "main_steam_temp_c",
    "reheat_steam_temp_c",
    "scr_temp_c",
    "condenser_vacuum",
    "coal_avg_heat_value_kcal_kg",
]


def mcp_call(server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if len(MCP_SHARED_TOKEN) < 32:
        raise RuntimeError("MCP_SHARED_TOKEN is required")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {MCP_SHARED_TOKEN}", "X-Grafana-Org-Id": os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1"), "X-Grafana-User": os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y")}
    request = urllib.request.Request(MCP_URLS[server], data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            envelope = json.loads(response.read())
        result = envelope.get("result") or {}
        content = result.get("content") or []
        payload = json.loads(content[0]["text"])
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{server}.{tool} failed to return typed JSON: {exc}") from exc
    if result.get("isError") or not payload.get("ok"):
        raise RuntimeError(f"{server}.{tool} failed: {payload}")
    return payload


def artifact_json(ref: str) -> Any:
    run_id, parts = parse_artifact_ref(ref)
    try:
        return json.loads((ARTIFACT_ROOT / run_id / f"{parts[0]}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read test evidence artifact: {ref}") from exc


def grafana_request(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    request = urllib.request.Request(GRAFANA_URL + path, data=None if body is None else json.dumps(body).encode(), headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana {method} {path} failed: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    discovery = mcp_call("query", "discover_datasets", {})
    inspected = mcp_call("query", "inspect_dataset", {"dataset_id": "u1-operating-daily"})
    plan = mcp_call("planner", "plan_query", {"dataset_metadata_ref": inspected["dataset_metadata_ref"], "selected_fields": FIELDS, "minimum_rows": 100})
    query = mcp_call("query", "execute_planned_query", {"plan_ref": plan["plan_ref"]})
    engineering = mcp_call("engineering", "analyze_correlation", {"frame_ref": query["frame_ref"], "fields": FIELDS, "method": "pearson", "minimum_rows": 100, "visualization_title": "一號機參數相關係數熱力圖"})
    prepared = mcp_call("renderer", "prepare_dashboard_write", {"analysis_result_ref": engineering["analysis_result_ref"]})
    rendered = mcp_call("renderer", "create_dashboard_from_analysis", {"analysis_result_ref": engineering["analysis_result_ref"], "approval_ref": prepared["approval_ref"]})

    method = artifact_json(engineering["method_result_ref"])
    analysis = artifact_json(engineering["analysis_result_ref"])
    dashboard_artifact = artifact_json(rendered["refs"]["dashboard_ref"])
    dashboard = dashboard_artifact["dashboard"]
    dashboard_uid = dashboard["uid"]
    exported = grafana_request(f"/api/dashboards/uid/{dashboard_uid}")
    panel = exported.get("dashboard", {}).get("panels", [{}])[0]
    panel_query = grafana_request("/api/ds/query", "POST", {"queries": panel.get("targets", []), "from": "0", "to": "9999999999999"})
    panel_frame = (panel_query.get("results", {}).get("A", {}).get("frames") or [{}])[0]
    panel_rows = len((panel_frame.get("data", {}).get("values") or [[]])[0])

    require(bool(discovery.get("datasets")) and inspected.get("metadata", {}).get("dataset_id") == "u1-operating-daily", "authorized metadata discovery failed")
    require(method.get("method") == "pairwise_correlation", "wrong engineering method")
    validity = method.get("validity") or {}
    require(query.get("validation", {}).get("row_count") == 365, "Grafana Query did not return the real 365-row frame")
    require(method.get("metrics", {}).get("input_rows") == 172 and validity.get("input_rows") == 365 and validity.get("valid_rows") == 172 and validity.get("excluded_rows") == 193, f"correlation validity filtering mismatch: {validity}")
    require(method.get("metrics", {}).get("field_count") == len(FIELDS), "correlation field count mismatch")
    require(len(method.get("pairs", [])) == len(FIELDS) * (len(FIELDS) - 1) // 2, "pairwise correlation output is incomplete")
    source = method.get("method_source") or {}
    runtime_flags = [source.get("runtime_agent"), source.get("runtime_llm"), source.get("runtime_skill")]
    require(all(isinstance(flag, bool) and not flag for flag in runtime_flags), "forbidden runtime provenance")
    require(set((analysis.get("details") or {}).get("method_result_refs", {})) == {"correlation"}, "unselected methods were included")
    require(len(analysis.get("recommended_panels", [])) == 1, "correlation intent must not produce a fixed panel set")
    require(dashboard.get("panels", [{}])[0].get("type") == "esnet-matrix-panel", "dashboard did not use the matrix heatmap plugin")
    require(len(exported.get("dashboard", {}).get("panels", [])) == 1, "Grafana dashboard export panel count mismatch")
    require(panel_query.get("results", {}).get("A", {}).get("status") == 200 and panel_rows == len(FIELDS) ** 2, "heatmap panel data query failed")
    require("相關性不是因果" in rendered.get("final_answer", ""), "final explanation lost correlation caution")

    evidence = {
        "ok": True,
        "scope": "direct deterministic Engineering vertical; adaptive Ask O11y preview/confirmation not claimed",
        "request": REQUEST,
        "tools_followed": ["grafana-query.discover_datasets", "grafana-query.inspect_dataset", "data-query-planner.plan_query", "grafana-query.execute_planned_query", "engineering-analysis.analyze_correlation", "grafana-renderer.prepare_dashboard_write", "grafana-renderer.create_dashboard_from_analysis"],
        "dataset_candidates": [dataset.get("dataset_id") for dataset in discovery["datasets"]],
        "dataset_id": inspected["metadata"]["dataset_id"],
        "selected_fields": FIELDS,
        "method": method["method"],
        "method_parameters": method["parameters"],
        "metrics": method["metrics"],
        "validity": validity,
        "strongest_pairs": method["pairs"][:5],
        "method_source": source,
        "analysis_type": analysis["analysis_type"],
        "method_result_refs": list((analysis.get("details") or {}).get("method_result_refs", {})),
        "visualizations": analysis["recommended_panels"],
        "dashboard_url": rendered["dashboard_url"],
        "dashboard_uid": dashboard_uid,
        "panel_count": rendered["panel_count"],
        "grafana_export_verified": exported.get("dashboard", {}).get("uid") == dashboard_uid,
        "heatmap_panel_data": {"status": panel_query["results"]["A"]["status"], "fields": [field.get("name") for field in panel_frame.get("schema", {}).get("fields", [])], "rows": panel_rows},
        "adaptive_runtime": False,
        "finance_real_e2e": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
