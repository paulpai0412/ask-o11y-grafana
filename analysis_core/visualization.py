"""Validated declarative visualization specifications."""
from __future__ import annotations

from typing import Any, Literal

from .frame import AnalysisCoreError

VisualizationKind = Literal["table", "timeseries", "bar", "scatter", "heatmap"]


def visualization_spec(kind: VisualizationKind, *, title: str, data_frame: str, fields: dict[str, Any]) -> dict[str, Any]:
    if not title or not data_frame:
        raise AnalysisCoreError("visualization title and data_frame are required")
    required = {"table": set(), "timeseries": {"x", "y"}, "bar": {"x", "y"}, "scatter": {"x", "y"}, "heatmap": {"source", "target", "value"}}[kind]
    missing = sorted(required - set(fields))
    if missing:
        raise AnalysisCoreError(f"{kind} visualization missing fields: {missing}")
    spec = {"type": kind, "title": title, "data_frame": data_frame, **fields}
    if kind == "heatmap":
        spec["plugin_id"] = "esnet-matrix-panel"
    return spec
