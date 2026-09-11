"""Bounded Plotly figure contract shared by Sandbox presentation and Artifact Bridge."""
from __future__ import annotations

import base64
import binascii
import hashlib
from html.parser import HTMLParser
import json
import math
import re
import struct
from typing import Any, NoReturn

PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"
FIGURE_FORMAT = "ask-o11y-ml-plotly-v1"

ALLOWED_TRACES = {
    "bar", "box", "candlestick", "contour", "funnel", "funnelarea", "histogram",
    "histogram2d", "histogram2dcontour", "heatmap", "indicator", "ohlc", "pie", "sankey",
    "scatter", "scattergl", "sunburst", "treemap", "violin", "waterfall",
}
FORBIDDEN_KEYS = {
    "frames", "transforms", "customdata", "ids", "meta", "hovertemplate", "texttemplate",
    "images", "template", "updatemenus", "sliders", "href", "src", "base64",
    "script", "onclick", "callback",
}
AXIS_LAYOUT_KEYS = {"xaxis", "yaxis"} | {f"{axis}{index}" for axis in ("xaxis", "yaxis") for index in range(2, 13)}
ALLOWED_LAYOUT_KEYS = {
    "title", "margin", "legend", "showlegend", "barmode", "hovermode", "annotations", "font",
    "paper_bgcolor", "plot_bgcolor", "coloraxis", "width", "height", "grid", "colorway",
    "uniformtext", "shapes",
} | AXIS_LAYOUT_KEYS
ALLOWED_AXIS_KEYS = {"title", "range", "type", "tickformat", "tickvals", "ticktext", "tickangle", "showgrid", "zeroline", "overlaying", "side", "automargin", "dtick", "domain", "anchor", "rangemode"}
ALLOWED_ANNOTATION_KEYS = {"text", "x", "y", "xref", "yref", "showarrow", "font", "ax", "ay"}
ALLOWED_COLORAXIS_KEYS = {"cmin", "cmax", "colorscale", "showscale", "colorbar"}
ALLOWED_TRACE_KEYS = {
    "type", "name", "x", "y", "z", "zmin", "zmax", "reversescale", "colorbar", "mode", "orientation", "marker", "line", "text",
    "showlegend", "coloraxis", "xaxis", "yaxis", "opacity", "width", "values",
    "labels", "insidetextorientation", "colorscale", "xgap", "ygap", "node", "link",
    "arrangement", "valueformat", "delta", "number", "title", "reference", "fill",
    "value", "domain", "boxmean", "parents", "open", "high", "low", "close", "measure",
    "histnorm", "histfunc", "nbinsx", "nbinsy", "autobinx", "autobiny", "cumulative",
    "hole", "sort", "direction", "rotation", "textinfo", "points", "box", "meanline",
    "notched", "boxpoints", "spanmode", "scalemode", "bandwidth", "jitter", "pointpos", "fillcolor", "legendgroup", "textposition",
}
ALLOWED_MARKER_KEYS = {"color", "size", "colorscale", "line", "opacity", "cmin", "cmid", "cmax", "showscale", "pattern", "symbol"}
ALLOWED_LINE_KEYS = {"color", "width"}
ALLOWED_FONT_KEYS = {"size", "color", "family"}
ALLOWED_MARGIN_KEYS = {"l", "r", "t", "b"}
ALLOWED_LEGEND_KEYS = {"orientation", "x", "y", "xanchor", "yanchor", "tracegroupgap"}
ALLOWED_INDICATOR_KEYS = {"mode", "value", "delta", "number", "title", "reference"}
ALLOWED_DELTA_KEYS = {"reference", "valueformat"}
ALLOWED_NUMBER_KEYS = {"valueformat"}
ALLOWED_TITLE_KEYS = {"text"}
ALLOWED_NODE_KEYS = {"label", "pad", "thickness", "color"}
ALLOWED_LINK_KEYS = {"source", "target", "value", "color"}
ALLOWED_UNIFORMTEXT_KEYS = {"mode", "minsize"}
ALLOWED_SHAPE_KEYS = {"type", "x0", "x1", "y0", "y1", "xref", "yref", "line"}
ALLOWED_SHAPE_LINE_KEYS = {"color", "width", "dash"}
ALLOWED_COLORBAR_KEYS = {"title"}
ALLOWED_DOMAIN_KEYS = {"x", "y"}
ALLOWED_VISIBLE_KEYS = {"visible"}
ALLOWED_CUMULATIVE_KEYS = {"enabled", "direction"}
MAX_TRACES = 12
MAX_SHAPES = 8
MAX_ANNOTATIONS = 8
MAX_COLORWAY = 12

