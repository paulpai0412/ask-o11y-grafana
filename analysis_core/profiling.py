"""Deterministic dataset profiling."""
from __future__ import annotations

from typing import Any

import pandas as pd  # pyright: ignore[reportMissingImports]

from .frame import AnalysisCoreError, scalar_float


def profile_dataframe(data: pd.DataFrame, fields: list[str] | None = None) -> dict[str, Any]:
    selected = fields or [str(column) for column in data.columns]
    unknown = [field for field in selected if field not in data.columns]
    if unknown:
        raise AnalysisCoreError("unknown profile fields: " + ", ".join(unknown))
    if data.empty:
        raise AnalysisCoreError("cannot profile an empty dataframe")
    summaries = []
    for field in selected:
        column = data.loc[:, field]
        if not isinstance(column, pd.Series):
            raise AnalysisCoreError(f"field is not a scalar column: {field}")
        numeric = pd.Series(pd.to_numeric(column, errors="coerce"), name=field)
        numeric_count = len(numeric.dropna())
        non_null = len(column.dropna())
        summary: dict[str, Any] = {"field": field, "rows": len(column), "non_null": non_null, "missing": len(column) - non_null, "unique": len(column.dropna().unique()), "kind": "numeric" if numeric_count == non_null and numeric_count > 0 else "categorical"}
        if summary["kind"] == "numeric":
            values = numeric.dropna()
            summary.update({"mean": scalar_float(values.mean(), f"{field} mean"), "std": scalar_float(values.std(), f"{field} std") if len(values) > 1 else 0.0, "min": scalar_float(values.min(), f"{field} min"), "median": scalar_float(values.median(), f"{field} median"), "max": scalar_float(values.max(), f"{field} max")})
        summaries.append(summary)
    return {"rows": len(data), "columns": len(selected), "fields": summaries, "validation": {"ok": True, "checks": ["non-empty dataframe", "known selected fields", "missingness and type profile"]}}
