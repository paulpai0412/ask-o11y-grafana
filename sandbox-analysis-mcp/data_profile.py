"""Deterministic full-data profiling for the generic analysis capability.

The profile operates on every row in an authorized columnar frame. Chart bins
are presentation-only aggregations; no sampled or derived dataset is emitted.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROFILE_FORMAT = "ask-o11y-data-profile-v1"
MAX_FIELDS = 200
MAX_CORRELATION_FIELDS = 12
MAX_DISTRIBUTION_FIELDS = 12
MAX_TREND_FIELDS = 4
MAX_TREND_POINTS = 24
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_MISSING = {"", "?", "na", "n/a", "null", "none"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in _MISSING
    try:
        return bool(math.isnan(float(value)))
    except (TypeError, ValueError, OverflowError):
        return False


def _number(value: Any) -> float | None:
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not _NUMERIC_RE.fullmatch(text):
            return None
        try:
            parsed = float(text)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed if math.isfinite(parsed) else None


def _datetime(value: Any) -> datetime | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(timestamp):
            return None
        if abs(timestamp) > 100_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError, OverflowError):
        return None


def _value_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires non-empty values")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None, "std": None, "cv": None, "skew": None, "q1": None, "median": None, "q3": None, "distinct": 0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    third = sum((value - mean) ** 3 for value in values) / len(values)
    skew = 0.0 if std == 0 else third / (std ** 3)
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "std": std,
        "cv": None if mean == 0 else abs(std / mean),
        "skew": skew if math.isfinite(skew) else 0.0,
        "q1": _quantile(values, 0.25),
        "median": _quantile(values, 0.5),
        "q3": _quantile(values, 0.75),
        "distinct": len(set(values)),
    }


def _categorical_summary(values: list[Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for value in values:
        key = _value_key(value)
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.values(), reverse=True)
    total = len(values)
    top_share = ordered[0] / total if ordered else 0.0
    top3_share = sum(ordered[:3]) / total if ordered else 0.0
    return {
        "count": total,
        "distinct": len(counts),
        "top_share": top_share,
        "top3_share": top3_share,
    }


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in range(cursor, end):
            ranks[ordered[index][0]] = rank
        cursor = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_dev = [value - left_mean for value in left]
    right_dev = [value - right_mean for value in right]
    denominator = math.sqrt(sum(value * value for value in left_dev) * sum(value * value for value in right_dev))
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(left_dev, right_dev, strict=True)) / denominator


def _spearman(left: Sequence[Any], right: Sequence[Any]) -> float | None:
    pairs = [(_number(a), _number(b)) for a, b in zip(left, right, strict=True)]
    complete = [(a, b) for a, b in pairs if a is not None and b is not None]
    if len(complete) < 2:
        return None
    left_ranks = _rank([pair[0] for pair in complete])
    right_ranks = _rank([pair[1] for pair in complete])
    return _pearson(left_ranks, right_ranks)


def _metadata(fields_view: Sequence[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in fields_view or []:
        if not isinstance(item, dict):
            continue
        name = item.get("physical_name") or item.get("name")
        if isinstance(name, str) and name:
            output[name] = item
    return output


def _metadata_source(ontology_status: str, item: dict[str, Any] | None) -> str:
    if isinstance(item, dict) and item.get("metadata_source"):
        return str(item["metadata_source"])
    if ontology_status == "approved":
        return "ontology"
    if ontology_status == "observed":
        return "observed"
    return "inferred"


def profile_columns(
    columns: Mapping[str, Sequence[Any]],
    *,
    fields_view: Sequence[dict[str, Any]] | None = None,
    ontology_status: str = "inferred",
    max_fields: int = MAX_FIELDS,
    max_correlation_fields: int = MAX_CORRELATION_FIELDS,
) -> dict[str, Any]:
    """Profile all rows and all columns in an authorized columnar frame."""
    if not isinstance(columns, Mapping) or not columns:
        raise ValueError("profile input must contain columns")
    names = [str(name) for name in columns]
    if len(names) > max_fields:
        raise ValueError(f"profile input exceeds {max_fields} fields")
    lengths = {len(values) for values in columns.values()}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) < 1:
        raise ValueError("profile columns must have one shared non-zero row count")
    rows = next(iter(lengths))
    metadata = _metadata(fields_view)
    fields: list[dict[str, Any]] = []
    warnings: list[str] = []
    numeric_columns: list[str] = []
    temporal_fields: list[dict[str, Any]] = []
    for name in names:
        values = list(columns[name])
        present = [value for value in values if not _is_missing(value)]
        item = metadata.get(name)
        declared_kind = str((item or {}).get("semantic_kind") or "")
        declared_type = str((item or {}).get("type") or (item or {}).get("data_type") or "")
        declared_role = str((item or {}).get("analysis_role") or "unknown")
        numbers = [_number(value) for value in present]
        temporal_declared = declared_kind in {"temporal", "date", "datetime"} or declared_type.casefold() in {"date", "datetime", "time", "timestamp"}
        is_numeric = bool(present) and len(numbers) == len(present) and not temporal_declared
        dates = [_datetime(value) for value in present]
        is_temporal = temporal_declared or (bool(present) and len(dates) == len(present) and not is_numeric)
        semantic_kind = declared_kind or ("temporal" if is_temporal else "measurement" if is_numeric else "categorical")
        field: dict[str, Any] = {
            "name": name,
            "semantic_kind": semantic_kind,
            "role": declared_role,
            "metadata_source": _metadata_source(ontology_status, item),
            "missing_rate": (len(values) - len(present)) / rows,
            "available": True,
        }
        if item and item.get("unit") is not None:
            field["unit"] = item["unit"]
        if is_numeric:
            summary = _numeric_summary([value for value in numbers if value is not None])
            field["numeric"] = summary
            field["categorical"] = None
            numeric_columns.append(name)
            if summary["std"] == 0 or (summary["cv"] is not None and summary["cv"] < 0.02):
                field["flag"] = "low_variance"
        elif is_temporal:
            parsed_dates = [value for value in dates if value is not None]
            field["temporal"] = {
                "count": len(parsed_dates),
                "min": min(parsed_dates).isoformat() if parsed_dates else None,
                "max": max(parsed_dates).isoformat() if parsed_dates else None,
                "distinct": len(set(parsed_dates)),
            }
            field["numeric"] = None
            field["categorical"] = None
            temporal_fields.append({"name": name, **field["temporal"]})
        else:
            summary = _categorical_summary(present)
            field["categorical"] = summary
            field["numeric"] = None
            if summary["top_share"] > 0.99:
                field["flag"] = "low_variance"
        if field["missing_rate"] > 0.4:
            field["flag"] = "high_missing"
        if field.get("flag") and len(warnings) < 8:
            warnings.append(f"{field['flag']}:{name}")
        for key in ("missing_rate",):
            try:
                field[key] = round(float(field[key]), 4)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{name} missing rate is invalid") from exc
        for section in ("numeric", "categorical"):
            values_section = field.get(section)
            if isinstance(values_section, dict):
                for key, value in list(values_section.items()):
                    if isinstance(value, float):
                        values_section[key] = round(value, 4)
        fields.append(field)

    correlation_names = numeric_columns[:max_correlation_fields]
    correlation_matrix: list[list[float | None]] = []
    for left_name in correlation_names:
        row: list[float | None] = []
        for right_name in correlation_names:
            value = 1.0 if left_name == right_name else _spearman(columns[left_name], columns[right_name])
            row.append(None if value is None else round(value, 4))
        correlation_matrix.append(row)
    return {
        "rows": rows,
        "profiled_rows": rows,
        "columns": len(names),
        "field_names": names,
        "fields": fields,
        "correlation": {
            "columns": correlation_names,
            "matrix": correlation_matrix,
            "selection": "registered/data column order; visualization bound only",
        },
        "temporal_fields": temporal_fields,
        "warnings": warnings,
        "ontology_status": ontology_status,
        "full_data": True,
        "sampling": False,
    }


def profile_dataframe(frame: Any, *, fields_view: Sequence[dict[str, Any]] | None = None, ontology_status: str = "inferred") -> dict[str, Any]:
    """Adapt a pandas-like frame without changing row count or column order."""
    names = [str(name) for name in frame.columns]
    columns = {name: list(frame[name]) for name in names}
    return profile_columns(columns, fields_view=fields_view, ontology_status=ontology_status)


def _required_count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{where} is invalid")
    return value


def build_profile_manifest(
    profile: dict[str, Any],
    *,
    identity: dict[str, Any],
    purpose: str,
    conclusion: str,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Build a report manifest whose values are derived only from profile facts."""
    if profile.get("rows") != profile.get("profiled_rows") or not profile.get("full_data") or profile.get("sampling"):
        raise ValueError("profile manifest requires a complete non-sampled input")
    if not isinstance(identity, dict) or not isinstance(purpose, str) or not purpose.strip() or not isinstance(conclusion, str) or not conclusion.strip():
        raise ValueError("profile manifest identity and narrative are required")
    return {
        "format": PROFILE_FORMAT,
        "schema_version": "ask-o11y.data-profile/v1",
        "purpose": purpose,
        "conclusion": conclusion,
        "identity": dict(identity),
        "data": {
            "rows": _required_count(profile.get("rows"), "profile rows"),
            "profiled_rows": _required_count(profile.get("profiled_rows"), "profiled rows"),
            "columns": _required_count(profile.get("columns"), "profile columns"),
            "full_data": True,
            "sampling": False,
            "derived_dataset": False,
        },
        "process": {
            "kind": "deterministic_data_profile",
            "visual_aggregation_only": True,
            "ontology_status": profile.get("ontology_status", "inferred"),
            "correlation_field_limit": len(profile.get("correlation", {}).get("columns") or []),
        },
        "profile": profile,
        "artifacts": [],
        "limitations": limitations or ["描述性資料概況不代表因果關係或預測能力。"],
    }


