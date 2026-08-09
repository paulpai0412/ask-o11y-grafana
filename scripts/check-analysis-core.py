#!/usr/bin/env python3
"""Focused deterministic contract check for analysis_core."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis_core import AnalysisCoreError, cluster_rows, dataframe_from_columnar_frame, detect_anomalies, deterministic_method_source, forecast_series, pairwise_correlation, profile_dataframe, supervised_model, visualization_spec  # noqa: E402  # pyright: ignore[reportMissingImports]

OUT = ROOT / ".scratch" / "poc" / "analysis-core-check.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def must_fail(name: str, action) -> str:
    try:
        action()
    except AnalysisCoreError:
        return name
    raise RuntimeError(f"negative check did not fail: {name}")


def main() -> int:
    count = 96
    dates = [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(count)]
    feature_a = list(range(count))
    feature_b = [math.sin(index / 5) * 10 + index * 0.3 for index in range(count)]
    feature_c = [(index * 7) % 19 for index in range(count)]
    target = [2.5 * feature_a[index] - 1.2 * feature_b[index] + feature_c[index] for index in range(count)]
    classification = ["high" if value >= sorted(target)[count // 2] else "low" for value in target]
    frame = {"schema": {"fields": [{"name": name} for name in ["date", "feature_a", "feature_b", "feature_c", "target", "class"]]}, "data": {"values": [dates, feature_a, feature_b, feature_c, target, classification]}}
    data = dataframe_from_columnar_frame(frame)

    profile = profile_dataframe(data)
    correlation_a = pairwise_correlation(data, ["feature_a", "feature_b", "feature_c", "target"], minimum_rows=40)
    correlation_b = pairwise_correlation(data, ["feature_a", "feature_b", "feature_c", "target"], minimum_rows=40)
    regression_a = supervised_model(data, target="target", features=["feature_a", "feature_b", "feature_c"], task="regression", model_family="random_forest", seed=42, time_field="date")
    regression_b = supervised_model(data, target="target", features=["feature_a", "feature_b", "feature_c"], task="regression", model_family="random_forest", seed=42, time_field="date")
    classification_result = supervised_model(data, target="class", features=["feature_a", "feature_b", "feature_c"], task="classification", model_family="linear", seed=42)
    clustering_a = cluster_rows(data, features=["feature_a", "feature_b", "feature_c"], clusters=3, seed=42)
    clustering_b = cluster_rows(data, features=["feature_a", "feature_b", "feature_c"], clusters=3, seed=42)
    anomalies_a = detect_anomalies(data, features=["feature_a", "feature_b", "feature_c"], contamination=0.05, seed=42)
    anomalies_b = detect_anomalies(data, features=["feature_a", "feature_b", "feature_c"], contamination=0.05, seed=42)
    forecast = forecast_series(data, time_field="date", target="target", horizon=7)
    visualizations = [
        visualization_spec("table", title="Table", data_frame="table", fields={}),
        visualization_spec("timeseries", title="Trend", data_frame="trend", fields={"x": "date", "y": ["target"]}),
        visualization_spec("bar", title="Importance", data_frame="importance", fields={"x": "feature", "y": ["importance"]}),
        visualization_spec("scatter", title="Actual vs Predicted", data_frame="prediction", fields={"x": "actual", "y": ["predicted"]}),
        visualization_spec("heatmap", title="Correlation", data_frame="correlation", fields={"source": "source", "target": "target", "value": "correlation"}),
    ]
    provenance = deterministic_method_source(implementation="analysis-core-check", method="regression", algorithm="random_forest", packages=["pandas", "scikit-learn"], seed=42)

    require(correlation_a == correlation_b, "correlation is not deterministic")
    require(regression_a["metrics"] == regression_b["metrics"] and regression_a["feature_importance"] == regression_b["feature_importance"], "supervised result is not deterministic")
    require(regression_a["split"]["strategy"] == "chronological" and regression_a["split"]["validation_rows"] > 0, "regression leakage-safe validation missing")
    require(classification_result["metrics"]["accuracy"] >= 0 and classification_result["feature_importance"], "classification evaluation/importance missing")
    require(clustering_a["assignments"] == clustering_b["assignments"], "clustering is not deterministic")
    require(anomalies_a["scores"] == anomalies_b["scores"], "anomaly detection is not deterministic")
    require(len(forecast["history"]) == count and len(forecast["forecast"]) == 7 and forecast["metrics"]["validation_rows"] > 0, "forecast history/evaluation missing")
    require([item["type"] for item in visualizations] == ["table", "timeseries", "bar", "scatter", "heatmap"], "visualization catalog incomplete")
    runtime_flags = [provenance["runtime_agent"], provenance["runtime_llm"], provenance["runtime_skill"]]
    require(all(isinstance(flag, bool) and not flag for flag in runtime_flags), "forbidden runtime provenance")

    negatives = [
        must_fail("unknown_field", lambda: pairwise_correlation(data, ["feature_a", "missing"])),
        must_fail("target_leakage", lambda: supervised_model(data, target="target", features=["target", "feature_a"], task="regression", model_family="linear")),
        must_fail("invalid_contamination", lambda: detect_anomalies(data, features=["feature_a"], contamination=0.9)),
        must_fail("invalid_visualization", lambda: visualization_spec("heatmap", title="Bad", data_frame="bad", fields={})),
        must_fail("insufficient_rows", lambda: supervised_model(data.iloc[:5], target="target", features=["feature_a"], task="regression", model_family="linear")),
    ]

    source_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "analysis_core").glob("*.py")))
    forbidden_dependencies = [marker for marker in ["import artifact_store", "from artifact_store", "import workflow_node", "from workflow_node", "import mcp", "from mcp", "import openai", "import anthropic", "import subprocess", "grafana_url", "datasource_uid"] if marker in source_text.lower()]
    require(not forbidden_dependencies, f"analysis_core contains forbidden dependency/rule: {forbidden_dependencies}")

    out = {"ok": True, "methods": {"profile": profile["validation"], "correlation_pairs": len(correlation_a["pairs"]), "regression": regression_a["metrics"], "classification": classification_result["metrics"], "feature_importance": regression_a["feature_importance"], "clusters": clustering_a["clusters"], "silhouette": clustering_a["silhouette"], "anomaly_count": anomalies_a["anomaly_count"], "forecast": forecast["metrics"], "explainability": regression_a["limitations"]}, "visualizations": [item["type"] for item in visualizations], "provenance": provenance, "negative_checks": negatives, "forbidden_dependencies": []}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
