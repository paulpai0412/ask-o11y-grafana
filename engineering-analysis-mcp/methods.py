"""Deterministic high-level Engineering methods; no transport or artifact access."""
from __future__ import annotations

from typing import Any, cast

import pandas as pd  # pyright: ignore[reportMissingImports]

from analysis_core import AnalysisCoreError, cluster_rows, detect_anomalies, deterministic_method_source, forecast_series, parse_timestamp_series, profile_dataframe, supervised_model, visualization_spec  # pyright: ignore[reportMissingImports]


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisCoreError(f"{label} must be an integer") from exc


def _number(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisCoreError(f"{label} must be numeric") from exc


def _fields(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(field, str) or not field for field in value):
        raise AnalysisCoreError(f"{label} must contain explicit field names")
    return cast(list[str], value)


def _trend_groups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 10:
        raise AnalysisCoreError("trend_groups must contain 1 to 10 explicit groups")
    groups: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"fields", "title"}:
            raise AnalysisCoreError("each trend group accepts only fields and an optional title")
        fields = _fields(item.get("fields"), "trend group fields")
        if len(fields) < 2 or len(fields) > 10 or len(set(fields)) != len(fields):
            raise AnalysisCoreError("each trend group must contain 2 to 10 unique fields")
        title = item.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise AnalysisCoreError("trend group title must be a non-empty string")
        groups.append({"fields": fields, "title": title})
    return groups


def _base_analysis(analysis_type: str, title: str, summary: str, subject: dict[str, Any], frames: list[dict[str, Any]], panels: list[dict[str, Any]], method_result: dict[str, Any]) -> dict[str, Any]:
    return {"analysis_type": analysis_type, "title": title, "summary": summary, "severity": "info", "time_range": {"from": None, "to": None}, "subject": {"domain": "engineering", **subject}, "findings": [{"level": "info", "message": summary}], "data_frames": frames, "recommended_panels": panels, "details": {"method_source": method_result["method_source"], "selected_method": method_result["method"], "validation": method_result["validation"], "domain_validation": subject}}