FIXED_CONFIG = {"displaylogo": False, "responsive": True}
HOST_COLORSCALE = [[0.0, "#1f3a4d"], [1.0, "#4fd1c5"]]
RISK_COLORSCALE = [[0.0, "#1f3a4d"], [1.0, "#eda06a"]]

MAX_FIGURE_BYTES = 64 * 1024
MAX_FIGURES = 14
MAX_POINTS_PER_FIGURE = 6000
MAX_ITEMS_PER_ARRAY = 2000
MAX_HEATMAP_CELLS = 100 * 100
MAX_STRING_LENGTH = 200
MAX_TICK_ANGLE = 180
# Plotly serializes numeric arrays for a JavaScript consumer. Values outside
# this range cannot be represented exactly by that consumer's Number type.
MAX_SAFE_INTEGER = 2**53 - 1
_PLOTLY_DEFAULT_HOVERTEMPLATE = "x=%{x}<br>y=%{y}<extra></extra>"
_PLOTLY_DEFAULT_TEMPLATE_SHA256 = "ab7372edc8f18d8fde22eeab07518d1a7dffc240fe28d2511ed45e28535a52e3"
_TYPED_ARRAY_FORMATS = {
    "i1": ("b", 1), "u1": ("B", 1), "i2": ("h", 2), "u2": ("H", 2),
    "i4": ("i", 4), "u4": ("I", 4), "i8": ("q", 8), "u8": ("Q", 8),
    "f4": ("f", 4), "f8": ("d", 8),
}


