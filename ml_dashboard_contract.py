"""Flow-agnostic safety validator for evidence-bound Grafana report dashboards."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from ml_plotly_contract import contains_markup

FORBIDDEN_KEYS = {"raw_rows", "frame", "python_code", "credentials", "physical_path", "signed_url"}
PREVIEW_TAG = "ask-o11y-preview"
REPORT_TAG = "ask-o11y-report"
PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"
MAX_SECTIONS = 8
MAX_PANELS = 24
NARRATIVE_FIELDS = {"headline", "observation", "interpretation", "cross_chart_context", "limitation", "next_step", "evidence"}


class _NarrativeHTML(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"div", "p", "span", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "ul", "ol", "li", "br", "hr", "blockquote", "code", "pre", "table", "thead", "tbody", "tr", "td", "th"}:
            raise ValueError("text panels are narrative-only; images require the Plotly plugin")
        for name, value in attrs:
            if name == "style":
                if not re.fullmatch(r"[A-Za-z0-9\s:;#.,%+\-]*", value or ""):
                    raise ValueError("text panel style contains an unsafe image or active binding")
            elif name not in {"class", "title", "role", "aria-label", "lang", "dir", "colspan", "rowspan"}:
                raise ValueError("text panel attribute is not narrative-only")


def validate_narrative_content(content: Any) -> None:
    if not isinstance(content, str) or "![" in content:
        raise ValueError("text panels are narrative-only; images require the Plotly plugin")
    parser = _NarrativeHTML(convert_charrefs=True)
    parser.feed(content)
    parser.close()


def _panels(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for panel in value:
        flattened.append(panel)
        nested = panel.get("panels")
        if isinstance(nested, list):
            flattened.extend(_panels([item for item in nested if isinstance(item, dict)]))
    return flattened


def _story_rows(top_level: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [panel for panel in top_level if panel.get("type") == "row"]
    if not 1 <= len(rows) <= MAX_SECTIONS:
        raise ValueError("report dashboard section count is outside bounds")
    section_ids: set[str] = set()
    for row in rows:
        section_id = row.get("askO11ySectionId")
        if not isinstance(section_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", section_id):
            raise ValueError("report row requires a safe askO11ySectionId")
        if section_id in section_ids:
            raise ValueError("report section ids must be unique")
        section_ids.add(section_id)
        if not isinstance(row.get("collapsed"), bool):
            raise ValueError("report row collapsed state is required")
        _safe_text(row.get("askO11ySectionPurpose"), "report section purpose")
    return rows


def _safe_text(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 800:
        raise ValueError(f"{where} is invalid")
    lowered = value.lower()
    if contains_markup(value) or any(token in lowered for token in ("javascript:", "http://", "https://")):
        raise ValueError(f"{where} contains unsafe text")


def _validate_display_evidence(evidence: Any, where: str) -> None:
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"{where} evidence is required")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"fact_ref", "label", "display"}:
            raise ValueError(f"{where} evidence shape is invalid")
        for key in ("fact_ref", "label", "display"):
            _safe_text(str(item.get(key) or ""), f"{where}.{key}")


def _validate_narrative(panel: dict[str, Any]) -> None:
    narrative = panel.get("askO11yNarrative")
    required = NARRATIVE_FIELDS - {"cross_chart_context", "next_step"}
    if not isinstance(narrative, dict) or not required <= set(narrative) <= NARRATIVE_FIELDS:
        raise ValueError("evidence panel requires an evidence-bound askO11yNarrative")
    for key in set(narrative) - {"evidence"}:
        _safe_text(narrative.get(key), f"narrative.{key}")
    evidence = narrative.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        raise ValueError("panel narrative evidence is outside bounds")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"fact_ref", "label", "display"}:
            raise ValueError("panel narrative evidence shape is invalid")
        for key in ("fact_ref", "label", "display"):
            _safe_text(str(item.get(key) or ""), f"narrative.evidence.{key}")


def _validate_view_narratives(panel: dict[str, Any], view_ids: list[str]) -> None:
    narratives = panel.get("askO11yViewNarratives")
    if not isinstance(narratives, list) or len(narratives) > len(view_ids):
        raise ValueError("view narratives exceed selected views")
    narrative_ids = []
    required = {"view_id", "headline", "data_observation", "visual_observation", "interpretation", "limitation", "evidence"}
    for narrative in narratives:
        if not isinstance(narrative, dict) or not required <= set(narrative) <= required | {"next_step"}:
            raise ValueError("view narrative shape is invalid")
        narrative_ids.append(narrative.get("view_id"))
        for key in set(narrative) - {"view_id", "visual_observation", "evidence"}:
            _safe_text(narrative.get(key), f"view narrative {key}")
        if narrative.get("visual_observation") is not None:
            _safe_text(narrative["visual_observation"], "view narrative visual observation")
        evidence = narrative.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("view narrative evidence is required")
    if not set(narrative_ids).issubset(view_ids) or len(set(narrative_ids)) != len(narrative_ids):
        raise ValueError("view narratives do not match selected view ids")


def _walk(value: Any, key: str = "") -> None:
    if key in FORBIDDEN_KEYS or key.endswith("_path") or key.endswith("_url"):
        raise ValueError(f"forbidden dashboard key: {key}")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _walk(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            _walk(child, key)
    elif isinstance(value, str) and ("?token=" in value or value.startswith("/tmp/")):
        raise ValueError("dashboard contains a resolved URL or physical path before bridge resolution")


def _validate_dashboard(dashboard: dict[str, Any], *, require_uid: bool) -> None:
    if require_uid and (not isinstance(dashboard.get("uid"), str) or not dashboard["uid"].strip()):
        raise ValueError("dashboard requires a stable UID")
    tags = dashboard.get("tags")
    if not isinstance(tags, list) or PREVIEW_TAG not in tags or REPORT_TAG not in tags:
        raise ValueError("report dashboard must remain a tagged Preview")
    top_level = dashboard.get("panels")
    if not isinstance(top_level, list) or not top_level:
        raise ValueError("dashboard panels are required")
    _story_rows([item for item in top_level if isinstance(item, dict)])
    thesis_panels = [item for item in top_level if isinstance(item, dict) and item.get("askO11yReportThesis") is not None]
    if len(thesis_panels) != 1 or thesis_panels[0].get("type") != "text":
        raise ValueError("report dashboard requires one LLM-authored thesis panel")
    question_panel = thesis_panels[0]
    if question_panel.get("askO11yBusinessQuestionSource") not in {"host_retained_plan", "retained_question_unverified"}:
        raise ValueError("report dashboard requires a recognized retained-question source")
    _safe_text(question_panel.get("askO11yBusinessQuestion"), "business question")
    _safe_text(thesis_panels[0].get("askO11yReportThesis"), "report thesis")
    _validate_display_evidence(thesis_panels[0].get("askO11yThesisEvidence"), "report thesis")
    flattened = _panels([item for item in top_level if isinstance(item, dict)])
    if len(flattened) > MAX_PANELS:
        raise ValueError("dashboard panel count exceeds bound")
    if any(panel.get("targets") for panel in flattened):
        raise ValueError("analysis dashboard may contain evidence/text panels only")
    if any(panel.get("type") not in {"row", "text", PLOTLY_PLUGIN_ID} for panel in flattened):
        raise ValueError("report dashboard contains an unsupported panel type")

    narrative_blocks = [panel for panel in flattened if panel.get("askO11yNarrativeBlock") is not None]
    for panel in narrative_blocks:
        block = panel["askO11yNarrativeBlock"]
        if not isinstance(block, dict) or set(block) != {"block_id", "title", "body", "evidence", "priority"}:
            raise ValueError("narrative block shape is invalid")
        _safe_text(block.get("title"), "narrative block title")
        _safe_text(block.get("body"), "narrative block body")
        _validate_display_evidence(block.get("evidence"), "narrative block")
    for panel in flattened:
        if panel.get("type") == "text":
            validate_narrative_content((panel.get("options") or {}).get("content", ""))
    evidence_panels = [panel for panel in flattened if panel.get("askO11yArtifactId") is not None]
    if not evidence_panels:
        raise ValueError("report dashboard requires at least one evidence panel")
    for panel in evidence_panels:
        if panel.get("type") != PLOTLY_PLUGIN_ID:
            raise ValueError("all image evidence must use the Plotly plugin")
        artifact_id = panel.get("askO11yArtifactId")
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", artifact_id):
            raise ValueError("evidence panel artifact id is invalid")
        view_ids = panel.get("askO11yViewIds")
        if not isinstance(view_ids, list) or not 1 <= len(view_ids) <= 12 or any(not isinstance(view_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", view_id) for view_id in view_ids) or len(set(view_ids)) != len(view_ids):
            raise ValueError("evidence panel view ids are invalid")
        _validate_narrative(panel)
        _validate_view_narratives(panel, view_ids)
        panel_placeholders: set[str] = set()
        bindings = panel.get("askO11yAssetBindings") or []
        if not isinstance(bindings, list):
            raise ValueError("asset bindings must be an array")
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) not in ({"placeholder", "$execution_ref", "output_index"}, {"placeholder", "$report_manifest_ref", "artifact_id"}):
                raise ValueError("asset binding shape is invalid")
            placeholder = binding.get("placeholder")
            if not isinstance(placeholder, str) or not placeholder.startswith("$asset_url_"):
                raise ValueError("asset binding placeholder is invalid")
            if "$execution_ref" in binding:
                if not str(binding.get("$execution_ref") or "").startswith("artifact://"):
                    raise ValueError("asset binding execution ref is invalid")
                output_index = binding.get("output_index")
                if isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
                    raise ValueError("asset binding output index is invalid")
            elif not str(binding.get("$report_manifest_ref") or "").startswith("artifact://") or not isinstance(binding.get("artifact_id"), str):
                raise ValueError("asset binding report manifest ref is invalid")
            panel_placeholders.add(placeholder)
        options = panel.get("options") or {}
        serialized_options = json.dumps(options, ensure_ascii=False)
        if panel_placeholders and not any(placeholder in serialized_options for placeholder in panel_placeholders):
            raise ValueError("asset binding placeholder is not used by its panel")
        if panel.get("type") == PLOTLY_PLUGIN_ID:
            plotly_bindings = panel.get("askO11yPlotlyBindings") or []
            if not isinstance(plotly_bindings, list):
                raise ValueError("Plotly bindings must be an array")
            mode = options.get("renderMode")
            if mode not in {"image", "plotly"} or "narrative" not in options:
                raise ValueError("image evidence requires an explicit mode and narrative")
            if mode == "image" and (not panel_placeholders or options.get("fallbackUrl") not in panel_placeholders or plotly_bindings or "figure" in options):
                raise ValueError("image mode requires an opaque PNG binding")
            if mode == "plotly" and not plotly_bindings:
                raise ValueError("Plotly mode requires a sanitized figure binding")
            plotly_placeholders = {binding.get("placeholder") for binding in plotly_bindings if isinstance(binding, dict)}
            if mode == "plotly" and not any(isinstance(item, str) and item in serialized_options for item in plotly_placeholders):
                raise ValueError("Plotly binding placeholder is not used by its panel")
            if mode == "plotly" and panel_placeholders and options.get("fallbackUrl") not in panel_placeholders:
                raise ValueError("Plotly fallback must use its opaque PNG binding")
            if mode == "plotly" and not panel_placeholders and "fallbackUrl" in options:
                raise ValueError("Plotly fallback requires an opaque PNG binding")
            if options.get("selectedViewIds") != view_ids or options.get("viewNarratives") != panel.get("askO11yViewNarratives"):
                raise ValueError("Plotly selected views and narratives must match the evidence panel metadata")
            view_specs = options.get("viewSpecs")
            if mode == "plotly" and (not isinstance(view_specs, list) or {item.get("view_id") for item in view_specs if isinstance(item, dict)} != set(view_ids)):
                raise ValueError("Plotly view specs must cover the selected evidence views")
    _walk(dashboard)


def validate_ml_dashboard_minimum(dashboard: dict[str, Any]) -> None:
    """Runtime write-gate retained under its existing public name."""
    _validate_dashboard(dashboard, require_uid=False)


def validate_preview_dashboard(dashboard: dict[str, Any], manifest: dict[str, Any] | None = None) -> None:
    """Validate the opaque pre-resolution report dashboard; manifest is validated upstream."""
    _validate_dashboard(dashboard, require_uid=True)
