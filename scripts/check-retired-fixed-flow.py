#!/usr/bin/env python3
"""Prove superseded fixed-flow services, scripts, documents, and listeners are gone."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "retired-fixed-flow-check.json"

RETIRED_PATHS = [
    ROOT / "scientific-method-mcp",
    ROOT / "thermal-power-analysis-mcp",
    ROOT / "grafana-poc-mcp",
    ROOT / "wferp-mcp/server.py",
    ROOT / "engineering-analysis-mcp",
    ROOT / "finance-analysis-mcp",
    ROOT / "analysis_core",
    ROOT / "wferp_provider.py",
    ROOT / "data/poc/u1_heat_rate_process_variation.csv",
    ROOT / "data/poc/u1_heat_rate_process_variation.metadata.json",
    ROOT / "data/poc/firepower_csv_profile.json",
    ROOT / "scripts/check-strong-model-workflow-chain.py",
    ROOT / "scripts/run-final-e2e-analysis.py",
    ROOT / "scripts/run-ask-o11y-workflow-e2e.py",
    ROOT / "scripts/check-workflow-contract.py",
    ROOT / "scripts/run-e2e-demo.py",
    ROOT / "scripts/test-process-variation-fixture.py",
    ROOT / "scripts/create-analysis-dashboard.py",
    ROOT / "scripts/run-planned-query.py",
    ROOT / "scripts/check-scientific-method-provenance.py",
    ROOT / "docs/strong-model-mcp-orchestration-design.md",
    ROOT / "docs/two-layer-scientific-method-mcp-architecture.md",
    ROOT / "docs/firepower-analysis-mcp-design.md",
    ROOT / "docs/research/ask-o11y-pi-agent-loop-assessment.md",
]
ACTIVE_PORTS = [8768, 8771, 8772, 8773, 8777]
RETIRED_PORTS = [8765, 8769, 8774, 8775, 8776]


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def contains_legacy_result(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("analysis_type") == "process_variation":
            return True
        return any(contains_legacy_result(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_legacy_result(item) for item in value)
    return isinstance(value, str) and ("K-Dense" in value or "scientific-method-mcp" in value)


def main() -> int:
    require(contains_legacy_result({"analysis_type": "process_variation"}) and contains_legacy_result({"nested": ["K-Dense runtime"]}), "legacy result semantic detector self-check failed")
    present = [str(path.relative_to(ROOT)) for path in RETIRED_PATHS if path.exists()]
    require(not present, f"retired fixed-flow paths remain executable: {present}")
    workflow_source = (ROOT / "workflow_node.py").read_text(encoding="utf-8")
    require("next_step" not in workflow_source and "next_tool" not in workflow_source, "shared response contract still supports a fixed next-step chain")
    scratch_root = ROOT / ".scratch"
    legacy_scratch_markers = {"five-endpoint", "engineering-analysis", "finance-analysis", "scientific-method", "thermal-power"}
    legacy_scratch = [str(path.relative_to(ROOT)) for path in scratch_root.rglob("*") if any(marker in path.name.lower() for marker in legacy_scratch_markers)]
    require(not legacy_scratch, f"stale legacy scratch artifacts remain: {legacy_scratch}")
    stale_bytecode = []
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if name not in {".git", ".venv", "node_modules"}]
        stale_bytecode.extend(str((Path(directory) / name).relative_to(ROOT)) for name in files if name.startswith("wferp_provider") and name.endswith(".pyc"))
    require(not stale_bytecode, f"stale WFERP provider bytecode remains: {stale_bytecode}")
    legacy_artifact_names = {"analysis-result.json", "method-association.json", "method-anomalies.json", "method-forecast.json"}
    artifact_roots = [ROOT / ".analysis-artifacts", ROOT / "data" / "poc" / "analysis", ROOT / "data-query-planner-mcp" / "metadata", ROOT / "data" / "poc"]
    legacy_artifacts = []
    for artifact_root in artifact_roots:
        for path in artifact_root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in legacy_artifact_names or path.name.startswith("process_variation-"):
                legacy_artifacts.append(str(path.relative_to(ROOT)))
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if path.suffix == ".json":
                try:
                    value = json.loads(text)
                except ValueError:
                    value = None
                if contains_legacy_result(value):
                    legacy_artifacts.append(str(path.relative_to(ROOT)))
            elif "K-Dense" in text or "scientific-method-mcp" in text:
                legacy_artifacts.append(str(path.relative_to(ROOT)))
    require(not legacy_artifacts, f"legacy fixed-flow result corpus remains: {legacy_artifacts[:5]}")
    retired_open = [port for port in RETIRED_PORTS if port_open(port)]
    active_closed = [port for port in ACTIVE_PORTS if not port_open(port)]
    require(not retired_open, f"retired MCP listeners remain open: {retired_open}")
    require(not active_closed, f"one of the five active MCP listeners is closed: {active_closed}")
    try:
        settings = json.loads((ROOT / ".scratch" / "poc" / "ask-o11y-workflow-tools-settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load generated Ask O11y settings: {exc}") from exc
    enabled_server_ids = [item.get("id") for item in settings.get("jsonData", {}).get("mcpServers", []) if isinstance(item, dict) and item.get("enabled")]
    require(enabled_server_ids == ["ontology", "data-query-planner", "grafana-query", "sandbox-analysis", "artifact-bridge"], f"Ask O11y does not expose exactly five MCP endpoints: {enabled_server_ids}")
    evidence_files = ["sandbox-analysis-real-spike.json", "sandbox-analysis-http-e2e.json", "ask-o11y-sandbox-shap-e2e.json"]
    try:
        evidence = [json.loads((ROOT / ".scratch" / "poc" / name).read_text(encoding="utf-8")) for name in evidence_files]
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load post-retirement E2E evidence: {exc}") from exc
    require(all(bool(item.get("ok", True)) for item in evidence), "post-retirement E2E evidence is incomplete")
    out = {
        "ok": True,
        "retired_paths_absent": [str(path.relative_to(ROOT)) for path in RETIRED_PATHS],
        "retired_ports_closed": RETIRED_PORTS,
        "active_mcp_ports_open": ACTIVE_PORTS,
        "enabled_server_ids": enabled_server_ids,
        "shared_next_step_contract": False,
        "legacy_scratch_artifacts": [],
        "stale_wferp_bytecode": [],
        "legacy_artifacts": [],
        "legacy_json_semantic_detection": True,
        "post_retirement_e2e": evidence_files,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
