"""Bounded Plotly figure contract shared by Sandbox presentation and Artifact Bridge."""
from __future__ import annotations

import json
import math
from typing import Any, NoReturn

PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"
FIGURE_FORMAT = "ask-o11y-ml-plotly-v1"

ALLOWED_TRACES = {"bar", "scatter", "heatmap", "indicator", "sankey"}
FORBIDDEN_KEYS = {
    "frames", "transforms", "customdata", "ids", "meta", "hovertemplate", "texttemplate",
    "images", "template", "updatemenus", "sliders", "href", "src", "base64",
    "script", "onclick", "callback",
}
AXIS_LAYOUT_KEYS = {"xaxis", "yaxis"} | {f"{axis}{index}" for axis in ("xaxis", "yaxis") for index in range(2, 13)}
ALLOWED_LAYOUT_KEYS = {
    "title", "margin", "legend", "showlegend", "barmode", "hovermode", "annotations", "font",
    "paper_bgcolor", "plot_bgcolor", "coloraxis", "width", "height", "grid", "colorway",
    "uniformtext",
} | AXIS_LAYOUT_KEYS
ALLOWED_AXIS_KEYS = {"title", "range", "type", "tickformat", "tickvals", "ticktext", "showgrid", "zeroline", "overlaying", "side", "automargin", "dtick", "domain", "anchor", "rangemode"}
ALLOWED_ANNOTATION_KEYS = {"text", "x", "y", "xref", "yref", "showarrow", "font", "ax", "ay"}
ALLOWED_COLORAXIS_KEYS = {"cmin", "cmax", "colorscale", "showscale", "colorbar"}
ALLOWED_TRACE_KEYS = {
    "type", "name", "x", "y", "z", "mode", "orientation", "marker", "line", "text",
    "showlegend", "coloraxis", "xaxis", "yaxis", "opacity", "width", "values",
    "labels", "insidetextorientation", "colorscale", "xgap", "ygap", "node", "link",
    "arrangement", "valueformat", "delta", "number", "title", "reference", "fill",
    "value", "domain",
}
ALLOWED_MARKER_KEYS = {"color", "size", "colorscale", "line", "opacity", "cmin", "cmid", "cmax", "showscale"}
ALLOWED_LINE_KEYS = {"color", "width", "dash", "shape"}
ALLOWED_FONT_KEYS = {"size", "color", "family"}
ALLOWED_MARGIN_KEYS = {"l", "r", "t", "b"}
ALLOWED_LEGEND_KEYS = {"orientation", "x", "y", "xanchor", "yanchor"}
ALLOWED_INDICATOR_KEYS = {"mode", "value", "delta", "number", "title", "reference"}
ALLOWED_DELTA_KEYS = {"reference", "valueformat"}
ALLOWED_NUMBER_KEYS = {"valueformat"}
ALLOWED_TITLE_KEYS = {"text"}
ALLOWED_NODE_KEYS = {"label", "pad", "thickness", "color"}
ALLOWED_LINK_KEYS = {"source", "target", "value", "color"}
ALLOWED_UNIFORMTEXT_KEYS = {"mode", "minsize"}

FIXED_CONFIG = {"displaylogo": False, "responsive": True}
HOST_COLORSCALE = [[0.0, "#1f3a4d"], [1.0, "#4fd1c5"]]
RISK_COLORSCALE = [[0.0, "#1f3a4d"], [1.0, "#eda06a"]]

MAX_FIGURE_BYTES = 64 * 1024
MAX_FIGURES = 14
MAX_POINTS_PER_FIGURE = 6000
MAX_ITEMS_PER_ARRAY = 2000
MAX_HEATMAP_CELLS = 100 * 100
MAX_STRING_LENGTH = 200


def _reject(message: str) -> NoReturn:
    raise ValueError(f"plotly figure contract violation: {message}")


def _check_key(key: Any, where: str) -> str:
    key = str(key)
    lowered = key.lower()
    if lowered in FORBIDDEN_KEYS:
        _reject(f"forbidden key {key!r} in {where}")
    if len(key) > 40:
        _reject(f"oversized key in {where}")
    return key