def build_profile_plotly_figures(manifest: dict[str, Any], *, frame: Any) -> dict[str, dict[str, Any]]:
    """Build bounded generic Plotly figures for profile PNG counterparts."""
    contract = sys.modules.get("ml_plotly_contract")
    if contract is None:
        contract_path = Path(__file__).with_name("ml_plotly_contract.py")
        if not contract_path.exists():
            contract_path = Path(__file__).resolve().parents[1] / "ml_plotly_contract.py"
        spec = importlib.util.spec_from_file_location("ml_plotly_contract", contract_path)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load ml_plotly_contract")
        contract = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = contract
        spec.loader.exec_module(contract)

    profile = manifest.get("profile") or {}
    fields = profile.get("fields") or []
    figures: dict[str, dict[str, Any]] = {}

    def finish(name: str, data: list[dict[str, Any]], layout: dict[str, Any]) -> None:
        figures[name] = contract.sanitize_figure({"data": data, "layout": layout})

    def missing_rate(item: dict[str, Any]) -> float:
        try:
            value = float(item.get("missing_rate") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("profile missing rate is invalid") from exc
        if not math.isfinite(value):
            raise ValueError("profile missing rate is invalid")
        return round(value * 100, 3)

    if fields:
        finish("profile_field_map", [{"type": "scatter", "mode": "markers", "x": [missing_rate(item) for item in fields], "y": [str(item.get("name") or "") for item in fields]}], {"title": "欄位缺失率與語義角色", "xaxis": {"title": "缺失率 %", "range": [0, 100]}})

    available = [item for item in fields if item.get("available")][:MAX_DISTRIBUTION_FIELDS]
    traces: list[dict[str, Any]] = []
    for item in available:
        name = str(item.get("name") or "")
        try:
            values = list(frame[name])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"profile field is unavailable: {name}") from exc
        counts: dict[str, int] = {}
        for value in values:
            if _is_missing(value):
                continue
            key = _value_key(value)
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]
        traces.append({"type": "bar", "name": name, "x": [pair[0] for pair in top], "y": [pair[1] for pair in top]})
    if traces:
        finish("profile_distributions", traces, {"title": "欄位分布與集中性", "showlegend": True})

    correlation = profile.get("correlation") or {}
    columns = correlation.get("columns") or []
    matrix = correlation.get("matrix") or []
    if len(columns) >= 2 and len(matrix) == len(columns) and all(isinstance(row, list) and len(row) == len(columns) and all(_number(cell) is not None for cell in row) for row in matrix):
        finish("profile_correlation", [{"type": "heatmap", "x": columns, "y": columns, "z": matrix}], {"title": "數值欄位相關性（相關非因果）"})

    temporal = profile.get("temporal_fields") or []
    numeric = [item for item in fields if item.get("available") and isinstance(item.get("numeric"), dict) and item["numeric"].get("count", 0) > 0][:MAX_TREND_FIELDS]
    if temporal and numeric:
        time_name = str(temporal[0].get("name") or "") if isinstance(temporal[0], dict) else str(temporal[0])
        try:
            times = [_datetime(value) for value in frame[time_name]]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"profile temporal field is unavailable: {time_name}") from exc
        order = sorted((index, value) for index, value in enumerate(times) if value is not None)
        traces = []
        for item in numeric:
            name = str(item.get("name") or "")
            values = list(frame[name])
            points = []
            for bucket in range(min(MAX_TREND_POINTS, len(order))):
                start = bucket * len(order) // min(MAX_TREND_POINTS, len(order))
                end = (bucket + 1) * len(order) // min(MAX_TREND_POINTS, len(order))
                complete = [_number(values[index]) for index, _ in order[start:end]]
                complete = [value for value in complete if value is not None]
                if complete:
                    points.append(sum(complete) / len(complete))
            traces.append({"type": "scatter", "mode": "lines", "name": name, "x": list(range(len(points))), "y": points})
        if traces:
            finish("profile_temporal_trend", traces, {"title": "時間欄位與數值欄位趨勢", "xaxis": {"title": time_name}})
    return figures


