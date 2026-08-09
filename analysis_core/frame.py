"""Pure columnar-frame validation and pandas conversion."""
from __future__ import annotations

from typing import Any

import pandas as pd  # pyright: ignore[reportMissingImports]


class AnalysisCoreError(ValueError):
    """Raised when deterministic analysis input violates its contract."""


def scalar_float(value: Any, label: str) -> float:
    if isinstance(value, pd.Series):
        raise AnalysisCoreError(f"{label} must be a scalar")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisCoreError(f"{label} must be numeric") from exc


def scalar_int(value: Any, label: str) -> int:
    if isinstance(value, pd.Series):
        raise AnalysisCoreError(f"{label} must be a scalar")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisCoreError(f"{label} must be an integer") from exc


def dataframe_from_columnar_frame(frame: dict[str, Any]) -> pd.DataFrame:
    fields = frame.get("schema", {}).get("fields")
    values = frame.get("data", {}).get("values")
    if not isinstance(fields, list) or not isinstance(values, list) or len(fields) != len(values):
        raise AnalysisCoreError("columnar frame fields/values must be equal-length arrays")
    names = [field.get("name") if isinstance(field, dict) else None for field in fields]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise AnalysisCoreError("columnar frame field names must be unique non-empty strings")
    if any(not isinstance(column, list) for column in values):
        raise AnalysisCoreError("columnar frame values must be arrays")
    lengths = {len(column) for column in values}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise AnalysisCoreError("columnar frame columns must have one shared non-zero row count")
    return pd.DataFrame(dict(zip(names, values, strict=True)))


def numeric_series(data: pd.DataFrame, field: str, minimum_rows: int) -> pd.Series:
    if field not in data.columns:
        raise AnalysisCoreError(f"unknown field: {field}")
    raw = data.loc[:, field]
    if not isinstance(raw, pd.Series):
        raise AnalysisCoreError(f"field is not a scalar column: {field}")
    numeric = pd.Series(pd.to_numeric(raw, errors="coerce"), name=field)
    if len(numeric.dropna()) < minimum_rows:
        raise AnalysisCoreError(f"insufficient numeric rows for field: {field}")
    return numeric
