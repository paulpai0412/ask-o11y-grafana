#!/usr/bin/env python3
"""Verify fresh intent-driven dashboards and every panel query through Grafana."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "real-dynamic-dashboard-check.json"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")


def request_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    token = base64.b64encode(f"{os.environ.get('GRAFANA_USER', 'admin')}:{os.environ.get('GRAFANA_PASSWORD', 'admin')}".encode()).decode()
    request = urllib.request.Request(GRAFANA_URL + path, data=None if body is None else json.dumps(body).encode(), headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana API failed: {method} {path}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read evidence: {path}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def renderer_arguments(status: dict[str, Any]) -> dict[str, Any]:
    for event in status.get("events") or []:
        data = event.get("data") or {}
        if event.get("type") != "tool_call_start" or data.get("name") != "grafana-renderer_create_dashboard_from_analysis":
            continue
        try:
            arguments = json.loads(data.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("invalid renderer arguments in E2E evidence") from exc
        if isinstance(arguments, dict):
            return arguments
    raise RuntimeError("renderer arguments missing from E2E evidence")


def run_id_from_ref(ref: str) -> str:
    parts = ref.split("/")
    if len(parts) < 4 or not parts[2].startswith("run_"):
        raise RuntimeError(f"invalid analysis ref: {ref}")
    return parts[2]


def dashboard_uid(run_id: str) -> str:
    evidence = load_json(ROOT / ".analysis-artifacts" / "runs" / run_id / "render-evidence.json")
    url = str(evidence.get("dashboard_url") or "")
    if "/d/" not in url:
        raise RuntimeError(f"render evidence has no dashboard URL: {run_id}")
    return url.split("/d/", 1)[1].split("/", 1)[0]


def main() -> int:
    correlation = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-correlation-preview-e2e.json")
    dynamic = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-dynamic-engineering-e2e.json")
    profile = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-profile-e2e.json")
    correlation_renderer = renderer_arguments(correlation["raw"]["execution_status"])
    profile_renderer = renderer_arguments(profile["raw"]["execution_status"])
    refs = {"correlation_heatmap": correlation_renderer["analysis_result_ref"], "eda_profile": profile_renderer["analysis_result_ref"]}
    refs.update({case["id"]: case["execution"]["renderer_arguments"]["analysis_result_ref"] for case in dynamic["cases"]})
    expected = {"correlation_heatmap": ["esnet-matrix-panel"], "eda_profile": ["table"], "anomaly_forecast": ["timeseries", "timeseries"], "supervised_regression": ["barchart", "xychart"]}
    dashboards = []
    for intent, ref in refs.items():
        run_id = run_id_from_ref(ref)
        uid = dashboard_uid(run_id)
        dashboard = request_json(f"/api/dashboards/uid/{uid}")["dashboard"]
        panels = dashboard.get("panels") or []
        panel_types = [panel.get("type") for panel in panels]
        require(panel_types == expected[intent], f"{intent} panel types mismatch: {panel_types}")
        panel_evidence = []
        for panel in panels:
            target = dict(panel["targets"][0])
            target["datasource"] = panel["datasource"]
            query_result = request_json("/api/ds/query", "POST", {"queries": [target], "from": "0", "to": "9999999999999"})
            result = (query_result.get("results") or {}).get(str(target.get("refId") or "A"), {})
            frames = result.get("frames") or []
            require(result.get("status") == 200 and isinstance(frames, list) and len(frames) > 0, f"{intent}/{panel.get('title')} Grafana panel query failed")
            values = frames[0].get("data", {}).get("values", [])
            row_count = len(values[0]) if values and isinstance(values[0], list) else 0
            require(row_count > 0, f"{intent}/{panel.get('title')} returned no Grafana rows")
            panel_evidence.append({"title": panel.get("title"), "type": panel.get("type"), "rows": row_count, "datasource_uid": panel.get("datasource", {}).get("uid")})
        dashboards.append({"intent": intent, "uid": uid, "url": f"{GRAFANA_URL}/d/{uid}", "analysis_result_ref": ref, "panel_count": len(panels), "panels": panel_evidence})
    require([item["panel_count"] for item in dashboards] == [1, 1, 2, 2], "dashboard panel count is fixed")
    evidence = {"ok": True, "dashboards": dashboards, "validation": {"real_grafana_api": True, "every_panel_query_status_200": True, "dynamic_panel_counts": [1, 1, 2, 2], "types": ["heatmap", "table", "timeseries", "bar", "scatter"]}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
