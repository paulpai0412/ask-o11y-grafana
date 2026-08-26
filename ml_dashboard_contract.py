"""Flow-agnostic safety validator for evidence-bound Grafana report dashboards."""
from __future__ import annotations

import json
import re
from typing import Any

FORBIDDEN_KEYS = {"raw_rows", "frame", "python_code", "credentials", "physical_path", "signed_url"}
PREVIEW_TAG = "ask-o11y-preview"
REPORT_TAG = "ask-o11y-report"
PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"
MAX_SECTIONS = 8
MAX_PANELS = 24
NARRATIVE_FIELDS = {"headline", "observation", "interpretation", "cross_chart_context", "limitation", "next_step", "evidence"}


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
    return rows


def _safe_text(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 800:
        raise ValueError(f"{where} is invalid")
    lowered = value.lower()
    if any(token in lowered for token in ("<", ">", "javascript:", "http://", "https://")):
        raise ValueError(f"{where} contains unsafe text")


def _validate_narrative(panel: dict[str, Any]) -> None:
    narrative = panel.get("askO11yNarrative")
    if not isinstance(narrative, dict) or set(narrative) != NARRATIVE_FIELDS:
        raise ValueError("evidence panel requires a complete askO11yNarrative")
    for key in NARRATIVE_FIELDS - {"evidence"}:
        _safe_text(narrative.get(key), f"narrative.{key}")
    evidence = narrative.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        raise ValueError("panel narrative evidence is outside bounds")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"fact_ref", "label", "display"}:
            raise ValueError("panel narrative evidence shape is invalid")
        for key in ("fact_ref", "label", "display"):
            _safe_text(str(item.get(key) or ""), f"narrative.evidence.{key}")


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
    _safe_text(thesis_panels[0].get("askO11yReportThesis"), "report thesis")
    flattened = _panels([item for item in top_level if isinstance(item, dict)])
    if len(flattened) > MAX_PANELS:
        raise ValueError("dashboard panel count exceeds bound")
    if any(panel.get("targets") for panel in flattened):
        raise ValueError("analysis dashboard may contain evidence/text panels only")
    if any(panel.get("type") not in {"row", "text", PLOTLY_PLUGIN_ID} for panel in flattened):
        raise ValueError("report dashboard contains an unsupported panel type")

    evidence_panels = [panel for panel in flattened if panel.get("askO11yArtifactId") is not None]
    if not evidence_panels:
        raise ValueError("report dashboard requires at least one evidence panel")
    placeholders: set[str] = set()
    for panel in evidence_panels:
        artifact_id = panel.get("askO11yArtifactId")
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", artifact_id):
            raise ValueError("evidence panel artifact id is invalid")
        view_ids = panel.get("askO11yViewIds")
        if not isinstance(view_ids, list) or not 1 <= len(view_ids) <= 12 or any(not isinstance(view_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", view_id) for view_id in view_ids):
            raise ValueError("evidence panel view ids are invalid")
        _validate_narrative(panel)
        bindings = panel.get("askO11yAssetBindings")
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("evidence panel requires an opaque PNG binding")
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {"placeholder", "$execution_ref", "output_index"}:
                raise ValueError("asset binding shape is invalid")
            placeholder = binding.get("placeholder")
            if not isinstance(placeholder, str) or not placeholder.startswith("$asset_url_"):
                raise ValueError("asset binding placeholder is invalid")
            if not str(binding.get("$execution_ref") or "").startswith("artifact://"):
                raise ValueError("asset binding execution ref is invalid")
            output_index = binding.get("output_index")
            if isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
                raise ValueError("asset binding output index is invalid")
            placeholders.add(placeholder)
        serialized_options = json.dumps(panel.get("options") or {}, ensure_ascii=False)
        if not any(placeholder in serialized_options for placeholder in placeholders):
            raise ValueError("asset binding placeholder is not used by its panel")
        if panel.get("type") == PLOTLY_PLUGIN_ID:
            plotly_bindings = panel.get("askO11yPlotlyBindings")
            options = panel.get("options") or {}
            if not isinstance(plotly_bindings, list) or not plotly_bindings or "fallbackUrl" not in options or "narrative" not in options:
                raise ValueError("Plotly evidence panel requires figure, fallback, and narrative bindings")
            if options.get("selectedViewIds") != view_ids:
                raise ValueError("Plotly selected views must match the evidence panel view ids")
            view_specs = options.get("viewSpecs")
            if not isinstance(view_specs, list) or {item.get("view_id") for item in view_specs if isinstance(item, dict)} != set(view_ids):
                raise ValueError("Plotly view specs must cover the selected evidence views")
    _walk(dashboard)


def validate_ml_dashboard_minimum(dashboard: dict[str, Any]) -> None:
    """Runtime write-gate retained under its existing public name."""
    _validate_dashboard(dashboard, require_uid=False)


def validate_preview_dashboard(dashboard: dict[str, Any], manifest: dict[str, Any] | None = None) -> None:
    """Validate the opaque pre-resolution report dashboard; manifest is validated upstream."""
    _validate_dashboard(dashboard, require_uid=True)