def build_profile_report_source(manifest: dict[str, Any], *, plotly_names: set[str] | None = None) -> dict[str, Any]:
    facts = {
        "rows": {"kind": "number", "label": "Rows", "value": manifest["data"]["rows"]},
        "columns": {"kind": "number", "label": "Columns", "value": manifest["data"]["columns"]},
    }
    if manifest["data"].get("profiled_rows") is not None:
        facts["profiled_rows"] = {"kind": "number", "label": "Profiled rows", "value": manifest["data"]["profiled_rows"]}
    selected = plotly_names or set()
    artifacts = []
    for item in manifest.get("artifacts") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        artifact_id = Path(item["name"]).stem
        render = {"artifact_id": artifact_id, "fact_refs": ["rows"], "png_output_name": item["name"]}
        if artifact_id in selected:
            render["plotly_output_name"] = f"ml-plotly-{artifact_id}.json"
        artifacts.append(render)
    return {"format": "ask-o11y-report-source-v1", "purpose": manifest["purpose"], "conclusion": manifest["conclusion"], "facts": facts, "artifacts": artifacts}


def validate_profile_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict) or manifest.get("format") != PROFILE_FORMAT:
        raise ValueError("profile manifest format is invalid")
    data = manifest.get("data")
    profile = manifest.get("profile")
    if not isinstance(data, dict) or not isinstance(profile, dict):
        raise ValueError("profile manifest data is incomplete")
    if data.get("rows") != data.get("profiled_rows") or data.get("rows") != profile.get("rows") or not data.get("full_data") or data.get("sampling") or data.get("derived_dataset"):
        raise ValueError("profile manifest violates full-data invariant")
    fields = profile.get("fields")
    if not isinstance(fields, list) or len(fields) != data.get("columns"):
        raise ValueError("profile manifest field coverage is incomplete")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("profile manifest artifacts are invalid")