def _check_string(value: str, where: str) -> str:
    if len(value) > MAX_STRING_LENGTH:
        _reject(f"string length exceeds {MAX_STRING_LENGTH} in {where}")
    lowered = value.lower()
    if "<" in value or ">" in value or "javascript:" in lowered or lowered.startswith("http"):
        _reject(f"forbidden character or scheme in {where}")
    return value


def _check_color(value: Any, where: str) -> None:
    if not isinstance(value, str) or not _is_hex_color(value):
        _reject(f"color must be a hex string in {where}")


def _is_hex_color(value: str) -> bool:
    body = value[1:] if value.startswith("#") else ""
    return bool(body) and all(character in "0123456789abcdefABCDEF" for character in body) and len(body) in {3, 4, 6, 8}


def _check_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(f"expected numeric value in {where}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"plotly figure contract violation: expected numeric value in {where}: {exc}") from exc
    if not math.isfinite(number):
        _reject(f"expected finite numeric value in {where}")
    return number


def _check_array(value: Any, where: str, *, max_items: int, numbers: bool) -> list[Any]:
    if not isinstance(value, list) or not value:
        _reject(f"expected non-empty array in {where}")
    if len(value) > max_items:
        _reject(f"points budget exceeded in {where}: {len(value)} > {max_items}")
    cleaned: list[Any] = []
    for item in value:
        if numbers:
            cleaned.append(_check_number(item, where))
        elif isinstance(item, str):
            cleaned.append(_check_string(item, where))
        else:
            _reject(f"expected string items in {where}")
    return cleaned


def _check_mapping_keys(value: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _reject(f"expected object in {where}")
    for key in value:
        if _check_key(key, where) not in allowed:
            _reject(f"unsupported {where} key {key!r}")
    return value


def _clean_colorscale(value: Any, where: str) -> list[list[Any]]:
    if not isinstance(value, list) or not value:
        _reject(f"invalid colorscale in {where}")
    cleaned = []
    for stop in value:
        if not isinstance(stop, (list, tuple)) or len(stop) != 2:
            _reject(f"invalid colorscale stop in {where}")
        position = _check_number(stop[0], where)
        _check_color(stop[1], where)
        cleaned.append([position, stop[1]])
    return cleaned


def _count_trace_points(budget: dict[str, int], values: list[Any], where: str) -> None:
    budget["points"] += len(values)
    if budget["points"] > MAX_POINTS_PER_FIGURE:
        _reject(f"points budget exceeded for figure: {budget['points']} > {MAX_POINTS_PER_FIGURE}")


def _clean_trace(trace: Any, budget: dict[str, int]) -> dict[str, Any]:
    _check_mapping_keys(trace, ALLOWED_TRACE_KEYS, "trace")
    trace_type = str(trace.get("type") or "")
    if trace_type not in ALLOWED_TRACES:
        _reject(f"unsupported trace type {trace_type!r}")
    cleaned: dict[str, Any] = {"type": trace_type}
    for key, value in trace.items():
        where = f"trace.{key}"
        if key in {"type"}:
            continue
        if key in {"x", "y", "text", "labels"}:
            numbers_only = key in {"x", "y"} and _looks_numeric(value)
            cleaned[key] = _check_array(value, where, max_items=MAX_ITEMS_PER_ARRAY, numbers=numbers_only)
            _count_trace_points(budget, cleaned[key], where)
            continue
        if key in {"z"}:
            if not isinstance(value, list) or not value:
                _reject(f"expected non-empty z array in {where}")
            rows = len(value)
            columns = max((len(row) for row in value if isinstance(row, list)), default=0)
            if rows * columns > MAX_HEATMAP_CELLS:
                _reject(f"points budget exceeded in {where}")
            budget["points"] += rows * columns
            cleaned[key] = [[_check_number(cell, where) for cell in row] if isinstance(row, list) else _check_number(row, where) for row in value]
            continue
        if key in {"values", "source", "target"}:
            cleaned[key] = _check_array(value, where, max_items=MAX_ITEMS_PER_ARRAY, numbers=True)
            _count_trace_points(budget, cleaned[key], where)
            continue
        if key in {"name"}:
            cleaned[key] = _check_string(str(value), where)
            continue
        if key in {"mode"}:
            modes = str(value)
            if any(part not in {"lines", "markers", "text", "none"} for part in modes.split("+")):
                _reject(f"unsupported mode in {where}")
            cleaned[key] = modes
            continue
        if key in {"marker", "line"}:
            allowed = ALLOWED_MARKER_KEYS if key == "marker" else ALLOWED_LINE_KEYS
            nested = _check_mapping_keys(value, allowed, where)
            cleaned[key] = _clean_styling(nested, where)
            continue
        if key == "colorscale":
            cleaned[key] = _clean_colorscale(value, where)
            continue
        if key in {"node", "link"}:
            allowed = ALLOWED_NODE_KEYS if key == "node" else ALLOWED_LINK_KEYS
            nested = _check_mapping_keys(value, allowed, where)
            cleaned_node: dict[str, Any] = {}
            for nested_key, nested_value in nested.items():
                if isinstance(nested_value, list):
                    numbers_only = nested_key in {"source", "target", "value", "pad", "thickness"}
                    cleaned_node[nested_key] = _check_array(nested_value, f"{where}.{nested_key}", max_items=MAX_ITEMS_PER_ARRAY, numbers=numbers_only)
                elif nested_key in {"color"}:
                    _check_color(nested_value, f"{where}.{nested_key}")
                    cleaned_node[nested_key] = nested_value
                else:
                    cleaned_node[nested_key] = _check_number(nested_value, f"{where}.{nested_key}") if not isinstance(nested_value, str) else _check_string(nested_value, f"{where}.{nested_key}")
            cleaned[key] = cleaned_node
            continue
        if key in {"delta", "number", "title"}:
            allowed = {"delta": ALLOWED_DELTA_KEYS, "number": ALLOWED_NUMBER_KEYS, "title": ALLOWED_TITLE_KEYS}[key]
            nested = _check_mapping_keys(value, allowed, f"trace.{key}")
            cleaned_nested: dict[str, Any] = {}
            for nested_key, nested_value in nested.items():
                if isinstance(nested_value, str):
                    cleaned_nested[nested_key] = _check_string(nested_value, f"trace.{key}.{nested_key}")
                else:
                    cleaned_nested[nested_key] = _check_number(nested_value, f"trace.{key}.{nested_key}")
            cleaned[key] = cleaned_nested
            continue
        if key in {"reference", "value"}:
            cleaned[key] = _check_number(value, where)
            continue
        if key == "domain":
            if not isinstance(value, dict):
                _reject(f"expected domain object in {where}")
            cleaned_domain: dict[str, Any] = {}
            for domain_key, domain_value in value.items():
                if domain_key not in {"x", "y"} or not isinstance(domain_value, list) or len(domain_value) != 2:
                    _reject(f"invalid domain in {where}.{domain_key}")
                cleaned_domain[str(domain_key)] = [_check_number(bound, f"{where}.{domain_key}") for bound in domain_value]
            cleaned[key] = cleaned_domain
            continue
        if key == "coloraxis":
            cleaned[key] = _check_string(str(value), where)
            continue
        if key in {"opacity", "width", "xgap", "ygap", "arrangement"}:
            cleaned[key] = _check_number(value, where)
            continue
        if key in {"orientation", "xaxis", "yaxis", "valueformat", "insidetextorientation", "fill"}:
            cleaned[key] = _check_string(str(value), where)
            continue
        if key in {"showlegend"}:
            cleaned[key] = bool(value)
            continue
        _reject(f"unsupported trace key {key!r}")
    if budget["points"] > MAX_POINTS_PER_FIGURE:
        _reject(f"points budget exceeded for figure: {budget['points']} > {MAX_POINTS_PER_FIGURE}")
    return cleaned


def _looks_numeric(value: Any) -> bool:
    return bool(value) and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def _clean_styling(value: dict[str, Any], where: str) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, nested_value in value.items():
        nested_where = f"{where}.{key}"
        if nested_value is None:
            continue
        if key in {"color", "line"} and key == "color":
            if isinstance(nested_value, str):
                _check_color(nested_value, nested_where)
                cleaned[key] = nested_value
            elif isinstance(nested_value, list):
                if _looks_numeric(nested_value):
                    cleaned[key] = _check_array(nested_value, nested_where, max_items=MAX_ITEMS_PER_ARRAY, numbers=True)
                else:
                    cleaned[key] = _check_array(nested_value, nested_where, max_items=MAX_ITEMS_PER_ARRAY, numbers=False)
                    for item in cleaned[key]:
                        _check_color(item, nested_where)
            else:
                _reject(f"invalid color in {nested_where}")
            continue
        if key == "line":
            nested_line = _check_mapping_keys(nested_value, ALLOWED_LINE_KEYS, nested_where)
            cleaned[key] = _clean_styling(nested_line, nested_where)
            continue
        if key in {"size", "width", "opacity", "cmin", "cmid", "cmax"}:
            cleaned[key] = _check_number(nested_value, nested_where)
            continue
        if key == "colorscale":
            cleaned[key] = _clean_colorscale(nested_value, nested_where)
            continue
        if key in {"showscale"}:
            cleaned[key] = bool(nested_value)
            continue
        _reject(f"unsupported {where} key {key!r}")
    return cleaned


def _clean_layout(layout: Any) -> dict[str, Any]:
    _check_mapping_keys(layout, ALLOWED_LAYOUT_KEYS, "layout")
    cleaned: dict[str, Any] = {}
    for key, value in layout.items():
        where = f"layout.{key}"
        if value is None:
            continue
        if key == "title":
            cleaned[key] = _check_string(str(value), where)
            continue
        if key in AXIS_LAYOUT_KEYS:
            axis = _check_mapping_keys(value, ALLOWED_AXIS_KEYS, where)
            cleaned_axis: dict[str, Any] = {}
            for axis_key, axis_value in axis.items():
                axis_where = f"{where}.{axis_key}"
                if isinstance(axis_value, str):
                    cleaned_axis[axis_key] = _check_string(axis_value, axis_where)
                elif isinstance(axis_value, list):
                    numbers_only = axis_key in {"range", "domain"} or (axis_key == "tickvals" and _looks_numeric(axis_value))
                    cleaned_axis[axis_key] = _check_array(axis_value, axis_where, max_items=MAX_ITEMS_PER_ARRAY, numbers=numbers_only)
                elif isinstance(axis_value, bool):
                    cleaned_axis[axis_key] = axis_value
                else:
                    cleaned_axis[axis_key] = _check_number(axis_value, axis_where)
            cleaned[key] = cleaned_axis
            continue
        if key == "annotations":
            if not isinstance(value, list) or len(value) > 8:
                _reject(f"annotations budget exceeded in {where}")
            cleaned_annotations = []
            for annotation in value:
                checked = _check_mapping_keys(annotation, ALLOWED_ANNOTATION_KEYS, f"{where}[]")
                cleaned_item: dict[str, Any] = {}
                for annotation_key, annotation_value in checked.items():
                    if isinstance(annotation_value, str):
                        cleaned_item[annotation_key] = _check_string(annotation_value, f"{where}.text")
                    elif isinstance(annotation_value, bool):
                        cleaned_item[annotation_key] = annotation_value
                    else:
                        cleaned_item[annotation_key] = _check_number(annotation_value, where)
                cleaned_annotations.append(cleaned_item)
            cleaned[key] = cleaned_annotations
            continue
        if key == "font":
            checked = _check_mapping_keys(value, ALLOWED_FONT_KEYS, where)
            cleaned_font: dict[str, Any] = {}
            for font_key, font_value in checked.items():
                cleaned_font[font_key] = _check_string(str(font_value), f"{where}.{font_key}") if isinstance(font_value, str) else _check_number(font_value, f"{where}.{font_key}")
            cleaned[key] = cleaned_font
            continue
        if key == "margin":
            checked = _check_mapping_keys(value, ALLOWED_MARGIN_KEYS, where)
            cleaned[key] = {margin_key: _check_number(margin_value, f"{where}.{margin_key}") for margin_key, margin_value in checked.items()}
            continue
        if key == "legend":
            checked = _check_mapping_keys(value, ALLOWED_LEGEND_KEYS, where)
            cleaned_legend: dict[str, Any] = {}
            for legend_key, legend_value in checked.items():
                cleaned_legend[legend_key] = _check_string(str(legend_value), f"{where}.{legend_key}") if isinstance(legend_value, str) else _check_number(legend_value, f"{where}.{legend_key}")
            cleaned[key] = cleaned_legend
            continue
        if key == "coloraxis":
            checked = _check_mapping_keys(value, ALLOWED_COLORAXIS_KEYS, where)
            cleaned_coloraxis: dict[str, Any] = {}
            for coloraxis_key, coloraxis_value in checked.items():
                if coloraxis_key == "colorscale":
                    cleaned_coloraxis[coloraxis_key] = _clean_colorscale(coloraxis_value, f"{where}.colorscale")
                elif isinstance(coloraxis_value, bool):
                    cleaned_coloraxis[coloraxis_key] = coloraxis_value
                elif isinstance(coloraxis_value, (int, float)):
                    cleaned_coloraxis[coloraxis_key] = _check_number(coloraxis_value, f"{where}.{coloraxis_key}")
                else:
                    _reject(f"unsupported {where}.{coloraxis_key}")
            cleaned[key] = cleaned_coloraxis
            continue
        if key == "colorway":
            cleaned[key] = _check_array(value, where, max_items=12, numbers=False)
            for color in cleaned[key]:
                _check_color(color, where)
            continue
        if key == "uniformtext":
            checked = _check_mapping_keys(value, ALLOWED_UNIFORMTEXT_KEYS, where)
            cleaned[key] = {
                uniform_key: (_check_string(str(uniform_value), f"{where}.{uniform_key}") if isinstance(uniform_value, str) else _check_number(uniform_value, f"{where}.{uniform_key}"))
                for uniform_key, uniform_value in checked.items()
            }
            continue
        if key in {"showlegend", "grid"}:
            cleaned[key] = value if isinstance(value, bool) else _reject(f"unsupported {where}")
            continue
        if key in {"width", "height"}:
            cleaned[key] = _check_number(value, where)
            continue
        if key in {"paper_bgcolor", "plot_bgcolor"}:
            _check_color(value, where)
            cleaned[key] = value
            continue
        if key in {"barmode", "hovermode"}:
            cleaned[key] = _check_string(str(value), where)
            continue
        _reject(f"unsupported layout key {key!r}")
    return cleaned


def sanitize_figure(figure: Any) -> dict[str, Any]:
    """Validate one figure and return the static {data, layout, config} for a panel."""
    if not isinstance(figure, dict):
        _reject("figure must be an object")
    unexpected = set(figure) - {"data", "layout", "config"}
    if unexpected:
        _reject(f"unsupported figure keys {sorted(unexpected)}")
    if "config" in figure and figure["config"] != FIXED_CONFIG:
        _reject("figure config must match the host-owned fixed config")
    data = figure.get("data")
    if not isinstance(data, list) or not data:
        _reject("figure data must be a non-empty array")
    if len(data) > 12:
        _reject("figure data exceeds 12 traces")
    budget = {"points": 0}
    cleaned_data = [_clean_trace(trace, budget) for trace in data]
    if budget["points"] > MAX_POINTS_PER_FIGURE:
        _reject(f"points budget exceeded for figure: {budget['points']} > {MAX_POINTS_PER_FIGURE}")
    cleaned = {"data": cleaned_data, "layout": _clean_layout(figure.get("layout") or {}), "config": dict(FIXED_CONFIG)}
    encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_FIGURE_BYTES:
        _reject(f"figure exceeds {MAX_FIGURE_BYTES} bytes")
    return cleaned


def sanitize_execution(figures: Any) -> list[dict[str, Any]]:
    """Validate the per-execution figure collection (count budget)."""
    if not isinstance(figures, list):
        _reject("figures must be an array")
    if len(figures) > MAX_FIGURES:
        _reject(f"figure count {len(figures)} exceeds {MAX_FIGURES}")
    return [sanitize_figure(figure) for figure in figures]
