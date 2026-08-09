#!/usr/bin/env python3
"""Fast regression check for adaptive preview/confirmation orchestration."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    config = load("adaptive_config_regression", ROOT / "scripts" / "configure-ask-o11y-workflow-tools.py")
    planner = load("adaptive_planner_regression", ROOT / "data-query-planner-mcp" / "server.py")
    engineering = load("adaptive_engineering_regression", ROOT / "engineering-analysis-mcp" / "server.py")
    finance = load("adaptive_finance_regression", ROOT / "finance-analysis-mcp" / "server.py")
    renderer = load("adaptive_renderer_regression", ROOT / "grafana-renderer-mcp" / "server.py")

    prompt = config.SYSTEM_PROMPT
    for text in ["Analysis Preview", "explicit confirmation", "There is no fixed workflow", "Call only capabilities required", "material change", "approval-gated Grafana write"]:
        require(text in prompt, f"system prompt missing adaptive rule: {text}")
    for forbidden in ["next_step.args", "一號機", "熱耗率", "8-step", "scientific-method", "thermal-power-analysis", "analyze_process_variation", "7 panels"]:
        require(forbidden.lower() not in prompt.lower(), f"system prompt contains fixed-flow marker: {forbidden}")

    specs = {spec["id"]: spec for spec in config.SERVER_SPECS}
    require(set(specs) == {"data-query-planner", "grafana-query", "engineering-analysis", "finance-analysis", "grafana-renderer"}, f"unexpected MCP topology: {sorted(specs)}")
    require(specs["data-query-planner"]["enabled_tools"] == ["plan_query"], "planner must expose only plan_query")
    require(specs["grafana-query"]["enabled_tools"] == ["discover_datasets", "inspect_dataset", "execute_planned_query"], "Grafana Query read boundary toolset changed")
    require(set(specs["engineering-analysis"]["enabled_tools"]) == {"analyze_profile", "analyze_correlation", "analyze_predictive", "analyze_patterns", "analyze_timeseries"}, "Engineering high-level capabilities incomplete")
    require(set(specs["finance-analysis"]["enabled_tools"]) == {"analyze_cost_drivers", "analyze_variance"}, "Finance high-level capabilities incomplete")
    require(specs["grafana-renderer"]["enabled_tools"] == ["prepare_dashboard_write", "create_dashboard_from_analysis"], "renderer capability/write tools changed")
    config_source = (ROOT / "scripts" / "configure-ask-o11y-workflow-tools.py").read_text(encoding="utf-8")
    require(all(name not in config_source for spec in specs.values() for name in spec["enabled_tools"]), "capability names are hardcoded in orchestrator Python instead of config")

    planner_schema = next(tool["inputSchema"] for tool in planner.TOOLS if tool["name"] == "plan_query")
    require(planner_schema["required"] == ["dataset_metadata_ref", "selected_fields"], "planner must require opaque metadata and explicit fields")
    require("profile" not in planner_schema["properties"] and "target" not in planner_schema["properties"], "planner contains fixed domain routing")
    planned = planner.tool_plan_query({"dataset_metadata_ref": "artifact://run_adaptive01/grafana-metadata-unit1", "selected_fields": ["date", "pressure"]})
    require("next_step" not in planned, "planner emitted fixed next_step chain")

    engineering_tools = {tool["name"]: tool for tool in engineering.TOOLS}
    require(set(engineering_tools) == {"analyze_profile", "analyze_correlation", "analyze_predictive", "analyze_patterns", "analyze_timeseries"}, "Engineering MCP tools differ from configuration")
    for tool in engineering_tools.values():
        schema = tool["inputSchema"]
        require("frame_ref" in schema["required"], f"{tool['name']} must require an authorized frame_ref")
        serialized = json.dumps(tool, ensure_ascii=False).lower()
        require("datasource_uid" not in serialized and "query" not in schema["properties"], f"{tool['name']} exposes direct datasource access")

    finance_tools = {tool["name"]: tool for tool in finance.TOOLS}
    require(set(finance_tools) == {"analyze_cost_drivers", "analyze_variance"}, "Finance MCP tools differ from configuration")
    finance_e2e = finance.SERVER_INFO.get("finance_real_e2e")
    require(isinstance(finance_e2e, bool) and not finance_e2e, "Finance deferred E2E marker missing")
    finance_source = (ROOT / "finance-analysis-mcp" / "server.py").read_text(encoding="utf-8")
    require(finance_source.count("visualization_spec(") >= 3, "Finance does not use shared analysis_core visualization mechanics")

    renderer_tools = {tool["name"]: tool for tool in renderer.TOOLS}
    require(set(renderer_tools) == {"prepare_dashboard_write", "create_dashboard_from_analysis"}, "Renderer tools must expose prepare plus approval-gated write")
    prepare_schema = renderer_tools["prepare_dashboard_write"]["inputSchema"]
    create_schema = renderer_tools["create_dashboard_from_analysis"]["inputSchema"]
    require(prepare_schema["required"] == ["analysis_result_ref"] and create_schema["required"] == ["analysis_result_ref", "approval_ref"], "server-issued Renderer approval capability is not schema-required")
    require("approval_confirmed" not in create_schema["properties"], "Renderer still trusts a caller-controlled approval boolean")

    evidence = {"ok": True, "checks": ["adaptive_system_prompt", "five_endpoint_topology", "config_only_capability_registration", "planner_plan_only", "grafana_query_read_boundary", "engineering_high_level_tools", "finance_shared_visualization", "finance_config_registration", "server_verified_renderer_approval", "no_fixed_next_step"], "engineering_tools": sorted(engineering_tools), "finance_real_e2e": False}
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
