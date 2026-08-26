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


def validate_report_synthesis(manifest: dict[str, Any], synthesis: dict[str, Any]) -> dict[str, Any]:
    """Validate LLM-authored flow/content against deterministic artifacts and facts."""
    if not isinstance(synthesis, dict) or set(synthesis) != {"format", "report_title", "thesis", "sections"}:
        raise ValueError("report synthesis shape is invalid")
    if synthesis.get("format") != REPORT_FORMAT:
        raise ValueError("report synthesis format is invalid")
    _safe_text(synthesis.get("report_title"), "report_title")
    _safe_text(synthesis.get("thesis"), "thesis")
    sections = synthesis.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ValueError("report synthesis sections are outside bounds")
    catalog = build_fact_catalog(manifest)
    artifacts = _artifact_ids(manifest)
    section_ids: set[str] = set()
    total_panels = 0
    for section_index, section in enumerate(sections):
        where = f"sections[{section_index}]"
        if not isinstance(section, dict) or set(section) != {"section_id", "title", "purpose", "collapsed", "panels"}:
            raise ValueError(f"{where} shape is invalid")
        section_id = _safe_text(section.get("section_id"), f"{where}.section_id", identifier=True)
        if section_id in section_ids:
            raise ValueError("report synthesis section_id must be unique")
        section_ids.add(section_id)
        _safe_text(section.get("title"), f"{where}.title")
        _safe_text(section.get("purpose"), f"{where}.purpose")
        if not isinstance(section.get("collapsed"), bool):
            raise ValueError(f"{where}.collapsed must be boolean")
        panels = section.get("panels")
        if not isinstance(panels, list):
            raise ValueError(f"{where}.panels must be an array")
        total_panels += len(panels)
        if total_panels > MAX_PANELS:
            raise ValueError("report synthesis panel count exceeds bound")
        for panel_index, panel in enumerate(panels):
            panel_where = f"{where}.panels[{panel_index}]"
            required = {"artifact_id", *TEXT_FIELDS, "evidence", "priority", "preferred_width"}
            if not isinstance(panel, dict) or set(panel) != required:
                raise ValueError(f"{panel_where} shape is invalid")
            artifact_id = _safe_text(panel.get("artifact_id"), f"{panel_where}.artifact_id", identifier=True)
            if artifact_id not in artifacts:
                raise ValueError(f"{panel_where} references unknown artifact")
            for field in TEXT_FIELDS:
                _safe_text(panel.get(field), f"{panel_where}.{field}")
            if panel.get("priority") not in PRIORITIES or panel.get("preferred_width") not in WIDTHS:
                raise ValueError(f"{panel_where} presentation hints are invalid")
            evidence = panel.get("evidence")
            if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_EVIDENCE:
                raise ValueError(f"{panel_where} evidence is outside bounds")
            seen_facts = set()
            for evidence_item in evidence:
                if not isinstance(evidence_item, dict) or set(evidence_item) != {"fact_ref", "format"}:
                    raise ValueError(f"{panel_where} evidence shape is invalid")
                fact_ref = evidence_item.get("fact_ref")
                if not isinstance(fact_ref, str) or fact_ref not in catalog:
                    raise ValueError(f"{panel_where} references unknown fact")
                if fact_ref in seen_facts or evidence_item.get("format") not in EVIDENCE_FORMATS:
                    raise ValueError(f"{panel_where} evidence is invalid")
                seen_facts.add(fact_ref)
    if total_panels < 1:
        raise ValueError("report synthesis requires at least one evidence panel")
    return copy.deepcopy(synthesis)
