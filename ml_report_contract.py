"""Flow-agnostic LLM report synthesis contract over bounded deterministic facts."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any

_plotly_module: Any = sys.modules.get("ml_plotly_contract")
if _plotly_module is None:
    _plotly_spec = importlib.util.spec_from_file_location("ml_plotly_contract", Path(__file__).with_name("ml_plotly_contract.py"))
    if _plotly_spec is None or _plotly_spec.loader is None:
        raise ImportError("cannot load ml_plotly_contract")
    _plotly_module = importlib.util.module_from_spec(_plotly_spec)
    sys.modules[_plotly_spec.name] = _plotly_module
    _plotly_spec.loader.exec_module(_plotly_module)
ml_plotly_contract: Any = _plotly_module

REPORT_FORMAT = "ask-o11y-report-synthesis-v1"
REPORT_SOURCE_FORMAT = "ask-o11y-report-source-v1"
REPORT_MANIFEST_FORMAT = "ask-o11y-report-manifest-v1"
MAX_FACTS = 1024
MAX_REPORT_FACTS = 64
MAX_REPORT_ARTIFACTS = 16
MAX_REPORT_TEXT = 600
MAX_REPORT_OUTPUT_NAME = 200
MAX_REPORT_PNG_BYTES = 4 * 1024 * 1024
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


def _report_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REPORT_TEXT:
        raise ValueError(f"{where} text is invalid")
    text = value.strip()
    lowered = text.lower()
    if any(token in lowered for token in ("<", ">", "http://", "https://", "javascript:", "?token=")) or text.startswith("/"):
        raise ValueError(f"{where} contains unsafe content")
    return text


def _report_name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_REPORT_OUTPUT_NAME or "/" in value or "\\" in value:
        raise ValueError(f"{where} output name is invalid")
    return value


def _report_fact_value(value: Any, kind: str, where: str) -> Any:
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{where} fact value is invalid")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where} fact value is invalid")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"{where} fact value is invalid")
        return value
    if kind == "text":
        return _report_text(value, f"{where} fact")
    raise ValueError(f"{where} fact kind is invalid")


def _validate_report_facts(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise ValueError("report facts are required")
    if len(value) > MAX_REPORT_FACTS:
        raise ValueError("report facts exceed bound")
    facts: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key):
            raise ValueError("report fact id is invalid")
        where = f"facts.{key}"
        if not isinstance(item, dict) or set(item) != {"kind", "label", "value"}:
            raise ValueError(f"{where} fact is invalid")
        kind = item.get("kind")
        if kind not in {"boolean", "number", "text"}:
            raise ValueError(f"{where} fact kind is invalid")
        facts[key] = {"kind": kind, "label": _report_text(item.get("label"), f"{where}.label"), "value": _report_fact_value(item.get("value"), kind, where)}
    return facts


def _validate_png(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("png output is not base64 text")
    try:
        payload = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("png output is invalid base64") from exc
    if len(payload) > MAX_REPORT_PNG_BYTES or len(payload) < 33 or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise ValueError("png output is invalid")
    width, height = struct.unpack(">II", payload[16:24])
    if not width or not height:
        raise ValueError("png output dimensions are invalid")
    return payload


def _mime_value(result: Any, mime_type: str) -> str | None:
    mime = result.get("mime") if isinstance(result, dict) else None
    value = mime.get(mime_type) if isinstance(mime, dict) else None
    return value if isinstance(value, str) else None


def _is_plotly_output(result: Any) -> bool:
    if _mime_value(result, "application/vnd.plotly.v1+json") is not None:
        return True
    payload = _mime_value(result, "application/json")
    if payload is None:
        return False
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict) and set(value) == {"data", "layout", "config"}


def normalize_report_manifest(*, execution_ref: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a bounded Host-owned manifest from captured execution outputs."""
    if not isinstance(execution_ref, str) or not execution_ref.startswith("artifact://"):
        raise ValueError("execution_ref is invalid")
    if not isinstance(results, list) or not results:
        raise ValueError("report results are required")
    names: dict[str, list[int]] = {}
    parsed_json: list[tuple[int, dict[str, Any]]] = []
    for index, result in enumerate(results):
        name = result.get("display_name") if isinstance(result, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("report output name is invalid")
        names.setdefault(name, []).append(index)
        payload = _mime_value(result, "application/json")
        if payload is not None:
            try:
                value = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError("report JSON output is invalid") from exc
            if isinstance(value, dict):
                parsed_json.append((index, value))
    duplicate_names = [name for name, indexes in names.items() if len(indexes) > 1]
    if duplicate_names:
        raise ValueError("ambiguous report output name")
    sources = [(index, value) for index, value in parsed_json if value.get("format") == REPORT_SOURCE_FORMAT]
    if len(sources) > 1:
        raise ValueError("report source format is invalid")
    if not sources:
        raise ValueError("report source format is required")
    source_index, source = sources[0]
    allowed_source_keys = {"format", "purpose", "conclusion", "facts", "artifacts"}
    if set(source) != allowed_source_keys:
        raise ValueError("report source contains unsupported fields")
    purpose = _report_text(source.get("purpose"), "report purpose")
    conclusion = _report_text(source.get("conclusion"), "report conclusion")
    facts = _validate_report_facts(source.get("facts"))
    source_artifacts = source.get("artifacts")
    if not isinstance(source_artifacts, list) or not source_artifacts:
        raise ValueError("report artifact list is required")
    if len(source_artifacts) > MAX_REPORT_ARTIFACTS:
        raise ValueError("report artifacts exceed bound")
    declared: set[str] = set()
    artifact_ids: set[str] = set()
    normalized_artifacts: list[dict[str, Any]] = []
    for item in source_artifacts:
        if not isinstance(item, dict):
            raise ValueError("report artifact is invalid")
        allowed = {"artifact_id", "fact_refs", "plotly_output_name", "png_output_name"}
        if not set(item).issubset(allowed):
            raise ValueError("report artifact contains unsupported fields")
        artifact_id = item.get("artifact_id")
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", artifact_id):
            raise ValueError("report artifact id is invalid")
        if artifact_id in artifact_ids:
            raise ValueError("duplicate report artifact id")
        artifact_ids.add(artifact_id)
        fact_refs = item.get("fact_refs")
        if not isinstance(fact_refs, list) or not 1 <= len(fact_refs) <= 8 or any(ref not in facts for ref in fact_refs):
            raise ValueError("report artifact fact refs are invalid")
        plotly_name = item.get("plotly_output_name")
        png_name = item.get("png_output_name")
        if plotly_name is None and png_name is None:
            raise ValueError("report artifact has no output")
        if plotly_name is not None:
            plotly_name = _report_name(plotly_name, f"artifact {artifact_id}")
            if plotly_name in declared:
                raise ValueError("duplicate report artifact output")
            declared.add(plotly_name)
        if png_name is not None:
            png_name = _report_name(png_name, f"artifact {artifact_id}")
            if png_name in declared:
                raise ValueError("duplicate report artifact output")
            declared.add(png_name)
        normalized_artifacts.append({"artifact_id": artifact_id, "fact_refs": list(fact_refs), "plotly_output_name": plotly_name, "png_output_name": png_name})
    output_indexes = {name: indexes[0] for name, indexes in names.items()}
    for name, index in output_indexes.items():
        if index == source_index:
            continue
        result = results[index]
        if _is_plotly_output(result) or _mime_value(result, "image/png") is not None:
            if name not in declared:
                raise ValueError("orphan report output")
    manifest_artifacts: list[dict[str, Any]] = []
    for item in normalized_artifacts:
        plotly_name = item["plotly_output_name"]
        png_name = item["png_output_name"]
        if plotly_name is not None:
            if plotly_name not in output_indexes:
                raise ValueError("report artifact output is missing")
            plotly_mime_type = "application/vnd.plotly.v1+json"
            figure_payload = _mime_value(results[output_indexes[plotly_name]], plotly_mime_type)
            if figure_payload is None:
                plotly_mime_type = "application/json"
                figure_payload = _mime_value(results[output_indexes[plotly_name]], plotly_mime_type)
            if figure_payload is None:
                raise ValueError("report Plotly output is invalid")
            try:
                figure = json.loads(figure_payload)
            except json.JSONDecodeError as exc:
                raise ValueError("report Plotly output is invalid") from exc
            clean_figure = ml_plotly_contract.sanitize_figure(figure)
            render = {"mode": "plotly", "output_index": output_indexes[plotly_name], "mime_type": plotly_mime_type, "sha256": hashlib.sha256(json.dumps(clean_figure, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()}
            if png_name is not None:
                if png_name not in output_indexes:
                    raise ValueError("report artifact output is missing")
                paired_png = _mime_value(results[output_indexes[png_name]], "image/png")
                if paired_png is None:
                    raise ValueError("report paired PNG output is invalid")
                encoded_png = _validate_png(paired_png)
                render["png_output_index"] = output_indexes[png_name]
                render["png_sha256"] = hashlib.sha256(encoded_png).hexdigest()
        else:
            if png_name not in output_indexes:
                raise ValueError("report artifact output is missing")
            png_payload = _mime_value(results[output_indexes[png_name]], "image/png")
            if png_payload is None:
                raise ValueError("report PNG output is invalid")
            encoded = _validate_png(png_payload)
            render = {"mode": "image", "output_index": output_indexes[png_name], "mime_type": "image/png", "sha256": hashlib.sha256(encoded).hexdigest()}
        manifest_artifacts.append({"artifact_id": item["artifact_id"], "fact_refs": item["fact_refs"], "render": render})
    manifest = {"format": REPORT_MANIFEST_FORMAT, "execution_ref": execution_ref, "source": {"format": REPORT_SOURCE_FORMAT, "output_index": source_index, "display_name": results[source_index]["display_name"]}, "purpose": purpose, "conclusion": conclusion, "facts": facts, "artifacts": manifest_artifacts}
    return manifest


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
        if key in {"artifacts", "narrative", "plain_language", "source", "execution_ref", "format"} or key in FORBIDDEN_FACT_KEYS:
            continue
        if key == "facts" and isinstance(value, dict):
            for fact_key, fact in value.items():
                if isinstance(fact, dict) and set(fact) == {"kind", "label", "value"} and fact.get("kind") in {"boolean", "number", "text"}:
                    fact_id = f"facts.{_safe_segment(fact_key, 'fact')}"
                    catalog[fact_id] = {"value": fact["value"], "kind": fact["kind"], "label": str(fact["label"])}
                else:
                    walk(fact, ["facts", _safe_segment(fact_key, "fact")])
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
        if not isinstance(item, dict):
            continue
        artifact_id = item.get("artifact_id")
        if isinstance(artifact_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", artifact_id):
            output.add(artifact_id)
            continue
        name = item.get("name")
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
