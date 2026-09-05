"""Generic evidence-bound Grafana dashboard compositor for LLM-authored report flows."""
from __future__ import annotations

import html
import math
from typing import Any

import ml_report_contract  # type: ignore[reportMissingImports]

PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"


def _number(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{where} is non-finite")
    return number


def format_fact(fact: dict[str, Any], value_format: str) -> str:
    value = fact["value"]
    if fact["kind"] != "number" or value_format == "auto":
        return str(value)
    number = _number(value, "evidence fact")
    if value_format == "integer":
        return f"{round(number):,}"
    if value_format == "number_1":
        return f"{number:,.1f}"
    if value_format == "number_2":
        return f"{number:,.2f}"
    if value_format == "percent_1":
        return f"{number * 100:.1f}%"
    raise ValueError("unsupported evidence format")


def _format_evidence(items: list[dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "fact_ref": item["fact_ref"],
            "label": item.get("label") or catalog[item["fact_ref"]]["label"],
            "display": format_fact(catalog[item["fact_ref"]], item["format"]),
        }
        for item in items
    ]


def _evidence_html(evidence: list[dict[str, str]]) -> str:
    return "".join(
        f'<span style="display:inline-block;margin:4px 8px 4px 0;padding:4px 8px;border:1px solid currentColor;border-radius:4px">{html.escape(item["label"])}: <strong>{html.escape(item["display"])}</strong></span>'
        for item in evidence
    )


def _narrative(panel: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence = [
        {
            "fact_ref": item["fact_ref"],
            "label": item.get("label") or catalog[item["fact_ref"]]["label"],
            "display": format_fact(catalog[item["fact_ref"]], item["format"]),
        }
        for item in panel["evidence"]
    ]
    return {
        "headline": panel["headline"],
        "observation": panel["observation"],
        "interpretation": panel["interpretation"],
        "cross_chart_context": panel["cross_chart_context"],
        "limitation": panel["limitation"],
        "next_step": panel["next_step"],
        "evidence": evidence,
    }


def _view_narratives(panel: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for item in panel["view_narratives"]:
        output.append({
            **{key: item[key] for key in ("view_id", "headline", "data_observation", "visual_observation", "interpretation", "limitation", "next_step")},
            "evidence": [
                {
                    "fact_ref": evidence["fact_ref"],
                    "label": evidence.get("label") or catalog[evidence["fact_ref"]]["label"],
                    "display": format_fact(catalog[evidence["fact_ref"]], evidence["format"]),
                }
                for evidence in item["evidence"]
            ],
        })
    return output


def _narrative_html(narrative: dict[str, Any]) -> str:
    facts = "".join(
        f'<span style="display:inline-block;margin:4px 8px 4px 0;padding:4px 8px;border:1px solid currentColor;border-radius:4px">{html.escape(item["label"])}: <strong>{html.escape(item["display"])}</strong></span>'
        for item in narrative["evidence"]
    )
    fields = (
        ("观察", "observation"), ("解读", "interpretation"), ("跨图关系", "cross_chart_context"),
        ("限制", "limitation"), ("下一步", "next_step"),
    )
    body = "".join(f'<div style="margin-top:8px"><strong>{label}：</strong>{html.escape(narrative[key])}</div>' for label, key in fields)
    return f'<div style="padding:8px 12px"><h3>{html.escape(narrative["headline"])}</h3><div>{facts}</div>{body}</div>'


def _view_narratives_html(view_narratives: list[dict[str, Any]]) -> str:
    sections = []
    for narrative in view_narratives:
        visual = f'<div><strong>视觉：</strong>{html.escape(narrative["visual_observation"])}</div>' if narrative["visual_observation"] else ""
        evidence = "".join(f'<span style="margin-right:8px">{html.escape(item["label"])}: <strong>{html.escape(item["display"])}</strong></span>' for item in narrative["evidence"])
        sections.append(
            f'<section style="margin-top:12px"><h4>{html.escape(narrative["headline"])}</h4>'
            f'<div>{evidence}</div><div><strong>资料：</strong>{html.escape(narrative["data_observation"])}</div>{visual}'
            f'<div><strong>解读：</strong>{html.escape(narrative["interpretation"])}</div>'
            f'<div><strong>限制：</strong>{html.escape(narrative["limitation"])}</div>'
            f'<div><strong>下一步：</strong>{html.escape(narrative["next_step"])}</div></section>'
        )
    return "".join(sections)


def _panel_height(width: str) -> int:
    return 12 if width == "full" else 10


def _layouts(
    panels: list[dict[str, Any]],
    outputs: dict[str, dict[str, Any]],
    start_y: int = 0,
) -> tuple[list[dict[str, int]], int]:
    layouts: list[dict[str, int]] = []
    y = start_y
    half_open = False
    open_height = 0
    for panel in panels:
        capability = outputs.get(panel["artifact_id"], {})
        width = "full" if capability.get("recommended_width") == "full" else panel["preferred_width"]
        try:
            min_height = int(capability.get("min_height") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("dashboard output min_height is invalid") from exc
        height = max(_panel_height(width), min_height)
        if width == "full":
            if half_open:
                y += open_height
                half_open = False
            layouts.append({"h": height, "w": 24, "x": 0, "y": y})
            y += height
        elif not half_open:
            layouts.append({"h": height, "w": 12, "x": 0, "y": y})
            open_height = height
            half_open = True
        else:
            layouts.append({"h": height, "w": 12, "x": 12, "y": y})
            y += max(open_height, height)
            half_open = False
    if half_open:
        y += open_height
    return layouts, y


def _evidence_panel(
    panel_id: int,
    panel: dict[str, Any],
    narrative: dict[str, Any],
    view_narratives: list[dict[str, Any]],
    execution_ref: str,
    output: dict[str, Any],
    grid_pos: dict[str, int],
) -> dict[str, Any]:
    artifact_id = panel["artifact_id"]
    asset_placeholder = f"$asset_url_{artifact_id}"
    asset_binding = {"placeholder": asset_placeholder, "$execution_ref": execution_ref, "output_index": output["png_index"]}
    base = {
        "id": panel_id,
        "title": panel["headline"],
        "gridPos": grid_pos,
        "askO11yArtifactId": artifact_id,
        "askO11yViewIds": panel["view_ids"],
        "askO11yNarrative": narrative,
        "askO11yViewNarratives": view_narratives,
        "askO11yAssetBindings": [asset_binding],
    }
    if "plotly_index" in output:
        plotly_placeholder = f"$plotly_{artifact_id}"
        return {
            **base,
            "type": PLOTLY_PLUGIN_ID,
            "options": {
                "renderMode": "plotly",
                "figure": plotly_placeholder,
                "fallbackUrl": asset_placeholder,
                "alt": panel["headline"],
                "narrative": narrative,
                "selectedViewIds": panel["view_ids"],
                "viewSpecs": [view for view in output.get("figure_spec", {}).get("views", []) if view.get("view_id") in panel["view_ids"]],
                "viewNarratives": view_narratives,
            },
            "askO11yPlotlyBindings": [{
                "placeholder": plotly_placeholder,
                "$execution_ref": execution_ref,
                "output_index": output["plotly_index"],
                "plugin_id": PLOTLY_PLUGIN_ID,
            }],
        }
    return {
        **base,
        "type": PLOTLY_PLUGIN_ID,
        "options": {
            "renderMode": "image",
            "fallbackUrl": asset_placeholder,
            "alt": panel["headline"],
            "narrative": narrative,
            "selectedViewIds": panel["view_ids"],
            "viewNarratives": view_narratives,
        },
    }


def compose_dashboard(
    manifest: dict[str, Any],
    synthesis: dict[str, Any],
    *,
    execution_ref: str,
    outputs: dict[str, dict[str, Any]],
    uid: str,
    title: str,
) -> dict[str, Any]:
    """Render the LLM-authored section flow without choosing or reordering its content."""
    validated = ml_report_contract.validate_report_synthesis(manifest, synthesis)
    catalog = ml_report_contract.build_fact_catalog(manifest)
    if not isinstance(uid, str) or not uid or not isinstance(title, str) or not title:
        raise ValueError("dashboard uid and title are required")
    thesis_evidence = _format_evidence(validated["thesis_evidence"], catalog)
    panels: list[dict[str, Any]] = [{
        "id": 1,
        "type": "text",
        "title": validated["report_title"],
        "askO11yReportThesis": validated["thesis"],
        "askO11yThesisEvidence": thesis_evidence,
        "gridPos": {"h": 7, "w": 24, "x": 0, "y": 0},
        "options": {"mode": "html", "content": f'<div style="padding:8px 12px"><h2>{html.escape(validated["report_title"])}</h2><p>{html.escape(validated["thesis"])}</p><div>{_evidence_html(thesis_evidence)}</div></div>'},
    }]
    next_id = 2
    y = 7
    for section in validated["sections"]:
        row_panel = {
            "id": next_id, "type": "row", "title": section["title"], "collapsed": section["collapsed"],
            "askO11ySectionId": section["section_id"], "askO11ySectionPurpose": section["purpose"],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        }
        next_id += 1
        y += 1
        section_panels: list[dict[str, Any]] = []
        block_start = 0 if section["collapsed"] else y
        for block in section["narrative_blocks"]:
            block_evidence = _format_evidence(block["evidence"], catalog)
            section_panels.append({
                "id": next_id, "type": "text", "title": block["title"],
                "askO11yNarrativeBlock": {**block, "evidence": block_evidence},
                "gridPos": {"h": 4, "w": 24, "x": 0, "y": block_start},
                "options": {"mode": "html", "content": f'<div style="padding:8px 12px"><h3>{html.escape(block["title"])}</h3><p>{html.escape(block["body"])}</p><div>{_evidence_html(block_evidence)}</div></div>'},
            })
            next_id += 1
            block_start += 4
        evidence_layouts, section_end = _layouts(section["panels"], outputs, block_start)
        for item, grid_pos in zip(section["panels"], evidence_layouts, strict=True):
            artifact_id = item["artifact_id"]
            if artifact_id not in outputs or "png_index" not in outputs[artifact_id]:
                raise ValueError("dashboard output mapping is incomplete")
            section_panels.append(_evidence_panel(next_id, item, _narrative(item, catalog), _view_narratives(item, catalog), execution_ref, outputs[artifact_id], grid_pos))
            next_id += 1
        if section["collapsed"]:
            row_panel["panels"] = section_panels
            panels.append(row_panel)
        else:
            panels.append(row_panel)
            panels.extend(section_panels)
            y = section_end
    return {
        "uid": uid,
        "title": title,
        "tags": ["ask-o11y-preview", "ask-o11y-report"],
        "timezone": "browser",
        "schemaVersion": 41,
        "version": 0,
        "refresh": "",
        "timepicker": {"hidden": True},
        "panels": panels,
    }