def capability_summary() -> dict[str, Any]:
    """Return the exact bounded figure surface exposed to analysis authors."""
    return {
        "format": FIGURE_FORMAT,
        "traces": sorted(ALLOWED_TRACES),
        "layout_keys": sorted(ALLOWED_LAYOUT_KEYS),
        "axis_keys": sorted(ALLOWED_AXIS_KEYS),
        "trace_keys": sorted(ALLOWED_TRACE_KEYS),
        "nested_keys": {name.removeprefix("ALLOWED_").removesuffix("_KEYS").lower(): sorted(value)
                        for name, value in globals().items() if name.startswith("ALLOWED_") and name.endswith("_KEYS")},
        "forbidden_keys": sorted(FORBIDDEN_KEYS),
        "fixed_config": dict(FIXED_CONFIG),
        "example": {"data": [{"type": "bar", "x": ["label"], "y": [1]}], "layout": {"xaxis": {"tickangle": -35}}},
        "limits": {
            "max_figures": MAX_FIGURES,
            "max_figure_bytes": MAX_FIGURE_BYTES,
            "max_points_per_figure": MAX_POINTS_PER_FIGURE,
            "max_items_per_array": MAX_ITEMS_PER_ARRAY,
            "max_traces": MAX_TRACES, "max_shapes": MAX_SHAPES, "max_annotations": MAX_ANNOTATIONS,
            "max_colorway": MAX_COLORWAY, "max_string_length": MAX_STRING_LENGTH,
            "max_heatmap_cells": MAX_HEATMAP_CELLS, "max_safe_integer": MAX_SAFE_INTEGER,
            "tickangle": {"kind": "number_or_auto", "min": -MAX_TICK_ANGLE, "max": MAX_TICK_ANGLE},
        },
        "rules": [
            "Use plotly.graph_objects.Figure with only the needed keys, set layout.template=None, then emit(figure, name='chart.json'). Example values are illustrative, never analysis defaults.",
            "Key lists are not unrestricted Plotly schemas: the value restrictions below also apply. Unknown keys fail closed; host validation is final.",
            "Arrays must be nonempty, homogeneous strings or finite numbers; no NaN/Infinity/null. x/y may use either; OHLC/values/z require numbers. z is a bounded matrix. Integer precision and aggregate points are bounded; x and y each count toward points.",
            "tickangle is numeric in [-180,180] or auto. nbinsx/nbinsy are integers 1..max_items_per_array; hole is 0..1; boxmean is boolean or sd; points/boxpoints are false, all, outliers or suspectedoutliers.",
            "trace.mode is lines/markers/text/none joined by +. measure entries are relative/absolute/total. box/meanline.visible are boolean. cumulative.direction is increasing/decreasing; enabled is boolean.",
            "Styling colors are hex (#RGB/#RGBA/#RRGGBB/#RRGGBBAA); marker.color also accepts bounded numeric or hex-color arrays. size/width/opacity/cmin/cmid/cmax are scalar numbers. colorscale is a list of [number,color] stops, not a named palette; rgb() stops are normalized to hex.",
            "marker.symbol only circle; marker.pattern only {shape:''}; textposition only auto; legend.tracegroupgap only 0. Trace/marker line supports only color and width; shape line has its own separate keys and rules.",
            "domain has x/y numeric pairs. colorbar has only title (string or {text:string}); trace.title is {text:string}. Annotation values are scalar string/number/boolean, not nested font objects. Omit layout.grid and trace.arrangement: their current host scalar forms do not describe Plotly grid/arrangement objects.",
            "shapes supports only numeric line endpoints x0/x1/y0/y1 in a single Cartesian view; xref=x or x domain, yref=y or y domain. Shape line width 0..16; dash solid/dot/dash/longdash/dashdot/longdashdot.",
            "Forbidden keys must not be authored. Only the exact installed default template and default hovertemplate are stripped for compatibility; altered defaults are rejected. Use graph_objects instead of relying on Plotly Express defaults.",
            "Plain scientific comparisons such as p < 0.05 and Temperature > 100 are allowed, unchanged. Raw HTML tags/comments/declarations, links and executable schemes are rejected. Entity-encoded text retains its existing literal-text rendering. Plotly renders plain text through text nodes; do not add HTML markup. No animation, remote assets or custom config. This contract is not a guarantee of rendering or scientific correctness.",
        ],
    }


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


class _MarkupDetector(HTMLParser):
    has_markup = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.has_markup = True

    def handle_endtag(self, tag: str) -> None:
        self.has_markup = True

    def handle_comment(self, data: str) -> None:
        self.has_markup = True

    def handle_decl(self, decl: str) -> None:
        self.has_markup = True

    def handle_pi(self, data: str) -> None:
        self.has_markup = True

    def unknown_decl(self, data: str) -> None:
        self.has_markup = True


def contains_markup(value: str) -> bool:
    """Detect raw markup without rewriting text; callers own their length/policy bounds."""
    if "<" not in value:
        return False
    parser = _MarkupDetector()
    try:
        parser.feed(value)
        parser.close()
    except AssertionError:
        return True
    return parser.has_markup


def _check_string(value: str, where: str) -> str:
    if len(value) > MAX_STRING_LENGTH:
        _reject(f"string length exceeds {MAX_STRING_LENGTH} in {where}")
    lowered = value.lower()
    if contains_markup(value) or "javascript:" in lowered or lowered.startswith("http"):
        _reject(f"forbidden character or scheme in {where}")
    # Preserve category/parent identities and repeated validation. Plotly's
    # native text-node renderer, not mutation of stored data, escapes text.
    return value


def _check_color(value: Any, where: str) -> None:
    if not isinstance(value, str) or not _is_hex_color(value):
        _reject(f"color must be a hex string in {where}")


def _is_hex_color(value: str) -> bool:
    body = value[1:] if value.startswith("#") else ""
    return bool(body) and all(character in "0123456789abcdefABCDEF" for character in body) and len(body) in {3, 4, 6, 8}


def _check_number(value: Any, where: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(f"expected numeric value in {where}")
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            _reject(f"integer is not safely representable downstream in {where}")
        return value
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"plotly figure contract violation: expected numeric value in {where}: {exc}") from exc
    if not math.isfinite(number):
        _reject(f"expected finite numeric value in {where}")
    return number


