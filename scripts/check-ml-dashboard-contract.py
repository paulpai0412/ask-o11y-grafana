#!/usr/bin/env python3
"""Self-check for flow-agnostic evidence-bound report dashboards."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ml_dashboard_compositor as compositor  # type: ignore[reportMissingImports]
import ml_dashboard_contract as contract  # type: ignore[reportMissingImports]


def dashboard() -> dict:
    manifest = {
        "format": "fixture", "purpose": "fixture", "conclusion": "fixture", "metrics": {"signal": 0.42},
        "artifacts": [{"name": "evidence.png", "caption": "fixture", "alt_text": "fixture"}],
    }
    synthesis = {
        "format": "ask-o11y-report-synthesis-v1", "report_title": "动态报告", "thesis": "证据支持当前解释，行动前仍需验证。",
        "thesis_evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
        "sections": [{
            "section_id": "custom-flow", "title": "由模型决定的主题", "purpose": "回答本次报告最重要的问题。", "collapsed": False, "narrative_blocks": [],
            "panels": [{
                "artifact_id": "evidence", "view_ids": ["view-1"],
                "view_narratives": [{"view_id": "view-1", "headline": "此视图呈现主要证据", "data_observation": "资料模型显示明确结构。", "visual_observation": "图形呈现清楚分层。", "interpretation": "这会影响判断重点。", "limitation": "目前不能建立因果结论。", "next_step": "使用额外资料验证。", "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}]}],
                "headline": "主要证据呈现明显结构", "observation": "本图与完整报告的事实一致。",
                "interpretation": "此结构会改变判断重点。", "cross_chart_context": "应与其他证据和限制一起阅读。",
                "limitation": "目前不能建立因果结论。", "next_step": "以额外资料继续验证。",
                "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}], "priority": "primary", "preferred_width": "full",
            }],
        }],
    }
    return compositor.compose_dashboard(
        manifest, synthesis, execution_ref="artifact://run/result", outputs={"evidence": {"png_index": 0, "plotly_index": 1, "figure_spec": {"views": [{"view_id": "view-1", "title": "Evidence"}]}}},
        uid="dynamic-preview", title="Dynamic Preview",
    )


def expect_reject(value: dict) -> None:
    try:
        contract.validate_preview_dashboard(value)
    except ValueError as exc:
        if not str(exc):
            raise AssertionError("dashboard rejection lacked a reason") from exc
        return
    raise AssertionError("unsafe report dashboard was accepted")


def main() -> int:
    value = dashboard()
    contract.validate_preview_dashboard(value)
    contract.validate_ml_dashboard_minimum(value)

    minimal = copy.deepcopy(value)
    panel = next(item for item in minimal["panels"] if item.get("askO11yArtifactId"))
    for field in ("cross_chart_context", "next_step"):
        panel["askO11yNarrative"].pop(field)
    panel["askO11yViewNarratives"] = []
    panel["options"]["narrative"] = copy.deepcopy(panel["askO11yNarrative"])
    panel["options"]["viewNarratives"] = []
    contract.validate_preview_dashboard(minimal)
    for field in ("headline", "observation", "interpretation", "limitation", "evidence"):
        invalid = copy.deepcopy(minimal)
        next(item for item in invalid["panels"] if item.get("askO11yArtifactId"))["askO11yNarrative"].pop(field)
        expect_reject(invalid)
    for mutation in ("duplicate-view", "unknown-field", "unknown-view-narrative", "empty-optional"):
        invalid = copy.deepcopy(minimal)
        target = next(item for item in invalid["panels"] if item.get("askO11yArtifactId"))
        if mutation == "duplicate-view":
            target["askO11yViewIds"] *= 2
        elif mutation == "unknown-field":
            target["askO11yNarrative"]["invented"] = "unexpected"
        elif mutation == "empty-optional":
            target["askO11yNarrative"]["next_step"] = ""
        else:
            target["askO11yViewNarratives"] = copy.deepcopy(next(item for item in value["panels"] if item.get("askO11yArtifactId"))["askO11yViewNarratives"])
            target["askO11yViewNarratives"][0]["view_id"] = "unknown"
        expect_reject(invalid)

    missing_tag = copy.deepcopy(value); missing_tag["tags"] = ["ask-o11y-preview"]
    expect_reject(missing_tag)
    missing_section = copy.deepcopy(value); next(item for item in missing_section["panels"] if item.get("type") == "row").pop("askO11ySectionId")
    expect_reject(missing_section)
    missing_narrative = copy.deepcopy(value); next(item for item in missing_narrative["panels"] if item.get("askO11yArtifactId")).pop("askO11yNarrative")
    expect_reject(missing_narrative)
    native_target = copy.deepcopy(value); native_target["panels"].append({"type": "timeseries", "targets": [{"refId": "A"}]})
    expect_reject(native_target)

    print("ok: flow-agnostic report dashboard contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
