"""Flow-agnostic LLM report synthesis contract over bounded deterministic facts."""
from __future__ import annotations

import copy
import math
import re
from typing import Any

REPORT_FORMAT = "ask-o11y-report-synthesis-v1"
MAX_FACTS = 1024
MAX_SECTIONS = 8
MAX_PANELS = 24
MAX_EVIDENCE = 8
MAX_TEXT = 600
FORBIDDEN_FACT_KEYS = {"raw_rows", "frame", "python_code", "credentials", "physical_path", "signed_url"}
TEXT_FIELDS = ("headline", "observation", "interpretation", "cross_chart_context", "limitation", "next_step")
EVIDENCE_FORMATS = {"auto", "integer", "number_1", "number_2", "percent_1"}
PRIORITIES = {"primary", "supporting", "technical"}
WIDTHS = {"full", "half"}
VIEW_TEXT_FIELDS = ("headline", "data_observation", "interpretation", "limitation", "next_step")


def _safe_segment(value: Any, fallback: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    return segment[:80] or fallback


def _list_segment(item: Any, index: int) -> str:
    if isinstance(item, dict):
        for key in ("id", "name", "feature", "label"):
            if item.get(key) not in (None, ""):
                return _safe_segment(item[key], str(index))
    return str(index)


def build_fact_catalog(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten bounded scalar manifest facts without exposing rows, paths, or credentials."""
    catalog: dict[str, dict[str, Any]] = {}

    def walk(value: Any, path: list[str]) -> None:
        if len(catalog) > MAX_FACTS:
            raise ValueError("manifest fact catalog exceeds bound")
        if value is None:
            return
        if isinstance(value, bool):
            fact_id = ".".join(path)
            catalog[fact_id] = {"value": value, "kind": "boolean", "label": " / ".join(path)}
            return
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("manifest fact is not numeric") from exc
            if not math.isfinite(number):
                raise ValueError("manifest fact is non-finite")
            fact_id = ".".join(path)
            catalog[fact_id] = {"value": value, "kind": "number", "label": " / ".join(path)}
            return
        if isinstance(value, str):
            if len(value) > MAX_TEXT or value.startswith("/") or "?token=" in value or value.startswith("http"):
                return
            fact_id = ".".join(path)
            catalog[fact_id] = {"value": value, "kind": "text", "label": " / ".join(path)}
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                if key_text in FORBIDDEN_FACT_KEYS or key_text.endswith("_path") or key_text.endswith("_url"):
                    continue
                walk(child, [*path, _safe_segment(key_text, "field")])
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, [*path, _list_segment(child, index)])

    for key, value in manifest.items():
        if key in {"artifacts", "narrative", "plain_language"} or key in FORBIDDEN_FACT_KEYS:
            continue
        walk(value, [_safe_segment(key, "section")])
    if len(catalog) > MAX_FACTS:
        raise ValueError("manifest fact catalog exceeds bound")
    return catalog


def _safe_text(value: Any, where: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError(f"{where} text is invalid")
    text = value.strip()
    lowered = text.lower()
    if any(token in lowered for token in ("<", ">", "http://", "https://", "javascript:", "script")):
        raise ValueError(f"{where} contains unsafe text")
    if identifier:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text):
            raise ValueError(f"{where} identifier is invalid")
    elif re.search(r"\d", text):
        raise ValueError(f"{where} contains unsupported numeric text; use evidence facts")
    return text


def _artifact_ids(manifest: dict[str, Any]) -> set[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("manifest artifacts are required")
    output = set()
    for item in artifacts:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.endswith(".png") and "/" not in name:
            output.add(name.removesuffix(".png"))
    return output


def _validate_evidence(evidence: Any, catalog: dict[str, dict[str, Any]], where: str) -> None:
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_EVIDENCE:
        raise ValueError(f"{where} evidence is outside bounds")
    seen_facts = set()
    for evidence_item in evidence:
        if not isinstance(evidence_item, dict) or set(evidence_item) not in ({"fact_ref", "format"}, {"fact_ref", "format", "label"}):
            raise ValueError(f"{where} evidence shape is invalid")
        if "label" in evidence_item:
            _safe_text(evidence_item["label"], f"{where} evidence label")
        fact_ref = evidence_item.get("fact_ref")
        if not isinstance(fact_ref, str) or fact_ref not in catalog:
            raise ValueError(f"{where} references unknown fact")
        if fact_ref in seen_facts or evidence_item.get("format") not in EVIDENCE_FORMATS:
            raise ValueError(f"{where} evidence is invalid")
        seen_facts.add(fact_ref)


def validate_report_synthesis(manifest: dict[str, Any], synthesis: dict[str, Any]) -> dict[str, Any]:
    """Validate LLM-authored flow/content against deterministic artifacts and facts."""
    if not isinstance(synthesis, dict) or set(synthesis) != {"format", "report_title", "thesis", "thesis_evidence", "sections"}:
        raise ValueError("report synthesis shape is invalid")
    if synthesis.get("format") != REPORT_FORMAT:
        raise ValueError("report synthesis format is invalid")
    _safe_text(synthesis.get("report_title"), "report_title")
    _safe_text(synthesis.get("thesis"), "thesis")
    catalog = build_fact_catalog(manifest)
    _validate_evidence(synthesis.get("thesis_evidence"), catalog, "thesis")
    sections = synthesis.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ValueError("report synthesis sections are outside bounds")
    artifacts = _artifact_ids(manifest)
    section_ids: set[str] = set()
    total_panels = 0
    for section_index, section in enumerate(sections):
        where = f"sections[{section_index}]"
        if not isinstance(section, dict) or set(section) != {"section_id", "title", "purpose", "collapsed", "narrative_blocks", "panels"}:
            raise ValueError(f"{where} shape is invalid")
        section_id = _safe_text(section.get("section_id"), f"{where}.section_id", identifier=True)
        if section_id in section_ids:
            raise ValueError("report synthesis section_id must be unique")
        section_ids.add(section_id)
        _safe_text(section.get("title"), f"{where}.title")
        _safe_text(section.get("purpose"), f"{where}.purpose")
        if not isinstance(section.get("collapsed"), bool):
            raise ValueError(f"{where}.collapsed must be boolean")
        narrative_blocks = section.get("narrative_blocks")
        if not isinstance(narrative_blocks, list) or len(narrative_blocks) > 8:
            raise ValueError(f"{where}.narrative_blocks are outside bounds")
        block_ids = set()
        for block_index, block in enumerate(narrative_blocks):
            block_where = f"{where}.narrative_blocks[{block_index}]"
            if not isinstance(block, dict) or set(block) != {"block_id", "title", "body", "evidence", "priority"}:
                raise ValueError(f"{block_where} shape is invalid")
            block_id = _safe_text(block.get("block_id"), f"{block_where}.block_id", identifier=True)
            if block_id in block_ids:
                raise ValueError(f"{where} narrative block ids must be unique")
            block_ids.add(block_id)
            _safe_text(block.get("title"), f"{block_where}.title")
            _safe_text(block.get("body"), f"{block_where}.body")
            _validate_evidence(block.get("evidence"), catalog, block_where)
            if block.get("priority") not in PRIORITIES:
                raise ValueError(f"{block_where}.priority is invalid")
        panels = section.get("panels")
        if not isinstance(panels, list):
            raise ValueError(f"{where}.panels must be an array")
        total_panels += len(panels)
        if total_panels > MAX_PANELS:
            raise ValueError("report synthesis panel count exceeds bound")
        for panel_index, panel in enumerate(panels):
            panel_where = f"{where}.panels[{panel_index}]"
            required = {"artifact_id", "view_ids", "view_narratives", *TEXT_FIELDS, "evidence", "priority", "preferred_width"}
            if not isinstance(panel, dict) or set(panel) != required:
                raise ValueError(f"{panel_where} shape is invalid")
            artifact_id = _safe_text(panel.get("artifact_id"), f"{panel_where}.artifact_id", identifier=True)
            if artifact_id not in artifacts:
                raise ValueError(f"{panel_where} references unknown artifact")
            view_ids = panel.get("view_ids")
            if not isinstance(view_ids, list) or not 1 <= len(view_ids) <= 12 or len(set(view_ids)) != len(view_ids):
                raise ValueError(f"{panel_where} view_ids are outside bounds")
            for view_id in view_ids:
                _safe_text(view_id, f"{panel_where}.view_id", identifier=True)
            view_narratives = panel.get("view_narratives")
            if not isinstance(view_narratives, list) or len(view_narratives) != len(view_ids):
                raise ValueError(f"{panel_where} view_narratives must cover every selected view")
            narrative_ids = []
            for narrative_index, view_narrative in enumerate(view_narratives):
                narrative_where = f"{panel_where}.view_narratives[{narrative_index}]"
                required_view = {"view_id", *VIEW_TEXT_FIELDS, "visual_observation", "evidence"}
                if not isinstance(view_narrative, dict) or set(view_narrative) != required_view:
                    raise ValueError(f"{narrative_where} shape is invalid")
                narrative_id = _safe_text(view_narrative.get("view_id"), f"{narrative_where}.view_id", identifier=True)
                narrative_ids.append(narrative_id)
                for field in VIEW_TEXT_FIELDS:
                    _safe_text(view_narrative.get(field), f"{narrative_where}.{field}")
                visual_observation = view_narrative.get("visual_observation")
                if visual_observation is not None:
                    _safe_text(visual_observation, f"{narrative_where}.visual_observation")
                _validate_evidence(view_narrative.get("evidence"), catalog, narrative_where)
            if len(set(narrative_ids)) != len(narrative_ids) or set(narrative_ids) != set(view_ids):
                raise ValueError(f"{panel_where} view_narratives do not match selected view_ids")
            for field in TEXT_FIELDS:
                _safe_text(panel.get(field), f"{panel_where}.{field}")
            if panel.get("priority") not in PRIORITIES or panel.get("preferred_width") not in WIDTHS:
                raise ValueError(f"{panel_where} presentation hints are invalid")
            _validate_evidence(panel.get("evidence"), catalog, panel_where)
    if total_panels < 1:
        raise ValueError("report synthesis requires at least one evidence panel")
    return copy.deepcopy(synthesis)
