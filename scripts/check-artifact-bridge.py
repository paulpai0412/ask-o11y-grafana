#!/usr/bin/env python3
"""Focused contract check for opaque dashboard target resolution."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_bridge():
    path = ROOT / "artifact-bridge-mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("artifact_bridge_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Artifact Bridge")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    bridge = load_bridge()
    with tempfile.TemporaryDirectory() as tmp:
        artifacts = bridge.ArtifactStore(Path(tmp) / "runs")
        setattr(bridge, "ARTIFACTS", artifacts)
        context = {"org_id": "1", "user_id": "bridge-check"}
        run_id = artifacts.create_run(context)
        plan_ref = artifacts.write_json(context, run_id, "query-plan", {
            "datasource_uid": "csv-poc",
            "datasource_type": "yesoreyeram-infinity-datasource",
            "selected_fields": ["date", "x", "y"],
            "grafana_query": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://data.example/input.csv", "parser": "backend", "format": "table", "columns": [{"selector": "date", "text": "date", "type": "timestamp"}, {"selector": "x", "text": "x", "type": "number"}, {"selector": "y", "text": "y", "type": "number"}]},
        })
        dashboard = {"title": "Model authored", "panels": [{"type": "xychart", "options": {"mapping": "auto"}, "targets": [{"$plan_ref": plan_ref, "fields": ["x", "y"]}]}]}
        result = bridge.resolve_dashboard_refs({"dashboard": dashboard, "_server_context": context})
        panel = result.get("dashboard", {}).get("panels", [{}])[0]
        checks = {
            "model_panel_type_preserved": panel.get("type") == "xychart",
            "model_panel_options_preserved": panel.get("options") == {"mapping": "auto"},
            "trusted_query_bound": panel.get("targets", [{}])[0].get("url") == "http://data.example/input.csv",
            "bridge_has_no_grafana_write": not any(name.startswith(("create_", "update_", "prepare_")) for name in [tool["name"] for tool in bridge.TOOLS]),
        }
        if not result.get("ok") or not all(checks.values()):
            raise AssertionError(json.dumps({"checks": checks, "result": result}, indent=2))
        print(json.dumps({"ok": True, "checks": list(checks)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
