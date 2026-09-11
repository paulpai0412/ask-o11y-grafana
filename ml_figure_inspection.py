"""Bounded semantic inspection specs for sanitized Plotly figures and PNG evidence."""
from __future__ import annotations

import math
import re
from typing import Any

import ml_plotly_contract  # type: ignore[reportMissingImports]


def _number(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{where} is non-finite")
    return number


def _axis_layout_key(axis_ref: str, axis: str) -> str:
    suffix = axis_ref.removeprefix(axis)
    return f"{axis}axis{suffix}" if suffix else f"{axis}axis"


def _axis_title(axis: dict[str, Any]) -> tuple[str | None, str | None]:
    raw = axis.get("title")
    title = raw.get("text") if isinstance(raw, dict) else raw
    if not isinstance(title, str) or not title.strip():
        return None, None
    match = re.fullmatch(r"\s*(.*?)\s*[（(]([^()（）]+)[）)]\s*", title)
    return (match.group(1).strip(), match.group(2).strip()) if match else (title.strip(), None)


def _numeric_extent(values: Any) -> tuple[float | None, float | None]:
    if not isinstance(values, list):
        return None, None
    numbers = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numbers.append(_number(value, "figure value"))
    return (min(numbers), max(numbers)) if numbers else (None, None)


def _matrix_values(value: Any) -> list[float]:
    output = []
    if not isinstance(value, list):
        return output
    for row in value:
        if isinstance(row, list):
            for cell in row:
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    output.append(_number(cell, "figure matrix value"))
    return output


def _point_count(trace: dict[str, Any]) -> int:
    matrix_count = len(_matrix_values(trace.get("z")))
    lengths = [
        len(trace[key])
        for key in ("x", "y", "labels", "values", "parents", "open", "high", "low", "close")
        if isinstance(trace.get(key), list)
    ]
    return max([matrix_count, *lengths], default=0)


def inspect_figure(artifact_id: str, figure: dict[str, Any], *, whole: bool = False) -> dict[str, Any]:
    """New reports inspect the complete native figure; v1 view identities stay stable."""
    sanitized = ml_plotly_contract.sanitize_figure(figure, legacy=not whole)
    data = sanitized["data"]
    layout = sanitized["layout"]
    if whole:
        title = layout.get("title")
        title = title.get("text") if isinstance(title, dict) else title
        return {"artifact_id": artifact_id, "kind": "plotly", "title": title,
                "trace_count": len(data),
                "views": [{"view_id": "figure", "title": title or artifact_id, "kind": "plotly",
                           "trace_types": list(dict.fromkeys(t.get("type", "scatter") for t in data)),
                           "trace_count": len(data)}],
                "inspection_note": "Complete native figure supplied; point counts and axes are not inferred across different native trace types. Spec inspection is not visual verification."}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace in data:
        x_ref = str(trace.get("xaxis") or "x")
        y_ref = str(trace.get("yaxis") or "y")
        grouped.setdefault((x_ref, y_ref), []).append(trace)
    views = []
    for index, ((x_ref, y_ref), traces) in enumerate(grouped.items(), start=1):
        x_axis = layout.get(_axis_layout_key(x_ref, "x")) or {}
        y_axis = layout.get(_axis_layout_key(y_ref, "y")) or {}
        x_label, x_unit = _axis_title(x_axis)
        y_label, y_unit = _axis_title(y_axis)
        x_values = [value for trace in traces for value in (trace.get("x") or [])] if all(isinstance(trace.get("x") or [], list) for trace in traces) else []
        y_values = [value for trace in traces for value in (trace.get("y") or [])] if all(isinstance(trace.get("y") or [], list) for trace in traces) else []
        x_min, x_max = _numeric_extent(x_values)
        y_min, y_max = _numeric_extent(y_values)
        matrix_values = [value for trace in traces for value in _matrix_values(trace.get("z"))]
        trace_names = [str(trace.get("name")) for trace in traces if trace.get("name")]
        trace_types = list(dict.fromkeys(str(trace.get("type")) for trace in traces))
        x_scale = str(x_axis.get("type") or ("category" if any(isinstance(value, str) for value in x_values) else "linear"))
        y_scale = str(y_axis.get("type") or ("category" if any(isinstance(value, str) for value in y_values) else "linear"))
        views.append({
            "view_id": f"view-{index}",
            "title": " / ".join(trace_names) or f"{artifact_id} view {index}",
            "trace_types": trace_types,
            "trace_count": len(traces),
            "point_count": sum(_point_count(trace) for trace in traces),
            "x": {
                "label": x_label, "unit": x_unit, "scale": x_scale,
                "range": [_number(value, "x axis range") for value in x_axis["range"]] if isinstance(x_axis.get("range"), list) else None,
                "tickformat": x_axis.get("tickformat"), "data_min": x_min, "data_max": x_max,
            },
            "y": {
                "label": y_label, "unit": y_unit, "scale": y_scale,
                "range": [_number(value, "y axis range") for value in y_axis["range"]] if isinstance(y_axis.get("range"), list) else None,
                "tickformat": y_axis.get("tickformat"), "data_min": y_min, "data_max": y_max,
            },
            "value_min": min(matrix_values) if matrix_values else None,
            "value_max": max(matrix_values) if matrix_values else None,
        })
    return {
        "artifact_id": artifact_id,
        "kind": "plotly",
        "title": layout.get("title"),
        "trace_count": len(data),
        "point_count": sum(_point_count(trace) for trace in data),
        "views": views,
    }


def inspect_png_only(artifact_id: str, title: str) -> dict[str, Any]:
    return {"artifact_id": artifact_id, "kind": "image", "trace_count": 0, "point_count": 0, "views": [{"view_id": "image", "title": title, "kind": "image"}]}
