#!/usr/bin/env python3
"""TDD check for the flow-agnostic evidence-bound dashboard compositor."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_manifest(artifact_ids: list[str], signal: float) -> dict:
    return {
        "format": "fixture", "purpose": "fixture", "conclusion": "fixture",
        "metrics": {"signal": signal},
        "artifacts": [{"name": artifact_id + ".png", "caption": "fixture", "alt_text": "fixture"} for artifact_id in artifact_ids],
    }


def panel(artifact_id: str, width: str = "full") -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "view_ids": ["view-1"],
        "view_narratives": [{
            "view_id": "view-1", "headline": "此视图呈现主要差异",
            "data_observation": "资料模型显示群组之间存在明显差异。", "visual_observation": "图中的形状呈现清楚分层。",
            "interpretation": "此差异会影响判断重点。", "limitation": "目前不能建立因果结论。",
            "next_step": "使用额外资料继续验证。", "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
        }],
        "headline": "主要证据呈现值得关注的结构",
        "observation": "本图显示的变化与整份报告一致。",
        "interpretation": "这项结构会影响后续判断重点。",
        "cross_chart_context": "应与其他证据和资料限制一起阅读。",
        "limitation": "目前不能据此建立因果结论。",
        "next_step": "使用额外资料或实验继续验证。",
        "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
        "priority": "primary", "preferred_width": width,
    }


def synthesis(sections: list[tuple[str, str, bool, list[dict[str, Any]]]]) -> dict[str, Any]:
    return {
        "format": "ask-o11y-report-synthesis-v1", "report_title": "动态分析报告",
        "thesis": "整份证据支持当前解释，但行动前仍需验证。",
        "thesis_evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
        "sections": [
            {"section_id": section_id, "title": title, "purpose": "回答本次报告在此处提出的问题。", "collapsed": collapsed, "narrative_blocks": [], "panels": panels}
            for section_id, title, collapsed, panels in sections
        ],
    }


def main() -> int:
    compositor = load("dashboard_compositor_check", ROOT / "ml_dashboard_compositor.py")
    fixtures = [
        (make_manifest(["class-view", "errors"], 0.4546), synthesis([("decision", "行动判断", False, [panel("class-view", "half")]), ("failure", "错误结构", True, [panel("errors")])])),
        (make_manifest(["matrix"], 0.99), synthesis([("redundancy", "重复讯号", False, [panel("matrix")])])),
        (make_manifest(["history", "future"], 0.18), synthesis([("past", "历史", False, [panel("history")]), ("range", "未来区间", False, [panel("future")]), ("risk", "风险", True, [])])),
    ]
    fixtures[2][1]["sections"][2]["narrative_blocks"] = [{
        "block_id": "closing", "title": "综合判断", "body": "现有证据支持受控验证，行动前仍需确认关键假设。",
        "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}], "priority": "primary",
    }]
    shapes = []
    for index, (manifest, report) in enumerate(fixtures):
        outputs = {}
        for slot, artifact_id in enumerate(item["name"].removesuffix(".png") for item in manifest["artifacts"]):
            output: dict[str, Any] = {"png_index": slot * 2}
            if index != 1:
                output["plotly_index"] = slot * 2 + 1
            if index == 0 and slot == 0:
                output.update({"recommended_width": "full", "min_height": 16})
            outputs[artifact_id] = output
        dashboard = compositor.compose_dashboard(
            manifest, report, execution_ref=f"artifact://run-{index}/sandbox-execution",
            outputs=outputs, uid=f"fixture-{index}", title=f"Fixture {index}",
        )
        assert dashboard["panels"][0].get("askO11yReportThesis") == report["thesis"], dashboard["panels"][0]
        rows = [item for item in dashboard["panels"] if item.get("type") == "row"]
        if [item["askO11ySectionId"] for item in rows] != [section["section_id"] for section in report["sections"]]:
            raise AssertionError("compositor changed the LLM-authored section order")
        assert all(row.get("askO11ySectionPurpose") for row in rows), rows
        assert not any(item.get("type") == "text" and item.get("title") in {section["title"] for section in report["sections"]} and not item.get("askO11yReportThesis") and not item.get("askO11yNarrativeBlock") for item in dashboard["panels"]), "section purpose was duplicated as a text panel"
        flattened = []
        for item in dashboard["panels"]:
            flattened.append(item); flattened.extend(item.get("panels") or [])
        evidence_panels = [item for item in flattened if item.get("askO11yArtifactId")]
        assert all(item.get("askO11yNarrative", {}).get("evidence") for item in evidence_panels), evidence_panels
        plotly_panels = [item for item in evidence_panels if item.get("type") == "asko11y-plotly-panel"]
        assert all((item.get("options") or {}).get("selectedViewIds") == ["view-1"] for item in plotly_panels), plotly_panels
        assert all(item.get("askO11yViewNarratives", [{}])[0].get("data_observation") == "资料模型显示群组之间存在明显差异。" for item in evidence_panels), evidence_panels
        if index == 2:
            block = next(item for item in flattened if item.get("askO11yNarrativeBlock"))
            assert "现有证据支持受控验证" in block["options"]["content"] and block["askO11yNarrativeBlock"]["evidence"][0]["display"] == "18.0%", block
        if index == 0:
            promoted = next(item for item in evidence_panels if item["askO11yArtifactId"] == "class-view")
            assert promoted["gridPos"]["w"] == 24 and promoted["gridPos"]["h"] >= 16, promoted["gridPos"]
        if index == 1:
            options = evidence_panels[0]["options"]
            assert options["renderMode"] == "image", options
            assert "content" not in options, "image evidence must remain in the Plotly plugin, not a text panel"
            assert options["narrative"]["observation"] == "本图显示的变化与整份报告一致。"
        assert "$asset_url_" in str(dashboard), dashboard
        if any("plotly_index" in output for output in outputs.values()):
            assert "$plotly_" in str(dashboard), dashboard
        shapes.append((len(rows), len(evidence_panels)))
    if shapes != [(2, 2), (1, 1), (3, 2)]:
        raise AssertionError(f"unexpected dynamic dashboard shapes: {shapes}")

    source = (ROOT / "ml_dashboard_compositor.py").read_text()
    for forbidden in ("Telco", "Contract", "feature_importance", "required_roles", "五幕"):
        assert forbidden not in source, forbidden

    print("ok: flow-agnostic generic dashboard compositor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
