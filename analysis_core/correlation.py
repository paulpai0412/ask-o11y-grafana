"""Pure deterministic correlation mechanics."""
from __future__ import annotations

from typing import Any, Literal

import pandas as pd  # pyright: ignore[reportMissingImports]

from .frame import AnalysisCoreError, numeric_series


def _scalar(value: Any, label: str) -> float:
    if isinstance(value, pd.Series):
        raise AnalysisCoreError(f"{label} must be a scalar")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisCoreError(f"{label} must be numeric") from exc


CorrelationMethod = Literal["pearson", "spearman"]


def paired_statistics(left: pd.Series, right: pd.Series, *, minimum_rows: int, method: CorrelationMethod = "pearson") -> dict[str, float | int]:
    if method not in {"pearson", "spearman"}:
        raise AnalysisCoreError("correlation method must be pearson or spearman")
    pair = pd.DataFrame({"left": left, "right": right}).dropna()
    pair_left, pair_right = pair.loc[:, "left"], pair.loc[:, "right"]
    if not isinstance(pair_left, pd.Series) or not isinstance(pair_right, pd.Series):
        raise AnalysisCoreError("paired fields must be scalar columns")
    if len(pair) < minimum_rows:
        raise AnalysisCoreError("insufficient paired numeric rows")
    left_variance = _scalar(pair_left.var(), "left variance")
    right_variance = _scalar(pair_right.var(), "right variance")
    if left_variance == 0 or right_variance == 0:
        raise AnalysisCoreError("paired fields must have non-zero variance")
    return {
        "rows": len(pair),
        "correlation": _scalar(pair_left.corr(other=pair_right, method=method), "correlation"),
        "covariance": _scalar(pair_left.cov(pair_right), "covariance"),
        "left_variance": left_variance,
        "right_variance": right_variance,
    }


def pairwise_correlation(data: pd.DataFrame, fields: list[str], *, method: CorrelationMethod = "pearson", minimum_rows: int = 20) -> dict[str, Any]:
    if len(fields) < 2 or len(set(fields)) != len(fields):
        raise AnalysisCoreError("pairwise correlation requires at least two unique fields")
    columns = {field: numeric_series(data, field, minimum_rows) for field in fields}
    non_null = {field: len(column.dropna()) for field, column in columns.items()}
    values = {field: {field: 1.0} for field in fields}
    pairs = []
    for index, left in enumerate(fields):
        for right in fields[index + 1 :]:
            stats = paired_statistics(columns[left], columns[right], minimum_rows=minimum_rows, method=method)
            try:
                correlation = float(stats["correlation"])
                paired_rows = int(stats["rows"])
            except (TypeError, ValueError) as exc:
                raise AnalysisCoreError("paired statistics returned invalid scalars") from exc
            values[left][right] = correlation
            values.setdefault(right, {})[left] = correlation
            pairs.append({"source": left, "target": right, "correlation": correlation, "paired_rows": paired_rows})
    pairs.sort(key=lambda item: abs(item["correlation"]), reverse=True)
    cells = [{"source": row, "target": column, "correlation": values[row][column]} for row in fields for column in fields]
    return {"fields": fields, "method": method, "minimum_rows": minimum_rows, "non_null_rows": non_null, "pairs": pairs, "cells": cells, "matrix": [[values[row][column] for column in fields] for row in fields]}
