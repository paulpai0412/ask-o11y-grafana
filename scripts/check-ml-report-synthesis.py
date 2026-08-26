#!/usr/bin/env python3
"""TDD check for flow-agnostic LLM report synthesis and fact evidence."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

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
        "sections": [
            {
                "section_id": section_id,
                "title": title,
                "purpose": "根据整份报告回答此处最重要的问题。",
                "collapsed": False,
                "panels": [{
                    "artifact_id": artifact,
                    "view_ids": ["view-1"],
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
    unknown_artifact = copy.deepcopy(base); unknown_artifact["sections"][0]["panels"][0]["artifact_id"] = "missing"
    expect_reject(contract, base_manifest, unknown_artifact, "artifact")
    unknown_fact = copy.deepcopy(base); unknown_fact["sections"][0]["panels"][0]["evidence"][0]["fact_ref"] = "missing.fact"
    expect_reject(contract, base_manifest, unknown_fact, "fact")
    hallucinated_number = copy.deepcopy(base); hallucinated_number["sections"][0]["panels"][0]["observation"] = "提升了 42%。"
    expect_reject(contract, base_manifest, hallucinated_number, "numeric")
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
