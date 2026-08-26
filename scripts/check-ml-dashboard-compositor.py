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
        "sections": [
            {"section_id": section_id, "title": title, "purpose": "回答本次报告在此处提出的问题。", "collapsed": collapsed, "panels": panels}
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
    shapes = []
    for index, (manifest, report) in enumerate(fixtures):
        outputs = {
            artifact_id: {
                "png_index": slot * 2, "plotly_index": slot * 2 + 1,
                **({"recommended_width": "full", "min_height": 16} if index == 0 and slot == 0 else {}),
            }
            for slot, artifact_id in enumerate(item["name"].removesuffix(".png") for item in manifest["artifacts"])
        }
        dashboard = compositor.compose_dashboard(
            manifest, report, execution_ref=f"artifact://run-{index}/sandbox-execution",
            outputs=outputs, uid=f"fixture-{index}", title=f"Fixture {index}",
        )
        rows = [item for item in dashboard["panels"] if item.get("type") == "row"]
        if [item["askO11ySectionId"] for item in rows] != [section["section_id"] for section in report["sections"]]:
            raise AssertionError("compositor changed the LLM-authored section order")
        flattened = []
        for item in dashboard["panels"]:
            flattened.append(item); flattened.extend(item.get("panels") or [])
        evidence_panels = [item for item in flattened if item.get("askO11yArtifactId")]
        assert all(item.get("askO11yNarrative", {}).get("evidence") for item in evidence_panels), evidence_panels
        if index == 0:
            promoted = next(item for item in evidence_panels if item["askO11yArtifactId"] == "class-view")
            assert promoted["gridPos"]["w"] == 24 and promoted["gridPos"]["h"] >= 16, promoted["gridPos"]
        assert "$asset_url_" in str(dashboard) and "$plotly_" in str(dashboard), dashboard
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