def apply_validity_rules(data: pd.DataFrame, args: dict[str, Any], rules: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
    requested = {value for key in ["target", "time_field"] if isinstance((value := args.get(key)), str)}
    for key in ["fields", "features", "anomaly_features"]:
        values = args.get(key)
        if isinstance(values, list):
            requested.update(str(value) for value in values)
    trend_groups = args.get("trend_groups")
    if isinstance(trend_groups, list):
        for group in trend_groups:
            if isinstance(group, dict) and isinstance(group.get("fields"), list):
                requested.update(str(value) for value in group["fields"])
    relevant = [rule for rule in rules if isinstance(rule, dict) and isinstance(rule.get("applies_to"), list) and requested.intersection(str(value) for value in rule["applies_to"])] if isinstance(rules, list) else []
    mask = pd.Series(True, index=data.index)
    applied = []
    for rule in relevant:
        field = rule.get("field")
        accepted = rule.get("accepted_values")
        if not isinstance(field, str) or field not in data.columns or not isinstance(accepted, list) or not accepted:
            raise AnalysisCoreError("required engineering validity rule is incomplete")
        normalized = {str(value).strip().lower() for value in accepted}
        field_values = data.loc[:, field]
        if not isinstance(field_values, pd.Series):
            raise AnalysisCoreError("validity field must be a scalar column")
        mask &= field_values.map(lambda value: str(value).strip().lower() in normalized)
        applied.append({"field": field, "applies_to": rule["applies_to"], "accepted_values": accepted})
    filtered = data.loc[mask].copy()
    if relevant and filtered.empty:
        raise AnalysisCoreError("engineering validity rules exclude every row")
    audit = {"input_rows": len(data), "valid_rows": len(filtered), "excluded_rows": len(data) - len(filtered), "rules": applied}
    return filtered, audit


def profile(data: pd.DataFrame, args: dict[str, Any]) -> dict[str, Any]:
    fields = _fields(args.get("fields"), "fields")
    result = profile_dataframe(data, fields)
    columns = ["field", "kind", "rows", "non_null", "missing", "unique", "mean", "std", "min", "median", "max"]
    summaries = result["fields"]
    frame = {"name": "profile", "schema": {"fields": [{"name": column, "type": "string" if column in {"field", "kind"} else "number"} for column in columns]}, "data": {"values": [[row.get(column) for row in summaries] for column in columns]}}
    panel = visualization_spec("table", title="Engineering Data Profile", data_frame="profile", fields={})
    source = deterministic_method_source(implementation="engineering-analysis-mcp.v1", method="profile", algorithm="pandas.descriptive_profile", packages=["pandas"])
    method_result = {"method": "profile", "parameters": {"fields": fields}, "profile": result, "validation": result["validation"], "assumptions": ["Rows represent the selected operating population after declared validity rules."], "limitations": ["Descriptive statistics do not establish causal or predictive relationships."], "method_source": source}
    summary = f"Profiled {len(fields)} selected Engineering fields across {len(data)} valid rows with missingness, cardinality, type, and numeric summaries."
    analysis = _base_analysis("engineering_profile", str(args.get("title") or "Engineering Data Profile"), summary, {"fields": fields}, [frame], [panel], method_result)
    analysis["details"]["profile"] = result
    return {"artifact_name": "profile", "method_result": method_result, "analysis": analysis, "preview": {"summary": summary, "profile": result, "visualizations": [panel]}}


def predictive(data: pd.DataFrame, args: dict[str, Any]) -> dict[str, Any]:
    target, features = args.get("target"), _fields(args.get("features"), "features")
    task, family = str(args.get("task") or ""), str(args.get("model_family") or "")
    if not isinstance(target, str) or not target:
        raise AnalysisCoreError("target is required")
    if task not in {"regression", "classification"} or family not in {"linear", "random_forest"}:
        raise AnalysisCoreError("task/model_family is unsupported")
    seed, test_fraction = _integer(args.get("seed", 42), "seed"), _number(args.get("test_fraction", 0.2), "test_fraction")
    time_field = args.get("time_field")
    result = supervised_model(data, target=target, features=features, task=cast(Any, task), model_family=cast(Any, family), test_fraction=test_fraction, seed=seed, time_field=str(time_field) if isinstance(time_field, str) else None)
    source = deterministic_method_source(implementation="engineering-analysis-mcp.v1", method=f"supervised_{task}", algorithm=f"scikit-learn.{family}", packages=["pandas", "scikit-learn"], seed=seed)
    method_result = {"method": f"supervised_{task}", **result, "profile": profile_dataframe(data, [target, *features]), "method_source": source}
    importance, predictions = result["feature_importance"], result["predictions"]
    frames = [
        {"name": "feature_importance", "schema": {"fields": [{"name": "feature", "type": "string"}, {"name": "importance", "type": "number"}]}, "data": {"values": [[row["feature"] for row in importance], [row["importance"] for row in importance]]}},
        {"name": "predictions", "schema": {"fields": [{"name": "index", "type": "string"}, {"name": "actual", "type": "number" if task == "regression" else "string"}, {"name": "predicted", "type": "number" if task == "regression" else "string"}]}, "data": {"values": [[row["index"] for row in predictions], [row["actual"] for row in predictions], [row["predicted"] for row in predictions]]}},
    ]
    panels = [visualization_spec("bar", title="Feature Importance", data_frame="feature_importance", fields={"x": "feature", "y": ["importance"]})]
    panels.append(visualization_spec("scatter", title="Actual vs Predicted", data_frame="predictions", fields={"x": "actual", "y": ["predicted"]}) if task == "regression" else visualization_spec("table", title="Classification Predictions", data_frame="predictions", fields={}))
    metric_text = ", ".join(f"{key}={value:.3f}" for key, value in result["metrics"].items() if isinstance(value, (int, float)))
    summary = f"Evaluated {family} {task} on a held-out {result['split']['strategy']} split ({metric_text}). Metrics and feature importance are predictive, not causal."
    analysis = _base_analysis(f"engineering_{task}", str(args.get("title") or f"Engineering {task.title()} Analysis"), summary, {"target": target, "features": features}, frames, panels, method_result)
    analysis["details"].update({"metrics": result["metrics"], "split": result["split"], "feature_importance": importance})
    return {"artifact_name": "predictive", "method_result": method_result, "analysis": analysis, "preview": {"summary": summary, "metrics": result["metrics"], "feature_importance": importance[:5], "visualizations": panels}}


def patterns(data: pd.DataFrame, args: dict[str, Any]) -> dict[str, Any]:
    features, operations = _fields(args.get("features"), "features"), args.get("operations")
    if not isinstance(operations, list) or not operations or not set(operations).issubset({"clustering", "anomaly"}):
        raise AnalysisCoreError("operations must select clustering and/or anomaly")
    seed, clusters = _integer(args.get("seed", 42), "seed"), _integer(args.get("clusters", 3), "clusters")
    contamination = _number(args.get("contamination", 0.05), "contamination")
    selected: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    panels: list[dict[str, Any]] = []
    if "clustering" in operations:
        selected["clustering"] = cluster_rows(data, features=features, clusters=clusters, seed=seed)
        rows = selected["clustering"]["assignments"]
        frames.append({"name": "cluster_assignments", "schema": {"fields": [{"name": "index", "type": "string"}, {"name": "cluster", "type": "number"}]}, "data": {"values": [[row["index"] for row in rows], [row["cluster"] for row in rows]]}})
        panels.append(visualization_spec("table", title="Cluster Assignments", data_frame="cluster_assignments", fields={}))
    if "anomaly" in operations:
        selected["anomaly"] = detect_anomalies(data, features=features, contamination=contamination, seed=seed)
        rows = selected["anomaly"]["scores"]
        frames.append({"name": "anomaly_scores", "schema": {"fields": [{"name": "index", "type": "string"}, {"name": "score", "type": "number"}, {"name": "anomaly", "type": "boolean"}]}, "data": {"values": [[row["index"] for row in rows], [row["score"] for row in rows], [row["anomaly"] for row in rows]]}})
        panels.append(visualization_spec("table", title="Anomaly Scores", data_frame="anomaly_scores", fields={}))
    source = deterministic_method_source(implementation="engineering-analysis-mcp.v1", method="pattern_analysis", algorithm="+".join(operations), packages=["pandas", "scikit-learn"], seed=seed)
    method_result = {"method": "pattern_analysis", "parameters": {"features": features, "operations": operations, "clusters": clusters, "contamination": contamination, "seed": seed}, "results": selected, "validation": {"ok": True, "checks": ["explicit features", "allowlisted selected operations", "deterministic seed"]}, "assumptions": [], "limitations": ["Clusters and anomaly scores are descriptive signals requiring engineering review."], "method_source": source}
    summary = "Completed only the requested pattern operations: " + ", ".join(operations) + ". Results identify structure or unusual rows, not confirmed faults."
    analysis = _base_analysis("engineering_patterns", str(args.get("title") or "Engineering Pattern Analysis"), summary, {"features": features}, frames, panels, method_result)
    analysis["details"].update({"operations": operations, "results": selected})
    return {"artifact_name": "patterns", "method_result": method_result, "analysis": analysis, "preview": {"summary": summary, "operations": operations, "visualizations": panels}}


def timeseries(data: pd.DataFrame, args: dict[str, Any]) -> dict[str, Any]:
    target, time_field, operations = args.get("target"), args.get("time_field"), args.get("operations")
    if not isinstance(time_field, str) or not time_field:
        raise AnalysisCoreError("time_field is required")
    if not isinstance(operations, list) or not operations or not set(operations).issubset({"trend", "forecast", "anomaly"}):
        raise AnalysisCoreError("operations must select trend, forecast, and/or anomaly")
    if "forecast" in operations and (not isinstance(target, str) or not target):
        raise AnalysisCoreError("target is required for forecast")
    anomaly_features = _fields(args.get("anomaly_features"), "anomaly_features") if "anomaly" in operations else []
    trend_groups = _trend_groups(args.get("trend_groups")) if "trend" in operations else []
    horizon, seed = _integer(args.get("horizon", 14), "horizon"), _integer(args.get("seed", 42), "seed")
    contamination = _number(args.get("contamination", 0.05), "contamination")
    selected: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    panels: list[dict[str, Any]] = []
    packages = ["pandas"]
    if "trend" in operations:
        if time_field not in data.columns:
            raise AnalysisCoreError(f"unknown time field: {time_field}")
        time_values = data.loc[:, time_field]
        if not isinstance(time_values, pd.Series):
            raise AnalysisCoreError("time_field must be a scalar column")
        parsed_times = parse_timestamp_series(time_values)
        trend_results = []
        for index, group in enumerate(trend_groups, 1):
            fields = cast(list[str], group["fields"])
            missing = [field for field in fields if field not in data.columns]
            if missing:
                raise AnalysisCoreError(f"unknown trend fields: {missing}")
            trend_data = pd.DataFrame({"time": parsed_times})
            for field in fields:
                trend_data[field] = pd.to_numeric(data.loc[:, field], errors="coerce")
            trend_data = trend_data.dropna().sort_values("time")
            if len(trend_data) < 2:
                raise AnalysisCoreError(f"trend group {index} requires at least two complete rows")
            frame_name = f"trend_{index}"
            frames.append({"name": frame_name, "schema": {"fields": [{"name": "time", "type": "timestamp"}, *[{"name": field, "type": "number"} for field in fields]]}, "data": {"values": [[pd.Timestamp(value).isoformat() for value in trend_data["time"].tolist()], *[trend_data[field].astype(float).tolist() for field in fields]]}})
            title = str(group.get("title") or " vs ".join(fields) + " Trend")
            panels.append(visualization_spec("timeseries", title=title, data_frame=frame_name, fields={"x": "time", "y": fields}))
            trend_results.append({"fields": fields, "row_count": len(trend_data), "time_from": pd.Timestamp(trend_data["time"].iloc[0]).isoformat(), "time_to": pd.Timestamp(trend_data["time"].iloc[-1]).isoformat()})
        selected["trend"] = trend_results
    if "forecast" in operations:
        selected["forecast"] = forecast_series(data, time_field=time_field, target=cast(str, target), horizon=horizon)
        history_rows, forecast_rows = selected["forecast"]["history"], selected["forecast"]["forecast"]
        frames.append({"name": "forecast", "schema": {"fields": [{"name": "time", "type": "timestamp"}, {"name": "actual", "type": "number"}, {"name": "forecast", "type": "number"}]}, "data": {"values": [[row["time"] for row in [*history_rows, *forecast_rows]], [*[row["value"] for row in history_rows], *([None] * len(forecast_rows))], [*([None] * len(history_rows)), *[row["value"] for row in forecast_rows]]]}})
        panels.append(visualization_spec("timeseries", title=f"{target} Actual and Forecast", data_frame="forecast", fields={"x": "time", "y": ["actual", "forecast"]}))
        packages.extend(["statsmodels", "scikit-learn"])
    if "anomaly" in operations:
        selected["anomaly"] = detect_anomalies(data, features=anomaly_features, contamination=contamination, seed=seed)
        rows = selected["anomaly"]["scores"]
        time_values = data.loc[:, time_field]
        if not isinstance(time_values, pd.Series):
            raise AnalysisCoreError("time_field must be a scalar column")
        parsed_times = parse_timestamp_series(time_values)
        time_lookup = {str(index): pd.Timestamp(value).isoformat() for index, value in parsed_times.items()}
        frames.append({"name": "timeseries_anomalies", "schema": {"fields": [{"name": "time", "type": "timestamp"}, {"name": "score", "type": "number"}, {"name": "anomaly", "type": "boolean"}]}, "data": {"values": [[time_lookup[row["index"]] for row in rows], [row["score"] for row in rows], [row["anomaly"] for row in rows]]}})
        panels.append(visualization_spec("timeseries", title="Anomaly Scores", data_frame="timeseries_anomalies", fields={"x": "time", "y": ["score"]}))
        packages.append("scikit-learn")
    source = deterministic_method_source(implementation="engineering-analysis-mcp.v1", method="timeseries_analysis", algorithm="+".join(operations), packages=sorted(set(packages)), seed=seed)
    method_result = {"method": "timeseries_analysis", "parameters": {"target": target, "time_field": time_field, "operations": operations, "trend_groups": trend_groups, "anomaly_features": anomaly_features, "horizon": horizon, "contamination": contamination, "seed": seed}, "results": selected, "validation": {"ok": True, "checks": ["explicit time field", "allowlisted selected operations", "trend fields and chronological order", "chronological forecast evaluation", "deterministic anomaly seed"]}, "assumptions": [], "limitations": ["Trend charts are descriptive and do not establish causality; forecasts extrapolate recent level/trend; anomaly scores require engineering review."], "method_source": source}
    summary = "Completed only the requested time-series operations: " + ", ".join(operations) + ". Trend charts are descriptive; forecast metrics use a trailing holdout; anomaly scores do not confirm faults."
    trend_fields = [field for group in trend_groups for field in group["fields"]]
    analysis = _base_analysis("engineering_timeseries", str(args.get("title") or "Engineering Time-Series Analysis"), summary, {"target": target, "time_field": time_field, "features": [*trend_fields, *anomaly_features]}, frames, panels, method_result)
    analysis["details"].update({"operations": operations, "results": selected})
    return {"artifact_name": "timeseries", "method_result": method_result, "analysis": analysis, "preview": {"summary": summary, "operations": operations, "metrics": selected.get("forecast", {}).get("metrics"), "visualizations": panels}}


METHODS = {"analyze_profile": profile, "analyze_predictive": predictive, "analyze_patterns": patterns, "analyze_timeseries": timeseries}


def run(name: str, data: pd.DataFrame, args: dict[str, Any]) -> dict[str, Any]:
    method = METHODS.get(name)
    if method is None:
        raise AnalysisCoreError(f"unsupported engineering method: {name}")
    return method(data, args)