def _check_integer(value: Any, where: str, *, minimum: int = 1, maximum: int = MAX_ITEMS_PER_ARRAY) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _reject(f"expected bounded integer in {where}")
    return value


def _decode_typed_array(value: Any, where: str, *, max_items: int) -> list[Any] | None:
    if not isinstance(value, dict):
        return None
    if set(value) != {"dtype", "bdata"}:
        _reject(f"unsupported typed array fields in {where}")
    dtype = value.get("dtype")
    encoded = value.get("bdata")
    spec = _TYPED_ARRAY_FORMATS.get(dtype) if isinstance(dtype, str) else None
    if spec is None:
        _reject(f"unsupported typed array dtype in {where}")
    if not isinstance(encoded, str) or not encoded:
        _reject(f"typed array base64 is invalid in {where}")
    item_format, item_size = spec
    max_encoded = ((max_items * item_size + 2) // 3) * 4
    if len(encoded) > max_encoded:
        _reject(f"points budget exceeded in {where}")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"plotly figure contract violation: typed array base64 is invalid in {where}") from exc
    if not payload or len(payload) % item_size:
        _reject(f"typed array byte length is invalid in {where}")
    count = len(payload) // item_size
    if count > max_items:
        _reject(f"points budget exceeded in {where}: {count} > {max_items}")
    try:
        return list(struct.unpack("<" + item_format * count, payload))
    except struct.error as exc:
        raise ValueError(f"plotly figure contract violation: typed array payload is invalid in {where}") from exc


def _decode_typed_matrix(value: dict[str, Any], where: str) -> list[list[Any]]:
    if set(value) != {"dtype", "bdata", "shape"}:
        _reject(f"unsupported typed matrix shape fields in {where}")
    shape = value["shape"]
    if not isinstance(shape, str) or len(shape) > 16:
        _reject(f"invalid typed matrix shape in {where}")
    parts = shape.split(",")
    if len(parts) != 2 or any(not part.strip().isdecimal() for part in parts):
        _reject(f"invalid typed matrix shape in {where}")
    rows, columns = map(int, parts)
    if rows < 1 or columns < 1 or rows * columns > MAX_HEATMAP_CELLS:
        _reject(f"typed matrix shape exceeds bounded cells in {where}")
    flat = _decode_typed_array({"dtype": value["dtype"], "bdata": value["bdata"]}, where, max_items=rows * columns)
    if flat is None or len(flat) != rows * columns:
        _reject(f"typed matrix shape does not match its payload in {where}")
    return [flat[index * columns:(index + 1) * columns] for index in range(rows)]


def _check_array(value: Any, where: str, *, max_items: int, numbers: bool) -> list[Any]:
    typed = _decode_typed_array(value, where, max_items=max_items)
    if typed is not None:
        value = typed
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


def _rgb_channel(value: str) -> int | None:
    try:
        channel = int(value, 10)
    except (TypeError, ValueError):
        return None
    return channel if 0 <= channel <= 255 else None


def _clean_colorscale(value: Any, where: str) -> list[list[Any]]:
    if not isinstance(value, list) or not value:
        _reject(f"invalid colorscale in {where}")
    cleaned = []
    for stop in value:
        if not isinstance(stop, (list, tuple)) or len(stop) != 2:
            _reject(f"invalid colorscale stop in {where}")
        position = _check_number(stop[0], where)
        color = stop[1]
        if isinstance(color, str):
            rgb = re.fullmatch(r"rgb\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*\)", color)
            channels = [_rgb_channel(channel) for channel in rgb.groups()] if rgb else []
            if channels and all(channel is not None for channel in channels):
                color = "#" + "".join(f"{channel:02x}" for channel in channels)
        _check_color(color, where)
        cleaned.append([position, color])
    return cleaned


def _count_trace_points(budget: dict[str, int], values: list[Any], where: str) -> None:
    budget["points"] += len(values)
    if budget["points"] > MAX_POINTS_PER_FIGURE:
        _reject(f"points budget exceeded for figure: {budget['points']} > {MAX_POINTS_PER_FIGURE}")


