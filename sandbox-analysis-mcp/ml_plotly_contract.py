"""Native Plotly figures with the existing offline, static-presentation boundary.

No trace/layout feature allowlist. Legacy normalization is read-only compatibility
for already retained v1 manifest hashes; new reports never use that path.
"""
from __future__ import annotations

from array import array
import base64
import binascii
from html.parser import HTMLParser
import json
import math
import importlib.util
from pathlib import Path
import struct
from typing import Any, Literal

import plotly
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version
# Explicit file loading also works when the MCP host loads this module by path.
_legacy_spec = importlib.util.spec_from_file_location("_plotly_legacy", Path(__file__).with_name("_plotly_legacy.py"))
if _legacy_spec is None or _legacy_spec.loader is None:
    raise ImportError("legacy Plotly reader is unavailable")
_legacy = importlib.util.module_from_spec(_legacy_spec)
_legacy_spec.loader.exec_module(_legacy)
contains_markup = _legacy.contains_markup  # shared report-text policy, unchanged

PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"
FIGURE_FORMAT = "ask-o11y-ml-plotly-v2"
FIXED_CONFIG = {"displaylogo": False, "responsive": True}
# Reuse the captured execution byte ceiling, not speculative trace/point limits.
MAX_FIGURE_BYTES = 5 * 1024 * 1024
MAX_SAFE_INTEGER = 2**53 - 1
_TYPED_ARRAY_FORMATS: dict[str, Literal["b", "B", "h", "H", "i", "I", "q", "Q", "f", "d"]] = {"i1": "b", "u1": "B", "i2": "h", "u2": "H", "i4": "i", "u4": "I", "i8": "q", "u8": "Q", "f4": "f", "f8": "d"}
_COORDINATES = {"x", "y", "z", "r", "theta", "lat", "lon", "open", "high", "low", "close"}
# These renderers fetch map tiles/topojson by default, even without an explicit URL.
_NETWORK_TRACES = {"scattergeo", "choropleth", "scattermapbox", "choroplethmapbox", "densitymapbox", "scattermap", "choroplethmap", "densitymap"}


def capability_summary() -> dict[str, Any]:
    return {
        "format": FIGURE_FORMAT, "plotly_python_version": plotly.__version__, "plotly_js_version": get_plotlyjs_version(),
        "presentation": "native static Plotly figure; preserve the complete figure and missing-coordinate gaps",
        "max_figure_bytes": MAX_FIGURE_BYTES,
        "rules": ["Emit the Plotly figure with a name ending in .json. No custom JavaScript or network assets. Use the installed Plotly version; no custom trace/layout vocabulary. A figure error is separate from successful computation and must remain visible."],
    }


class FigureError(ValueError):
    """Safe, bounded error to expose without echoing arbitrary figure contents."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"plotly figure unavailable: {code}")


class _ActiveMarkup(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "iframe", "object", "embed", "img", "svg", "math", "style", "link", "base"} or any(name.startswith("on") or name in {"href", "src", "style"} for name, _ in attrs):
            raise FigureError("active_or_external_content")


def _decode_array(value: dict[str, Any]) -> Any:
    if not {"dtype", "bdata"} <= value.keys() or set(value) - {"dtype", "bdata", "shape"}:
        raise FigureError("invalid_typed_array")
    fmt = _TYPED_ARRAY_FORMATS.get(value["dtype"]) if isinstance(value["dtype"], str) else None
    if fmt is None or not isinstance(value["bdata"], str):
        raise FigureError("invalid_typed_array")
    try:
        raw = base64.b64decode(value["bdata"], validate=True)
        size = struct.calcsize("<" + fmt)
        if len(raw) % size:
            raise FigureError("invalid_typed_array")
        decoded = list(struct.unpack("<" + fmt * (len(raw) // size), raw))
    except (ValueError, binascii.Error, struct.error) as exc:
        raise FigureError("invalid_typed_array") from exc
    if "shape" in value:
        try:
            shape = [int(part.strip()) for part in value["shape"].split(",")]
        except (AttributeError, TypeError, ValueError) as exc:
            raise FigureError("invalid_typed_shape") from exc
        if not shape or any(n < 1 for n in shape) or math.prod(shape) != len(decoded):
            raise FigureError("invalid_typed_shape")
        decoded = memoryview(array(fmt, decoded)).cast("B").cast(fmt, shape=shape).tolist()
    return decoded


def _static_value(value: Any, *, coordinate: bool = False, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        if {"bdata", "dtype"} <= value.keys():
            return _static_value(_decode_array(value), coordinate=coordinate, path=path)
        out = {}
        for key, item in value.items():
            # Actual resource-fetch fields, not text containing a word such as 'script'.
            if key == "source" and ("images" in path or "layers" in path or value.get("type") == "image") and isinstance(item, str) and item:
                raise FigureError("external_resource")
            out[key] = _static_value(item, coordinate=coordinate or (path == ("data",) and key in _COORDINATES), path=(*path, key))
        return out
    if isinstance(value, list):
        return [_static_value(item, coordinate=coordinate, path=path) for item in value]
    if isinstance(value, str):
        try:
            parser = _ActiveMarkup(convert_charrefs=True)
            parser.feed(value)
            parser.close()
        except FigureError:
            raise
        except (ValueError, AssertionError) as exc:
            raise FigureError("invalid_text") from exc
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise FigureError("unsafe_integer_precision")
        return value
    if isinstance(value, float):
        if math.isnan(value) and coordinate:
            return None
        if not math.isfinite(value):
            raise FigureError("nonfinite_value")
        return value
    raise FigureError("not_json_data")


def sanitize_figure(figure: Any, *, legacy: bool = False) -> dict[str, Any]:
    if legacy:
        return _legacy.sanitize_figure(figure)
    if not isinstance(figure, dict) or set(figure) - {"data", "layout", "config"}:
        raise FigureError("invalid_figure")
    if "config" in figure and figure["config"] != FIXED_CONFIG:
        raise FigureError("runtime_config")
    try:
        if len(json.dumps(figure, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_FIGURE_BYTES:
            raise FigureError("output_size")
        clean = _static_value(figure)
        # Native schema validation: no skip_invalid and no hand-written feature list.
        # Discard its canonical form: it injects defaults and can rewrite producer text.
        data = clean.get("data")
        if not isinstance(data, list) or not data:
            raise FigureError("empty_figure")
        if any(isinstance(trace, dict) and trace.get("type") in _NETWORK_TRACES for trace in data):
            raise FigureError("network_renderer")
        go.Figure(data=data, layout=clean.get("layout") or {}, skip_invalid=False)
        clean.setdefault("layout", {})
        clean["config"] = dict(FIXED_CONFIG)
        if len(json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()) > MAX_FIGURE_BYTES:
            raise FigureError("output_size")
        return clean
    except FigureError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise FigureError("invalid_native_figure") from exc


def sanitize_execution(figures: Any) -> list[dict[str, Any]]:
    if not isinstance(figures, list):
        raise FigureError("invalid_figure_collection")
    return [sanitize_figure(figure) for figure in figures]
