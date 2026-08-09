#!/usr/bin/env python3
"""Catch semantic Engineering, provenance, and service-identity regressions."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analysis_core.timeseries import forecast_series  # noqa: E402


def load(name: str, path: Path) -> Any:
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
    dates = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    try:
        forecast = forecast_series(pd.DataFrame({"date": [int(value.timestamp() * 1000) for value in dates], "metric": range(40)}), time_field="date", target="metric", horizon=7)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"forecast semantic check failed: {exc}") from exc
    require(forecast["history"][0]["time"].startswith("2026-01-01"), f"epoch milliseconds parsed incorrectly: {forecast['history'][0]}")

    server = load("engineering_semantic_server", ROOT / "engineering-analysis-mcp" / "server.py")
    with tempfile.TemporaryDirectory() as tmp:
        store = server.ArtifactStore(Path(tmp) / "runs")
        server.ARTIFACTS = store
        context = {"org_id": "1", "user_id": "semantic-check"}
        run_id = store.create_run(context)
        rows = 60
        valid = [False] * 20 + [True] * 40
        heat_rate = [0.0] * 20 + [9000.0 + index * 5 for index in range(40)]
        frame = {"schema": {"fields": [{"name": "heat_rate"}, {"name": "feature_a"}, {"name": "feature_b"}, {"name": "heat_rate_valid"}]}, "data": {"values": [heat_rate, list(range(rows)), [index * 2 for index in range(rows)], valid]}}
        store.write_json(context, run_id, "query-plan", {"analysis_input_contract": {"validity_rules": [{"field": "heat_rate_valid", "applies_to": ["heat_rate"], "accepted_values": [True]}]}})
        frame_ref = store.write_json(context, run_id, "grafana-frame", [frame])
        profile_result = server.high_level_analysis("analyze_profile", {"frame_ref": frame_ref, "fields": ["heat_rate", "feature_a", "feature_b"], "_server_context": context})
        require(bool(profile_result.get("ok")), f"user-facing profile check failed: {profile_result}")
        profile_method = store.read_json(context, profile_result["method_result_ref"])
        require(profile_method.get("method") == "profile" and profile_method.get("profile", {}).get("rows") == 40 and profile_result.get("preview", {}).get("visualizations", [{}])[0].get("type") == "table", f"profile capability is incomplete: {profile_method}")
        result = server.high_level_analysis("analyze_predictive", {"frame_ref": frame_ref, "target": "heat_rate", "features": ["feature_a", "feature_b"], "task": "regression", "model_family": "linear", "seed": 42, "_server_context": context})
        require(bool(result.get("ok")), f"predictive semantic check failed: {result}")
        method = store.read_json(context, result["method_result_ref"])
        validity = method.get("validity") or {}
        require(validity.get("input_rows") == 60 and validity.get("valid_rows") == 40 and validity.get("excluded_rows") == 20, f"validity audit missing/wrong: {validity}")
        try:
            actuals = [float(row["actual"]) for row in method["predictions"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid prediction artifact: {exc}") from exc
        require(all(value > 0 for value in actuals), "invalid zero heat-rate rows reached evaluation")

    security = load("semantic_mcp_security", ROOT / "mcp_security.py")
    old = {name: os.environ.get(name) for name in ["MCP_SHARED_TOKEN", "ANALYSIS_SERVICE_ORG_ID", "ANALYSIS_SERVICE_USER_ID"]}
    try:
        os.environ.update({"MCP_SHARED_TOKEN": "x" * 32, "ANALYSIS_SERVICE_ORG_ID": "1", "ANALYSIS_SERVICE_USER_ID": "ask-o11y"})
        forged = {"Authorization": "Bearer " + "x" * 32, "X-Grafana-Org-Id": "999", "X-Grafana-User": "attacker"}
        require(security.authenticate_headers(forged) is None, "valid bearer accepted forged org/user identity")
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    try:
        manifest = json.loads((ROOT / "docs" / "third-party-reuse-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read reuse manifest: {exc}") from exc
    sources = manifest.get("sources", [])
    adopted = [item for item in sources if str(item.get("status", "")).startswith("adopted") or item.get("status") == "approved-runtime-plugin"]
    require(bool(adopted) and all(bool(item.get("copyright")) and bool(item.get("notice")) for item in adopted), "adopted third-party records require copyright and NOTICE metadata")
    require((ROOT / "NOTICE").is_file() and (ROOT / "docs" / "sbom.json").is_file(), "NOTICE and docs/sbom.json are required")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    infinity = next((item for item in adopted if item.get("name") == "grafana/grafana-infinity-datasource"), None)
    require(isinstance(infinity, dict) and infinity.get("tag") == "v3.11.2" and "yesoreyeram-infinity-datasource 3.11.2" in compose, "Infinity datasource provenance/version must be pinned")
    sdk = next((item for item in sources if item.get("name") == "modelcontextprotocol/python-sdk"), None)
    require(isinstance(sdk, dict) and sdk.get("status") == "evaluated-not-adopted-runtime" and sdk.get("evidence") == ".scratch/poc/mcp-python-sdk-compatibility-spike.json" and (ROOT / str(sdk["evidence"])).is_file(), "official MCP SDK requires an evidence-backed adoption/rejection decision")
    planner_source = (ROOT / "data-query-planner-mcp" / "server.py").read_text(encoding="utf-8")
    require(not any(marker in planner_source for marker in ["GRAFANA_PASSWORD", "GRAFANA_USER", "fetch_grafana_datasource", "urllib.request", "urlopen("]), "Planner source must not access Grafana or datasource credentials")
    require("analyze_profile" in {tool["name"] for tool in server.TOOLS}, "Engineering profile/EDA must be user-facing")
    print(json.dumps({"ok": True, "forecast_year": 2026, "invalid_rows_excluded": 20, "valid_bearer_forgery_rejected": True, "notice_sbom": True, "infinity_version": "3.11.2", "engineering_profile_tool": True, "planner_grafana_access": False, "mcp_sdk_decision": "evaluated-not-adopted-runtime"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
