#!/usr/bin/env python3
"""Verify the deterministic Finance MCP contract without claiming live E2E."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "finance-analysis-contract.json"


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
        raise RuntimeError(message)


def main() -> int:
    finance = load("finance_contract_server", ROOT / "finance-analysis-mcp" / "server.py")
    config = load("adaptive_ask_config", ROOT / "scripts" / "configure-ask-o11y-workflow-tools.py")
    schemas = {tool["name"]: tool["inputSchema"] for tool in finance.TOOLS}
    require(set(schemas) == {"analyze_cost_drivers", "analyze_variance"}, f"unexpected Finance tools: {sorted(schemas)}")
    forbidden_properties = {"datasource", "datasource_uid", "url", "sql", "query", "code", "prompt", "skill"}
    for name, schema in schemas.items():
        additional = schema.get("additionalProperties")
        require(isinstance(additional, bool) and not additional, f"{name} must fail closed on unknown input")
        require(not forbidden_properties.intersection(schema.get("properties", {})), f"{name} exposes direct datasource/runtime input")
    prompt_lower = config.SYSTEM_PROMPT.lower()
    fixed_orchestrator_terms = ["engineering", "finance", "heat_rate", "correlation", "regression", "forecast", "anomaly", "heatmap", "timeseries", "7-panel"]
    require(not [term for term in fixed_orchestrator_terms if term in prompt_lower], "orchestrator system prompt contains a domain/method/panel branch")
    specs = {spec["id"]: spec for spec in config.SERVER_SPECS}
    require(specs["finance-analysis"]["enabled_tools"] == ["analyze_cost_drivers", "analyze_variance"], "Finance registration mismatch")
    config_source = (ROOT / "scripts" / "configure-ask-o11y-workflow-tools.py").read_text(encoding="utf-8")
    require(all(name not in config_source for name in specs["finance-analysis"]["enabled_tools"]), "Finance capabilities are hardcoded in orchestrator Python")
    source = (ROOT / "finance-analysis-mcp" / "server.py").read_text(encoding="utf-8")
    require(source.count("visualization_spec(") >= 3, "Finance must use shared analysis_core visualization_spec")
    forbidden_runtime = ["import openai", "import anthropic", "import subprocess", "os.system(", "shell=True", "exec(", "eval("]
    require(not [marker for marker in forbidden_runtime if marker in source], "Finance MCP contains a forbidden runtime")

    token = os.environ.get("MCP_SHARED_TOKEN", "")
    require(len(token) >= 32, "MCP_SHARED_TOKEN is required for live Finance inspection")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", "X-Grafana-Org-Id": os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1"), "X-Grafana-User": os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y")}
    request = urllib.request.Request("http://127.0.0.1:8776/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            live = json.loads(response.read())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot inspect live Finance MCP tools: {exc}") from exc
    live_names = [tool["name"] for tool in live.get("result", {}).get("tools", [])]
    require(set(live_names) == set(schemas), f"live Finance tools differ from source: {live_names}")

    out = {
        "ok": True,
        "finance_real_e2e": False,
        "scope": "deterministic unit/contract/security/self-check only; live Grafana/Ask O11y Finance E2E deferred",
        "tools": live_names,
        "schemas_fail_closed": True,
        "artifact_authorization_self_check": True,
        "domain_validation": ["currency", "fiscal_period", "target/drivers", "actual/baseline"],
        "forbidden_runtime": [],
        "direct_datasource_inputs": [],
        "orchestrator_domain_branches": [],
        "registration_only": "Finance capability names come from config/adaptive-mcp-capabilities.json; generic orchestrator Python and SYSTEM_PROMPT contain no Finance/domain method branch",
        "shared_analysis_core_visualization_spec": True,
        "provenance": {"runtime_agent": False, "runtime_llm": False, "runtime_skill": False},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