def _clean_trace(trace: Any, budget: dict[str, int]) -> dict[str, Any]:
    if isinstance(trace, dict) and trace.get("hovertemplate") == _PLOTLY_DEFAULT_HOVERTEMPLATE:
        trace = {key: value for key, value in trace.items() if key != "hovertemplate"}
    _check_mapping_keys(trace, ALLOWED_TRACE_KEYS, "trace")
    trace_type = str(trace.get("type") or "")
    if trace_type not in ALLOWED_TRACES:
        _reject(f"unsupported trace type {trace_type!r}")
    cleaned: dict[str, Any] = {"type": trace_type}
    for key, value in trace.items():
        where = f"trace.{key}"
        if key in {"type"}:
            continue
        if key in {"x", "y", "text", "labels", "parents", "open", "high", "low", "close"}:
            numbers_only = key in {"open", "high", "low", "close"} or (key in {"x", "y"} and _looks_numeric(value))
            cleaned[key] = _check_array(value, where, max_items=MAX_ITEMS_PER_ARRAY, numbers=numbers_only)
            _count_trace_points(budget, cleaned[key], where)
            continue
        if key in {"z"}:
            if isinstance(value, dict):
                value = _decode_typed_matrix(value, where)
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
        if key == "measure":
            cleaned[key] = _check_array(value, where, max_items=MAX_ITEMS_PER_ARRAY, numbers=False)
            if any(item not in {"relative", "absolute", "total"} for item in cleaned[key]):
                _reject(f"unsupported waterfall measure in {where}")
            _count_trace_points(budget, cleaned[key], where)
            continue
        if key == "boxmean":
            if not isinstance(value, bool) and value != "sd":
                _reject(f"boxmean must be a boolean or 'sd' in {where}")
            cleaned[key] = value
            continue
        if key in {"box", "meanline"}:
            nested = _check_mapping_keys(value, ALLOWED_VISIBLE_KEYS, where)
            if not isinstance(nested.get("visible"), bool):
                _reject(f"{where}.visible must be boolean")
            cleaned[key] = {"visible": nested["visible"]}
            continue
        if key == "cumulative":
            nested = _check_mapping_keys(value, ALLOWED_CUMULATIVE_KEYS, where)
            cleaned_cumulative = {"enabled": bool(nested.get("enabled", False))}
            if "direction" in nested:
                if nested["direction"] not in {"increasing", "decreasing"}:
                    _reject(f"unsupported cumulative direction in {where}")
                cleaned_cumulative["direction"] = nested["direction"]
            cleaned[key] = cleaned_cumulative
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
        if key == "colorbar":
            cleaned[key] = _clean_colorbar(value, where)
            continue
        if key in {"reference", "value", "zmin", "zmax"}:
            cleaned[key] = _check_number(value, where)
            continue
        if key == "domain":
            if not isinstance(value, dict):
                _reject(f"expected domain object in {where}")
            cleaned_domain: dict[str, Any] = {}
            for domain_key, domain_value in value.items():
                if domain_key not in ALLOWED_DOMAIN_KEYS or not isinstance(domain_value, list) or len(domain_value) != 2:
                    _reject(f"invalid domain in {where}.{domain_key}")
                cleaned_domain[str(domain_key)] = [_check_number(bound, f"{where}.{domain_key}") for bound in domain_value]
            cleaned[key] = cleaned_domain
            continue
        if key == "coloraxis":
            cleaned[key] = _check_string(str(value), where)
            continue
        if key in {"nbinsx", "nbinsy"}:
            cleaned[key] = _check_integer(value, where)
            continue
        if key == "hole":
            cleaned[key] = _check_number(value, where)
            if not 0 <= cleaned[key] <= 1:
                _reject(f"hole must be between 0 and 1 in {where}")
            continue
        if key in {"opacity", "width", "xgap", "ygap", "arrangement", "rotation", "bandwidth", "jitter", "pointpos", "spanmode", "scalemode"}:
            if key in {"spanmode", "scalemode"}:
                cleaned[key] = _check_string(str(value), where)
            else:
                cleaned[key] = _check_number(value, where)
            continue
        if key in {"histnorm", "histfunc", "direction", "textinfo", "orientation", "xaxis", "yaxis", "valueformat", "insidetextorientation", "fill"}:
            cleaned[key] = _check_string(str(value), where)
            continue
        if key == "legendgroup":
            cleaned[key] = _check_string(value, where)
            continue
        if key == "textposition":
            if value != "auto":
                _reject(f"unsupported Plotly default in {where}")
            cleaned[key] = value
            continue
        if key in {"autobinx", "autobiny", "sort", "notched", "reversescale"}:
            if not isinstance(value, bool):
                _reject(f"expected boolean in {where}")
            cleaned[key] = value
            continue
        if key in {"points", "boxpoints"}:
            if isinstance(value, bool):
                if value:
                    _reject(f"unsupported points mode in {where}")
            elif not isinstance(value, str) or value not in {"all", "outliers", "suspectedoutliers"}:
                _reject(f"unsupported points mode in {where}")
            cleaned[key] = value
            continue
        if key == "fillcolor":
            _check_color(value, where)
            cleaned[key] = value
            continue
        if key == "showlegend":
            cleaned[key] = bool(value)
            continue
        _reject(f"unsupported trace key {key!r}")
    if budget["points"] > MAX_POINTS_PER_FIGURE:
        _reject(f"points budget exceeded for figure: {budget['points']} > {MAX_POINTS_PER_FIGURE}")
    return cleaned


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, dict):
        return isinstance(value.get("dtype"), str) and value.get("dtype") in _TYPED_ARRAY_FORMATS
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
        if key == "symbol":
            if nested_value != "circle":
                _reject(f"unsupported Plotly default in {nested_where}")
            cleaned[key] = nested_value
            continue
        if key == "pattern":
            if nested_value != {"shape": ""}:
                _reject(f"unsupported Plotly default in {nested_where}")
            cleaned[key] = {"shape": ""}
            continue
        _reject(f"unsupported {where} key {key!r}")
    return cleaned


