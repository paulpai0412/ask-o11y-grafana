#!/usr/bin/env python3
"""TDD check for prepare/compose report tools over opaque execution artifacts."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "artifact-bridge-mcp"))


def load_bridge():
    path = ROOT / "artifact-bridge-mcp/server.py"
    spec = importlib.util.spec_from_file_location("report_tools_bridge_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def synthesis() -> dict:
    return {
        "format": "ask-o11y-report-synthesis-v1", "report_title": "动态报告", "thesis": "完整证据支持当前解释，但行动前仍需验证。",
        "sections": [{"section_id": "model-choice", "title": "本次重点", "purpose": "根据完整报告说明最重要的判断。", "collapsed": False, "panels": [{
            "artifact_id": "chart", "headline": "主要证据呈现明显结构", "observation": "本图与完整报告事实一致。",
            "interpretation": "此结构会影响判断重点。", "cross_chart_context": "应与其他证据和限制共同阅读。",
            "limitation": "目前不能建立因果结论。", "next_step": "使用额外资料继续验证。",
            "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}], "priority": "primary", "preferred_width": "full",
        }]}],
    }


def main() -> int:
    bridge = load_bridge()
    with tempfile.TemporaryDirectory() as tmp:
        setattr(bridge, "ARTIFACTS", bridge.ArtifactStore(Path(tmp) / "runs"))
        context = {"org_id": "1", "user_id": "report-tools"}
        run_id = bridge.ARTIFACTS.create_run(context)
        manifest = {"format": "fixture", "metrics": {"signal": 0.42}, "artifacts": [{"name": "chart.png", "caption": "x", "alt_text": "x"}]}
        execution_ref = bridge.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": [
            {"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "chart.png"},
            {"mime": {"application/json": json.dumps({
                "data": [
                    {"type": "bar", "x": ["a"], "y": [1]},
                    {"type": "bar", "x": ["b"], "y": [2], "xaxis": "x2", "yaxis": "y2"},
                    {"type": "bar", "x": ["c"], "y": [3], "xaxis": "x3", "yaxis": "y3"},
                    {"type": "bar", "x": ["d"], "y": [4], "xaxis": "x4", "yaxis": "y4"},
                ],
                "layout": {"xaxis": {}, "yaxis": {}, "xaxis2": {}, "yaxis2": {}, "xaxis3": {}, "yaxis3": {}, "xaxis4": {}, "yaxis4": {}},
            })}, "display_name": "ml-plotly-chart.json"},
            {"mime": {"application/json": json.dumps(manifest)}, "display_name": "ml-presentation.json"},
        ], "error": None})

        prepared = bridge.prepare_ml_report({"execution_ref": execution_ref, "manifest_output_index": 2, "_server_context": context})
        assert prepared["ok"], prepared
        assert prepared["report_context"]["facts"]["metrics.signal"]["value"] == 0.42, prepared
        assert prepared["report_context"]["artifacts"] == [{"artifact_id": "chart", "png_output_index": 0, "plotly_output_index": 1, "recommended_width": "full", "min_height": 16}], prepared

        composed = bridge.compose_ml_dashboard({
            "execution_ref": execution_ref, "manifest_output_index": 2, "synthesis": synthesis(),
            "uid": "dynamic-report", "title": "Dynamic Report", "_server_context": context,
        })
        assert composed["ok"], composed
        dashboard = composed["dashboard"]
        assert dashboard["panels"][0]["askO11ySectionId"] == "model-choice", dashboard
        assert "主要证据呈现明显结构" in str(dashboard), dashboard

        bad = synthesis(); bad["sections"][0]["panels"][0]["evidence"][0]["fact_ref"] = "unknown"
        denied = bridge.compose_ml_dashboard({
            "execution_ref": execution_ref, "manifest_output_index": 2, "synthesis": bad,
            "uid": "bad", "title": "Bad", "_server_context": context,
        })
        assert not denied["ok"], denied

    tool_names = {item["name"] for item in bridge.TOOLS}
    assert {"prepare_ml_report", "compose_ml_dashboard", "resolve_dashboard_refs"} <= tool_names, tool_names
    print("ok: Artifact Bridge generic report tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