def _plot_modules():
    import matplotlib  # type: ignore[reportMissingImports]
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore[reportMissingImports]
    return plt


def _save_figure(figure: Any, output: Path, emit_figure: Callable[[Any, str], None] | None) -> None:
    if emit_figure is not None:
        emit_figure(figure, output.name)
    figure.savefig(output, dpi=150, bbox_inches="tight", facecolor="white")
    figure.clear()


def render_profile_assets(
    manifest: dict[str, Any],
    output_dir: Path,
    *,
    frame: Any,
    emit_figure: Callable[[Any, str], None] | None = None,
) -> list[dict[str, str]]:
    """Render bounded views from the complete frame; plots never become data inputs."""
    validate_profile_manifest(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = manifest["profile"]
    fields = profile["fields"]
    plt = _plot_modules()
    colors = {"measurement": "#3274D9", "temporal": "#5794F2", "categorical": "#A352CC", "identifier": "#6B7280"}
    artifacts: list[dict[str, str]] = []

    if fields:
        shown = list(reversed(fields))
        figure, axis = plt.subplots(figsize=(10, max(4, len(shown) * 0.32)))
        missing = [item["missing_rate"] * 100 for item in shown]
        field_colors = ["#E02F44" if item.get("role") in {"forbidden", "leakage_risk", "sensitive"} else colors.get(item.get("semantic_kind"), "#56A64B") for item in shown]
        axis.scatter(missing, [item["name"] for item in shown], c=field_colors, s=80, zorder=3)
        axis.set_xlim(0, max(105, max(missing, default=0) + 10))
        axis.set_xlabel("缺失率（%）；完整資料計算")
        axis.set_title("資料欄位地圖：可用性與缺失", loc="left", fontsize=16, weight="bold")
        axis.grid(axis="x", alpha=0.25, zorder=0)
        _save_figure(figure, output_dir / "profile_field_map.png", emit_figure)
        artifacts.append({"name": "profile_field_map.png", "caption": "完整輸入資料每個欄位的缺失率與語義角色；圖表僅作視覺呈現。", "alt_text": "資料欄位缺失率與語義角色散點圖"})

    available = [item for item in fields if item.get("available")][:MAX_DISTRIBUTION_FIELDS]
    if available:
        columns = 3
        rows = math.ceil(len(available) / columns)
        figure, axes = plt.subplots(rows, columns, figsize=(15, max(4, rows * 3.2)))
        axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]
        for axis, item in zip(axes_list, available, strict=False):
            name = item["name"]
            values = list(frame[name])
            numeric = [_number(value) for value in values]
            color = colors.get(item.get("semantic_kind"), "#56A64B")
            if item.get("numeric") is not None:
                axis.hist([value for value in numeric if value is not None], bins=20, color=color)
            elif item.get("temporal") is not None:
                axis.hist([value.timestamp() for value in (_datetime(value) for value in values) if value is not None], bins=20, color=color)
            else:
                counts: dict[str, int] = {}
                for value in values:
                    if not _is_missing(value):
                        key = _value_key(value)
                        counts[key] = counts.get(key, 0) + 1
                top = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]
                axis.bar([pair[0] for pair in top], [pair[1] for pair in top], color=color)
                axis.tick_params(axis="x", rotation=35)
            axis.set_title(name, fontsize=11, weight="bold")
        for axis in axes_list[len(available):]:
            axis.axis("off")
        figure.suptitle("資料分布：完整資料的形狀與集中性", fontsize=17, weight="bold")
        _save_figure(figure, output_dir / "profile_distributions.png", emit_figure)
        artifacts.append({"name": "profile_distributions.png", "caption": "依資料欄位順序呈現分布；顯示用聚合不會改變完整資料統計。", "alt_text": "資料欄位分布小 multiples"})

    correlation = profile.get("correlation") or {}
    correlation_names = correlation.get("columns") or []
    matrix = correlation.get("matrix") or []
    if len(correlation_names) >= 2 and len(matrix) == len(correlation_names) and all(len(row) == len(correlation_names) for row in matrix):
        import numpy as np  # type: ignore[reportMissingImports]

        figure, axis = plt.subplots(figsize=(8.5, 7))
        try:
            image_values = [[float("nan") if value is None else float(value) for value in row] for row in matrix]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("profile correlation matrix is invalid") from exc
        image = axis.imshow(np.array(image_values), cmap="coolwarm", vmin=-1, vmax=1)
        axis.set_xticks(range(len(correlation_names)), correlation_names, rotation=45, ha="right", fontsize=9)
        axis.set_yticks(range(len(correlation_names)), correlation_names, fontsize=9)
        for row_index, values in enumerate(matrix):
            for column_index, value in enumerate(values):
                if value is not None:
                    axis.text(column_index, row_index, f"{value:.2f}", ha="center", va="center", fontsize=8, color="white" if abs(value) > 0.6 else "black")
        figure.colorbar(image, ax=axis, shrink=0.8)
        axis.set_title("Spearman 相關性（完整資料）", fontsize=15, weight="bold")
        _save_figure(figure, output_dir / "profile_correlation.png", emit_figure)
        artifacts.append({"name": "profile_correlation.png", "caption": "依資料欄位順序計算的 Spearman 相關性；一起變動不代表因果。", "alt_text": "數值欄位 Spearman 相關係數熱圖"})

    temporal = profile.get("temporal_fields") or []
    numeric_names = [item["name"] for item in fields if item.get("numeric") is not None][:MAX_TREND_FIELDS]
    if temporal and numeric_names:
        time_name = temporal[0]["name"]
        times = [_datetime(value) for value in frame[time_name]]
        dated_indices = [(index, value) for index, value in enumerate(times) if value is not None]
        dated_indices.sort(key=lambda pair: pair[1])
        point_count = min(MAX_TREND_POINTS, len(dated_indices))
        if point_count:
            figure, axis = plt.subplots(figsize=(11, 5.5))
            for name in numeric_names:
                values = list(frame[name])
                points: list[float] = []
                for bucket in range(point_count):
                    start = bucket * len(dated_indices) // point_count
                    end = (bucket + 1) * len(dated_indices) // point_count
                    indices = [pair[0] for pair in dated_indices[start:end]]
                    numbers = [_number(values[index]) for index in indices]
                    complete = [value for value in numbers if value is not None]
                    if complete:
                        points.append(sum(complete) / len(complete))
                axis.plot(range(len(points)), points, marker="o", label=name)
            axis.set_title("時間欄位與數值欄位的完整資料趨勢", loc="left", fontsize=15, weight="bold")
            axis.set_xlabel(time_name)
            axis.set_ylabel("各視覺分組的平均值")
            axis.legend(frameon=False)
            axis.grid(alpha=0.25)
            _save_figure(figure, output_dir / "profile_temporal_trend.png", emit_figure)
            artifacts.append({"name": "profile_temporal_trend.png", "caption": "以完整資料按時間排序後作視覺分組平均；此聚合只服務圖表，不建立衍生資料集。", "alt_text": "完整時間序列資料的數值欄位趨勢圖"})

    manifest["artifacts"] = artifacts
    validate_profile_manifest(manifest)
    return artifacts