def _clean_colorbar(value: Any, where: str) -> dict[str, Any]:
    checked = _check_mapping_keys(value, ALLOWED_COLORBAR_KEYS, where)
    if "title" not in checked:
        return {}
    title = checked["title"]
    if isinstance(title, dict):
        title = _check_mapping_keys(title, ALLOWED_TITLE_KEYS, f"{where}.title").get("text", "")
    return {"title": {"text": _check_string(title, f"{where}.title.text")}}


def _clean_shapes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_SHAPES:
        _reject("shape budget exceeded")
    cleaned = []
    for shape in value:
        checked = _check_mapping_keys(shape, ALLOWED_SHAPE_KEYS, "shape")
        if checked.get("type") != "line":
            _reject("only numeric line shapes are supported")
        item: dict[str, Any] = {key: _check_number(checked.get(key), f"shape.{key}") for key in ("x0", "x1", "y0", "y1")}
        item["type"] = "line"
        for axis in ("x", "y"):
            key = f"{axis}ref"
            if key in checked:
                reference = checked[key]
                if not isinstance(reference, str) or reference not in {axis, f"{axis} domain"}:
                    _reject("unsupported shape reference")
                item[key] = reference
        if "line" in checked:
            line = _check_mapping_keys(checked["line"], ALLOWED_SHAPE_LINE_KEYS, "shape.line")
            if "color" in line:
                _check_color(line["color"], "shape.line.color")
            if "width" in line and not 0 <= _check_number(line["width"], "shape.line.width") <= 16:
                _reject("shape line width must be between 0 and 16")
            if "dash" in line and (not isinstance(line["dash"], str) or line["dash"] not in {"solid", "dot", "dash", "longdash", "dashdot", "longdashdot"}):
                _reject("unsupported shape line dash")
            item["line"] = dict(line)
        cleaned.append(item)
    return cleaned


