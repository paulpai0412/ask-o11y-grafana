"""Allowlisted deterministic clustering and anomaly detection."""
from __future__ import annotations

from typing import Any

import pandas as pd  # pyright: ignore[reportMissingImports]
from sklearn.cluster import KMeans  # pyright: ignore[reportMissingImports]
from sklearn.ensemble import IsolationForest  # pyright: ignore[reportMissingImports]
from sklearn.impute import SimpleImputer  # pyright: ignore[reportMissingImports]
from sklearn.metrics import silhouette_score  # pyright: ignore[reportMissingImports]
from sklearn.pipeline import Pipeline  # pyright: ignore[reportMissingImports]
from sklearn.preprocessing import StandardScaler  # pyright: ignore[reportMissingImports]

from .frame import AnalysisCoreError, scalar_float, scalar_int


def _matrix(data: pd.DataFrame, features: list[str]) -> tuple[Any, list[Any]]:
    if len(features) < 1 or len(set(features)) != len(features):
        raise AnalysisCoreError("features must contain unique field names")
    unknown = [field for field in features if field not in data.columns]
    if unknown:
        raise AnalysisCoreError("unknown features: " + ", ".join(unknown))
    numeric = pd.DataFrame({field: pd.to_numeric(data.loc[:, field], errors="coerce") for field in features})
    if len(numeric) < 20:
        raise AnalysisCoreError("unsupervised analysis requires at least 20 rows")
    pipeline = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    return pipeline.fit_transform(numeric), list(numeric.index)


def cluster_rows(data: pd.DataFrame, *, features: list[str], clusters: int = 3, seed: int = 42) -> dict[str, Any]:
    matrix, indices = _matrix(data, features)
    if not 2 <= clusters <= min(10, len(indices) - 1):
        raise AnalysisCoreError("clusters must be between 2 and min(10, rows-1)")
    model = KMeans(n_clusters=clusters, random_state=seed, n_init="auto")
    labels = model.fit_predict(matrix)
    score = scalar_float(silhouette_score(matrix, labels), "silhouette score")
    return {"method": "kmeans", "features": features, "clusters": clusters, "seed": seed, "silhouette": score, "assignments": [{"index": str(index), "cluster": scalar_int(label, "cluster label")} for index, label in zip(indices, labels, strict=True)], "validation": {"ok": True, "checks": ["numeric features", "median imputation", "standard scaling", "bounded cluster count", "silhouette evaluation"]}, "limitations": ["K-means assumes approximately spherical clusters in scaled feature space."]}


def detect_anomalies(data: pd.DataFrame, *, features: list[str], contamination: float = 0.05, seed: int = 42) -> dict[str, Any]:
    matrix, indices = _matrix(data, features)
    if not 0.001 <= contamination <= 0.25:
        raise AnalysisCoreError("contamination must be between 0.001 and 0.25")
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=seed, n_jobs=1)
    labels = model.fit_predict(matrix)
    scores = -model.score_samples(matrix)
    rows = sorted(({"index": str(index), "anomaly": bool(label == -1), "score": scalar_float(score, "anomaly score")} for index, label, score in zip(indices, labels, scores, strict=True)), key=lambda item: item["score"], reverse=True)
    return {"method": "isolation_forest", "features": features, "contamination": contamination, "seed": seed, "anomaly_count": sum(1 for row in rows if row["anomaly"]), "scores": rows, "validation": {"ok": True, "checks": ["numeric features", "median imputation", "standard scaling", "bounded contamination", "deterministic seed"]}, "limitations": ["Anomaly scores identify unusual multivariate rows, not confirmed faults or fraud."]}
