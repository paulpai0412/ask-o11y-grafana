#!/usr/bin/env python3
"""TDD check for flow-agnostic LLM report synthesis and fact evidence."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "ml_report_contract.py"
    spec = importlib.util.spec_from_file_location("ml_report_contract_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def manifest(artifact: str, facts: dict) -> dict:
    return {
        "format": "fixture",
        "purpose": "回答本次分析问题。",
        "conclusion": "结论必须由证据支持。",
        "artifacts": [{"name": artifact + ".png", "caption": "fixture", "alt_text": "fixture"}],
        **facts,
    }


def synthesis(artifact: str, sections: list[tuple[str, str]]) -> dict:
    return {
        "format": "ask-o11y-report-synthesis-v1",
        "report_title": "本次分析报告",
        "thesis": "证据显示主要讯号集中在少数结构，仍需验证行动效果。",
        "thesis_evidence": [{"fact_ref": "metrics.signal", "format": "percent_1", "label": "核心讯号"}],
        "sections": [
            {
                "section_id": section_id,
                "title": title,
                "purpose": "根据整份报告回答此处最重要的问题。",
                "collapsed": False,
                "narrative_blocks": [],
                "panels": [{
                    "artifact_id": artifact,
                    "view_ids": ["view-1"],
                    "view_narratives": [{
                        "view_id": "view-1",
                        "headline": "此视图呈现主要讯号",
                        "data_observation": "资料模型显示群组之间存在明显差异。",
                        "visual_observation": "图中的柱形高度呈现清楚分层。",
                        "interpretation": "这个差异会影响报告的判断重点。",
                        "limitation": "目前不能由此建立因果关系。",
                        "next_step": "使用额外资料继续验证。",
                        "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
                    }],
                    "headline": "主要结构呈现明显差异",
                    "observation": "本图的差异与报告其他证据一致。",
                    "interpretation": "这项发现会影响后续判断重点。",
                    "cross_chart_context": "它应与其他图及资料限制一起阅读。",
                    "limitation": "目前证据不能证明因果关系。",
                    "next_step": "以额外资料或实验验证这项解释。",
                    "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}],
                    "priority": "primary",
                    "preferred_width": "full",
                }],
            }
            for section_id, title in sections
        ],
    }


def expect_reject(contract, manifest_value: dict, synthesis_value: dict, fragment: str) -> None:
    try:
        contract.validate_report_synthesis(manifest_value, synthesis_value)
    except ValueError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r}, got {exc}") from exc
        return
    raise AssertionError(f"expected rejection containing {fragment!r}")


def main() -> int:
    contract = load_module()
    fixtures = [
        (manifest("classification-evidence", {"metrics": {"signal": 0.4546}}), synthesis("classification-evidence", [("decision", "是否采取行动"), ("risk", "风险结构")])),
        (manifest("correlation-matrix", {"metrics": {"signal": 0.99}}), synthesis("correlation-matrix", [("redundancy", "欄位结构与重复讯号")])),
        (manifest("forecast-band", {"metrics": {"signal": 0.18}}), synthesis("forecast-band", [("history", "历史变化"), ("forecast", "未来区间"), ("uncertainty", "不确定性"), ("operations", "行动判断")])),
    ]
    section_shapes = []
    for manifest_value, synthesis_value in fixtures:
        catalog = contract.build_fact_catalog(manifest_value)
        assert catalog["metrics.signal"]["value"] in {0.4546, 0.99, 0.18}, catalog
        validated = contract.validate_report_synthesis(manifest_value, synthesis_value)
        section_shapes.append(len(validated["sections"]))
    assert section_shapes == [2, 1, 4], section_shapes

    base_manifest, base = fixtures[0]
    assert contract.validate_report_synthesis(base_manifest, base) == base, "legacy content must remain unchanged"
    minimal = copy.deepcopy(base)
    for section in minimal["sections"]:
        for key in ("collapsed", "narrative_blocks"):
            section.pop(key)
        for panel in section["panels"]:
            for key in ("view_narratives", "cross_chart_context", "next_step", "priority", "preferred_width"):
                panel.pop(key)
    before = copy.deepcopy(minimal)
    invalid = copy.deepcopy(minimal); invalid["sections"][0]["unexpected"] = {"nested": []}
    with patch.object(contract.copy, "deepcopy", side_effect=AssertionError("copied invalid nested input before validation")):
        expect_reject(contract, base_manifest, invalid, "shape")
    normalized = contract.validate_report_synthesis(base_manifest, minimal)
    assert minimal == before, "normalization mutated model input"
    plain = normalized["sections"][0]["panels"][0]
    assert plain["view_narratives"] == [] and plain["preferred_width"] == "full" and plain["priority"] == "supporting"
    assert "cross_chart_context" not in plain and "next_step" not in plain, "host must not invent narrative text"
    collapsed = normalized["sections"][0]["collapsed"]
    assert isinstance(collapsed, bool) and not collapsed
    assert normalized["sections"][0]["narrative_blocks"] == []
    for field in ("headline", "observation", "interpretation", "limitation", "evidence"):
        invalid = copy.deepcopy(minimal); invalid["sections"][0]["panels"][0].pop(field)
        expect_reject(contract, base_manifest, invalid, "shape")
    for field in ("cross_chart_context", "next_step"):
        invalid = copy.deepcopy(minimal); invalid["sections"][0]["panels"][0][field] = ""
        expect_reject(contract, base_manifest, invalid, field)
    focused = copy.deepcopy(base)
    focused_panel = focused["sections"][0]["panels"][0]
    focused_panel["view_ids"].append("view-2")
    focused_panel["view_narratives"][0].pop("next_step")
    focused_panel["view_narratives"][0].pop("visual_observation")
    focused_output = contract.validate_report_synthesis(base_manifest, focused)
    assert focused_output["sections"][0]["panels"][0]["view_narratives"][0]["visual_observation"] is None
    with_block = copy.deepcopy(base)
    with_block["sections"][0]["narrative_blocks"] = [{"block_id": "closing", "title": "综合判断", "body": "现有证据支持受控验证，行动前仍需核准关键假设。", "evidence": [{"fact_ref": "metrics.signal", "format": "percent_1"}], "priority": "primary"}]
    contract.validate_report_synthesis(base_manifest, with_block)
    bad_thesis = copy.deepcopy(base); bad_thesis["thesis_evidence"][0]["fact_ref"] = "missing"
    expect_reject(contract, base_manifest, bad_thesis, "fact")
    unknown_artifact = copy.deepcopy(base); unknown_artifact["sections"][0]["panels"][0]["artifact_id"] = "missing"
    expect_reject(contract, base_manifest, unknown_artifact, "artifact")
    unknown_fact = copy.deepcopy(base); unknown_fact["sections"][0]["panels"][0]["evidence"][0]["fact_ref"] = "missing.fact"
    expect_reject(contract, base_manifest, unknown_fact, "fact")
    missing_view = copy.deepcopy(base); missing_view["sections"][0]["panels"][0]["view_narratives"] = []
    contract.validate_report_synthesis(base_manifest, missing_view)  # panel explanation replaces repetitive view captions
    unknown_view = copy.deepcopy(base); unknown_view["sections"][0]["panels"][0]["view_narratives"][0]["view_id"] = "unknown"
    expect_reject(contract, base_manifest, unknown_view, "view_narratives")
    duplicated_view = copy.deepcopy(base); duplicated_view["sections"][0]["panels"][0]["view_narratives"].append(copy.deepcopy(duplicated_view["sections"][0]["panels"][0]["view_narratives"][0]))
    expect_reject(contract, base_manifest, duplicated_view, "view_narratives")
    hallucinated_number = copy.deepcopy(base); hallucinated_number["sections"][0]["panels"][0]["observation"] = "提升了 42%。"
    expect_reject(contract, base_manifest, hallucinated_number, "numeric")
    visual_number = copy.deepcopy(base); visual_number["sections"][0]["panels"][0]["view_narratives"][0]["visual_observation"] = "图中出现 42 个点。"
    expect_reject(contract, base_manifest, visual_number, "numeric")
    unsafe_html = copy.deepcopy(base); unsafe_html["sections"][0]["panels"][0]["next_step"] = "<script>alert(1)</script>"
    expect_reject(contract, base_manifest, unsafe_html, "unsafe")
    duplicate = copy.deepcopy(base); duplicate["sections"].append(copy.deepcopy(duplicate["sections"][0]))
    expect_reject(contract, base_manifest, duplicate, "section_id")

    source = (ROOT / "ml_report_contract.py").read_text()
    for forbidden in ("Telco", "Contract", "required_roles", "fixed_chart"):
        assert forbidden not in source, forbidden

    print("ok: flow-agnostic LLM report synthesis contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