def _clean_layout(layout: Any) -> dict[str, Any]:
    if not isinstance(layout, dict):
        _reject("layout must be an object")
    # Plotly adds one deterministic default template. Do not silently discard
    # a producer mutation: only this exact installed-default fingerprint is
    # host styling, while every other template fails closed.
    if "template" in layout:
        template = layout["template"]
        if not isinstance(template, dict):
            _reject("layout.template is not the installed Plotly default")
        digest = hashlib.sha256(json.dumps(template, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if digest != _PLOTLY_DEFAULT_TEMPLATE_SHA256:
            _reject("layout.template is not the installed Plotly default")
        layout = {key: value for key, value in layout.items() if key != "template"}
    _check_mapping_keys(layout, ALLOWED_LAYOUT_KEYS, "layout")
    cleaned: dict[str, Any] = {}
    for key, value in layout.items():
        where = f"layout.{key}"
        if value is None:
            continue
        if key == "title":
            title_text = value.get("text", "") if isinstance(value, dict) else value
            cleaned[key] = _check_string(str(title_text), where)
            continue
        if key in AXIS_LAYOUT_KEYS:
            axis = _check_mapping_keys(value, ALLOWED_AXIS_KEYS, where)
            cleaned_axis: dict[str, Any] = {}
            for axis_key, axis_value in axis.items():
                axis_where = f"{where}.{axis_key}"
                if axis_key == "title" and isinstance(axis_value, dict):
                    axis_value = axis_value.get("text", "")
                if axis_key == "tickangle":
                    if axis_value == "auto":
                        cleaned_axis[axis_key] = axis_value
                    elif isinstance(axis_value, (int, float)) and not isinstance(axis_value, bool) and -MAX_TICK_ANGLE <= axis_value <= MAX_TICK_ANGLE:
                        cleaned_axis[axis_key] = _check_number(axis_value, axis_where)
                    else:
                        _reject(f"tickangle must be 'auto' or a number between {-MAX_TICK_ANGLE} and {MAX_TICK_ANGLE}")
                elif isinstance(axis_value, str):
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
        if key == "shapes":
            cleaned[key] = _clean_shapes(value)
            continue
        if key == "annotations":
            if not isinstance(value, list) or len(value) > MAX_ANNOTATIONS:
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
                if legend_key == "tracegroupgap":
                    if legend_value != 0:
                        _reject(f"unsupported Plotly default in {where}.{legend_key}")
                    cleaned_legend[legend_key] = 0
                else:
                    cleaned_legend[legend_key] = _check_string(str(legend_value), f"{where}.{legend_key}") if isinstance(legend_value, str) else _check_number(legend_value, f"{where}.{legend_key}")
            cleaned[key] = cleaned_legend
            continue
        if key == "coloraxis":
            checked = _check_mapping_keys(value, ALLOWED_COLORAXIS_KEYS, where)
            cleaned_coloraxis: dict[str, Any] = {}
            for coloraxis_key, coloraxis_value in checked.items():
                if coloraxis_key == "colorbar":
                    cleaned_coloraxis[coloraxis_key] = _clean_colorbar(coloraxis_value, f"{where}.colorbar")
                elif coloraxis_key == "colorscale":
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
            cleaned[key] = _check_array(value, where, max_items=MAX_COLORWAY, numbers=False)
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
    if len(data) > MAX_TRACES:
        _reject(f"figure data exceeds {MAX_TRACES} traces")
    budget = {"points": 0}
    cleaned_data = [_clean_trace(trace, budget) for trace in data]
    if budget["points"] > MAX_POINTS_PER_FIGURE:
        _reject(f"points budget exceeded for figure: {budget['points']} > {MAX_POINTS_PER_FIGURE}")
    cleaned = {"data": cleaned_data, "layout": _clean_layout(figure.get("layout") or {}), "config": dict(FIXED_CONFIG)}
    # ponytail: reference lines support one Cartesian view; remap/filter refs before supporting subplot shapes.
    if cleaned["layout"].get("shapes") and any(
        trace.get("xaxis", "x") != "x" or trace.get("yaxis", "y") != "y"
        or trace["type"] in {"indicator", "pie", "sankey", "sunburst", "treemap", "funnelarea"}
        for trace in cleaned_data
    ):
        _reject("reference lines require a single Cartesian view")
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
