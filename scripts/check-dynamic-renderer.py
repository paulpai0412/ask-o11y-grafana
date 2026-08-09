#!/usr/bin/env python3
"""Verify dynamic renderer types, panel counts, approval gate, and fail-closed specs."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_renderer() -> Any:
    path = ROOT / "grafana-renderer-mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("dynamic_renderer_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load renderer")
    module = importlib.util.module_from_spec(spec)
    sys.modules["dynamic_renderer_check"] = module
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    renderer = load_renderer()
    writes: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        renderer.ARTIFACTS = renderer.ArtifactStore(Path(tmp) / "runs")
        renderer.CHART_OUTPUT_DIR = Path(tmp) / "chart-csv"
        renderer.CHART_URL_BASE = "http://example.invalid/analysis"
        renderer.GRAFANA_URL = "http://grafana.example.invalid"
        context = {"org_id": "1", "user_id": "dynamic-renderer-check"}
        run_id = renderer.ARTIFACTS.create_run(context, "run_dynamic_renderer")
        source = {"mode": "deterministic_library", "implementation": "analysis-core-check", "method": "dynamic_rendering", "algorithm": "declarative_specs", "algorithm_version": "1", "libraries": [{"name": "pandas", "version": "3.0.5"}], "runtime_agent": False, "runtime_llm": False, "runtime_skill": False}
        method_ref = renderer.ARTIFACTS.write_json(context, run_id, "method-dynamic", {"method": "dynamic_rendering", "method_source": source})
        frames = [
            {"name": "table", "schema": {"fields": [{"name": "name"}, {"name": "value"}]}, "data": {"values": [["a", "b"], [1, 2]]}},
            {"name": "trend", "schema": {"fields": [{"name": "time"}, {"name": "value"}]}, "data": {"values": [["2026-01-01", "2026-01-02"], [1, 2]]}},
            {"name": "importance", "schema": {"fields": [{"name": "feature"}, {"name": "importance"}]}, "data": {"values": [["a", "b"], [0.7, 0.3]]}},
            {"name": "prediction", "schema": {"fields": [{"name": "actual"}, {"name": "predicted"}]}, "data": {"values": [[1, 2], [1.1, 1.9]]}},
            {"name": "correlation", "schema": {"fields": [{"name": "source"}, {"name": "target"}, {"name": "correlation"}]}, "data": {"values": [["a", "a", "b", "b"], ["a", "b", "a", "b"], [1, 0.5, 0.5, 1]]}},
        ]
        panels = [
            {"type": "table", "title": "Table", "data_frame": "table"},
            {"type": "timeseries", "title": "Trend", "data_frame": "trend", "x": "time", "y": ["value"]},
            {"type": "bar", "title": "Importance", "data_frame": "importance", "x": "feature", "y": ["importance"]},
            {"type": "scatter", "title": "Prediction", "data_frame": "prediction", "x": "actual", "y": ["predicted"]},
            {"type": "heatmap", "title": "Correlation", "plugin_id": "esnet-matrix-panel", "data_frame": "correlation", "source": "source", "target": "target", "value": "correlation"},
        ]
        base = {"analysis_type": "dynamic", "title": "Dynamic", "summary": "Dynamic rendering check.", "severity": "info", "time_range": {"from": None, "to": None}, "subject": {"domain": "check"}, "findings": [{"level": "info", "message": "ready"}], "data_frames": frames, "recommended_panels": panels, "details": {"method_result_refs": {"dynamic": method_ref}, "method_source": source}}

        def fake_post(dashboard):
            writes.append(dashboard)
            return {"uid": dashboard["uid"], "url": "/d/check"}

        def render_with_capability(ref):
            prepared = renderer.prepare_dashboard_write({"analysis_result_ref": ref, "_server_context": context})
            require(prepared.get("ok"), f"approval preparation failed: {prepared}")
            return renderer.render_analysis({"analysis_result_ref": ref, "approval_ref": prepared["approval_ref"], "_server_context": context}, post_fn=fake_post)

        five_ref = renderer.ARTIFACTS.write_json(context, run_id, "analysis-five", base)
        five = render_with_capability(five_ref)
        require(five.get("ok") and five.get("panel_count") == 5, "five-panel dynamic render failed")
        rendered_types = [panel["type"] for panel in writes[-1]["panels"]]
        require(rendered_types == ["table", "timeseries", "barchart", "xychart", "esnet-matrix-panel"], f"unexpected rendered types: {rendered_types}")

        two_analysis = deepcopy(base)
        two_analysis["data_frames"] = frames[:2]
        two_analysis["recommended_panels"] = panels[:2]
        two_ref = renderer.ARTIFACTS.write_json(context, run_id, "analysis-two", two_analysis)
        two = render_with_capability(two_ref)
        require(two.get("ok") and two.get("panel_count") == 2, "panel count is fixed instead of intent-driven")

        write_count = len(writes)

        def chart_snapshot():
            return {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in renderer.CHART_OUTPUT_DIR.glob("*.csv")}

        chart_files = chart_snapshot()
        rejected = renderer.render_analysis({"analysis_result_ref": five_ref, "_server_context": context}, post_fn=fake_post)
        require(not rejected.get("ok") and len(writes) == write_count and chart_snapshot() == chart_files, "missing approval caused a write side effect")

        bad = deepcopy(base)
        bad["recommended_panels"] = [{"type": "scatter", "title": "Bad", "data_frame": "prediction", "x": "missing", "y": ["predicted"]}]
        bad_ref = renderer.ARTIFACTS.write_json(context, run_id, "analysis-bad-spec", bad)
        inconsistent = renderer.prepare_dashboard_write({"analysis_result_ref": bad_ref, "_server_context": context})
        require(not inconsistent.get("ok") and len(writes) == write_count, "inconsistent visualization spec caused a Grafana write")

        replay = renderer.render_analysis({"analysis_result_ref": five_ref, "approval_ref": five["evidence"]["approval_ref"], "_server_context": context}, post_fn=fake_post)
        require(not replay.get("ok") and len(writes) == write_count and chart_snapshot() == chart_files, "replayed approval caused a write side effect")

        mismatch_prepared = renderer.prepare_dashboard_write({"analysis_result_ref": five_ref, "_server_context": context})
        mismatch = renderer.render_analysis({"analysis_result_ref": two_ref, "approval_ref": mismatch_prepared["approval_ref"], "_server_context": context}, post_fn=fake_post)
        require(not mismatch.get("ok") and len(writes) == write_count and chart_snapshot() == chart_files, "mismatched approval caused a write side effect")

        expired_prepared = renderer.prepare_dashboard_write({"analysis_result_ref": five_ref, "_server_context": context})
        _, expired_parts = renderer.parse_artifact_ref(expired_prepared["approval_ref"])
        expired = renderer.ARTIFACTS.read_json(context, expired_prepared["approval_ref"])
        expired["expires_at"] = 0
        renderer.ARTIFACTS.write_json(context, run_id, expired_parts[0], expired)
        expired_out = renderer.render_analysis({"analysis_result_ref": five_ref, "approval_ref": expired_prepared["approval_ref"], "_server_context": context}, post_fn=fake_post)
        require(not expired_out.get("ok") and len(writes) == write_count and chart_snapshot() == chart_files, "expired approval caused a write side effect")

        evidence = {"ok": True, "panel_counts": [five["panel_count"], two["panel_count"]], "rendered_types": rendered_types, "approval_rejection_no_write": True, "server_verified_one_time_approval": True, "approval_invalid_cases_no_any_write": ["missing", "replay", "mismatch", "expired"], "inconsistent_spec_no_write": True, "writes": len(writes)}
        out = ROOT / ".scratch" / "poc" / "dynamic-renderer-check.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
