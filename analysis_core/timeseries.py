"""Deterministic univariate forecasting with held-out evaluation."""
from __future__ import annotations

from typing import Any

import pandas as pd  # pyright: ignore[reportMissingImports]
from sklearn.metrics import mean_absolute_error, mean_squared_error  # pyright: ignore[reportMissingImports]
from statsmodels.tsa.holtwinters import Holt  # pyright: ignore[reportMissingImports]

from .frame import AnalysisCoreError, scalar_float


def parse_timestamp_series(values: pd.Series) -> pd.Series:
    """Parse ISO timestamps or explicit Unix seconds/milliseconds without pandas' ns default."""
    try:
        non_null = int(values.notna().sum())
        numeric = pd.to_numeric(values, errors="coerce")
        if not isinstance(numeric, pd.Series):
            raise AnalysisCoreError("timestamp input must be a scalar series")
        numeric_count = int(numeric.notna().sum())
        magnitude = float(numeric.dropna().abs().median()) if numeric_count else 0.0
    except (TypeError, ValueError) as exc:
        raise AnalysisCoreError("timestamp values cannot be inspected") from exc
    if non_null and numeric_count == non_null:
        if 100_000_000_000 <= magnitude < 100_000_000_000_000:
            return pd.to_datetime(numeric, unit="ms", errors="coerce", utc=True)  # pyright: ignore[reportCallIssue,reportArgumentType,reportReturnType]
        if 1_000_000_000 <= magnitude < 100_000_000_000:
            return pd.to_datetime(numeric, unit="s", errors="coerce", utc=True)  # pyright: ignore[reportCallIssue,reportArgumentType,reportReturnType]
        raise AnalysisCoreError("numeric timestamps must be Unix seconds or milliseconds")
    return pd.to_datetime(values, errors="coerce", utc=True)  # pyright: ignore[reportReturnType]


def forecast_series(data: pd.DataFrame, *, time_field: str, target: str, horizon: int = 14, damped_trend: bool = True) -> dict[str, Any]:
    if time_field not in data.columns or target not in data.columns:
        raise AnalysisCoreError("time_field and target must exist")
    if not 1 <= horizon <= 365:
        raise AnalysisCoreError("horizon must be between 1 and 365")
    raw_time = data.loc[:, time_field]
    if not isinstance(raw_time, pd.Series):
        raise AnalysisCoreError("time_field must be a scalar column")
    series = pd.DataFrame({"time": parse_timestamp_series(raw_time), "value": pd.to_numeric(data.loc[:, target], errors="coerce")}).dropna().sort_values("time")
    if len(series) < max(20, horizon + 8):
        raise AnalysisCoreError("insufficient valid chronological rows for forecast")
    time_values, value_values = series.loc[:, "time"], series.loc[:, "value"]
    if not isinstance(time_values, pd.Series) or not isinstance(value_values, pd.Series):
        raise AnalysisCoreError("forecast time and target must be scalar columns")
    if bool(time_values.duplicated().to_numpy().any()):
        raise AnalysisCoreError("forecast time field must be unique")
    holdout = min(max(3, horizon), max(3, len(series) // 4))
    train_values, validation_values = value_values.iloc[:-holdout], value_values.iloc[-holdout:]
    validation_model = Holt(train_values, damped_trend=damped_trend, initialization_method="estimated").fit(optimized=True)
    validation_prediction = validation_model.forecast(holdout)
    mse = scalar_float(mean_squared_error(validation_values, validation_prediction), "forecast mean squared error")
    metrics = {"mae": scalar_float(mean_absolute_error(validation_values, validation_prediction), "forecast mean absolute error"), "rmse": mse**0.5, "validation_rows": holdout}
    fitted = Holt(value_values, damped_trend=damped_trend, initialization_method="estimated").fit(optimized=True)
    forecast = fitted.forecast(horizon)
    frequency = pd.infer_freq(pd.DatetimeIndex(time_values))
    last_time = pd.Timestamp(time_values.iloc[-1])
    if isinstance(frequency, str) and frequency:
        future_times = pd.date_range(start=last_time, periods=horizon + 1, freq=frequency)[1:]
    else:
        step = time_values.diff().dropna().median()
        if not isinstance(step, pd.Timedelta) or step <= pd.Timedelta(0):
            raise AnalysisCoreError("cannot infer a positive forecast time step")
        future_times = [last_time + step * offset for offset in range(1, horizon + 1)]
    history = [{"time": pd.Timestamp(timestamp).isoformat(), "value": scalar_float(value, "historical value")} for timestamp, value in zip(time_values, value_values, strict=True)]
    return {"method": "holt_damped_trend" if damped_trend else "holt_trend", "time_field": time_field, "target": target, "horizon": horizon, "metrics": metrics, "history": history, "forecast": [{"time": timestamp.isoformat(), "value": scalar_float(value, "forecast value")} for timestamp, value in zip(future_times, forecast, strict=True)], "validation": {"ok": True, "checks": ["unique chronological timestamps", "positive bounded horizon", "held-out trailing evaluation", "full-series refit"]}, "assumptions": ["Recent level and trend are informative for the requested horizon."], "limitations": ["Holt trend does not model external drivers or abrupt regime changes."]}
