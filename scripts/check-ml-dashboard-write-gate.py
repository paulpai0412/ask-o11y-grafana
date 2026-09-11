#!/usr/bin/env python3
"""Self-check for generic report composition through the Artifact Bridge write-gate."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ml_dashboard_compositor as compositor  # type: ignore[reportMissingImports]


def load_bridge():
    path = ROOT / "artifact-bridge-mcp/server.py"
    spec = importlib.util.spec_from_file_location("generic_report_bridge_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture(execution_ref: str) -> dict:
    manifest = {"format": "fixture", "purpose": "fixture question", "metrics": {"signal": 0.42}, "artifacts": [{"name": "chart.png", "caption": "x", "alt_text": "x"}]}
    synthesis = {
        "format": "ask-o11y-report-synthesis-v1", "report_title": "动态报告", "thesis": "证据支持当前解释，行动前仍需验证。",
        "thesis_evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
        "sections": [{"section_id": "arbitrary", "title": "动态结构", "purpose": "回答本次资料提出的问题。", "collapsed": False, "narrative_blocks": [], "panels": [{
            "artifact_id": "chart", "view_ids": ["view-1"],
            "view_narratives": [{"view_id": "view-1", "headline": "此视图呈现主要证据", "data_observation": "资料模型显示明确结构。", "visual_observation": "图形呈现清楚分层。", "interpretation": "这会影响判断重点。", "limitation": "目前不能建立因果结论。", "next_step": "使用额外资料验证。", "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}]}],
            "headline": "主要证据呈现明显结构", "observation": "本图与完整报告事实一致。",
            "interpretation": "此结构会影响判断重点。", "cross_chart_context": "应与其他证据和限制共同阅读。",
            "limitation": "目前不能建立因果结论。", "next_step": "使用额外资料继续验证。",
            "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}], "priority": "primary", "preferred_width": "full",
        }]}],
    }
    return compositor.compose_dashboard(
        manifest, synthesis, execution_ref=execution_ref, outputs={"chart": {"png_index": 0, "plotly_index": 1, "figure_spec": {"views": [{"view_id": "view-1", "title": "Chart"}]}}},
        uid="generic-report", title="Generic Report",
    )


def main() -> int:
    bridge = load_bridge()
    with tempfile.TemporaryDirectory() as tmp:
        setattr(bridge, "ARTIFACTS", bridge.ArtifactStore(Path(tmp) / "runs"))
        context = {"org_id": "1", "user_id": "report-check"}
        run_id = bridge.ARTIFACTS.create_run(context)
        execution_ref = bridge.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": [
            {"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "chart.png"},
            {"mime": {"application/json": json.dumps({"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {}})}, "display_name": "ml-plotly-chart.json"},
        ], "error": None})
        value = fixture(execution_ref)
        dashboard_run = bridge.ARTIFACTS.create_run(context)
        dashboard_ref = bridge.ARTIFACTS.write_json(context, dashboard_run, "dashboard", value)
        result = bridge.resolve_dashboard_refs({"dashboard": {"$dashboard_ref": dashboard_ref}, "_server_context": context})
        assert result["ok"], result
        assert result["evidence"]["resolved_assets"] == 1 and result["evidence"]["resolved_plotly"] == 1, result

        bad = copy.deepcopy(value)
        next(item for item in bad["panels"] if item.get("askO11yArtifactId")).pop("askO11yNarrative")
        denied = bridge.resolve_dashboard_refs({"dashboard": bad, "_server_context": context})
        assert not denied["ok"] and "opaque composed" in denied["error"], denied

    print("ok: generic report dashboard bridge write-gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
