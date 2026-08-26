#!/usr/bin/env python3
"""Grouped importance stability: aggregate one-hot names back to original columns before top-k Jaccard."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]
from sklearn.datasets import make_classification  # type: ignore[reportMissingImports]
from sklearn.ensemble import RandomForestClassifier  # type: ignore[reportMissingImports]
from sklearn.model_selection import StratifiedKFold  # type: ignore[reportMissingImports]
from sklearn.pipeline import Pipeline  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_helper_mapping() -> None:
    research = load_module("gis_research", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
    aggregate = research.aggregate_importance_to_original
    columns = ["Age", "Contract", "Payment Method", "Monthly Charge"]

    # One-hot dummies map back and sum; numeric passthrough; unknown kept.
    grouped = aggregate(
        ["num__Age", "cat__Contract_Month-to-Month", "cat__Contract_Two_Year", "cat__Payment Method_Credit", "num__Monthly Charge", "cat__UnknownCol_x"],
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        columns,
    )
    assert math.isclose(grouped["Age"], 0.1), grouped
    assert math.isclose(grouped["Contract"], 0.5), grouped  # 0.2 + 0.3
    assert math.isclose(grouped["Payment Method"], 0.4), grouped
    assert math.isclose(grouped["Monthly Charge"], 0.5), grouped
    assert math.isclose(grouped["UnknownCol_x"], 0.6), grouped  # unmapped kept as-is
    assert set(grouped) == {"Age", "Contract", "Payment Method", "Monthly Charge", "UnknownCol_x"}, grouped

    # Longest-prefix wins: "Contract" must not swallow a real column "Contract Type_x".
    grouped2 = aggregate(["cat__Contract Type_x"], [1.0], ["Contract", "Contract Type_x"])
    assert math.isclose(grouped2["Contract Type_x"], 1.0), grouped2

    # Names already without prefix still map.
    grouped3 = aggregate(["Contract_Two_Year"], [0.7], ["Contract"])
    assert math.isclose(grouped3["Contract"], 0.7), grouped3

    # Hand-crafted dummy jitter: raw top sets disagree on Contract category,
    # while grouping correctly recognizes the same original business feature.
    fold1 = set(aggregate(["cat__Contract_Month-to-Month", "num__Age"], [0.6, 0.4], ["Contract", "Age"]))
    fold2 = set(aggregate(["cat__Contract_Two_Year", "num__Age"], [0.6, 0.4], ["Contract", "Age"]))
    raw1 = {"cat__Contract_Month-to-Month", "num__Age"}
    raw2 = {"cat__Contract_Two_Year", "num__Age"}
    grouped_jaccard = len(fold1 & fold2) / len(fold1 | fold2)
    raw_jaccard = len(raw1 & raw2) / len(raw1 | raw2)
    if grouped_jaccard <= raw_jaccard:
        raise AssertionError(f"grouped Jaccard {grouped_jaccard} <= raw {raw_jaccard}")


def test_stability_uses_grouping() -> None:
    research = load_module("gis_research2", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")

    # Synthetic frame with categorical columns whose dummies split two signals.
    values, target = make_classification(n_samples=300, n_features=12, n_informative=7, weights=[0.7, 0.3], random_state=7)
    frame = pd.DataFrame(values, columns=[f"n{i}" for i in range(values.shape[1])])
    frame["Contract"] = np.where(values[:, 0] > 0, "Month-to-Month", "Two Year")
    frame["Payment Method"] = np.where(values[:, 1] > 0, "Credit Card", "Bank Withdrawal")

    best = Pipeline([
        ("preprocess", research._preprocessor(frame)),
        ("model", RandomForestClassifier(n_estimators=30, max_depth=6, random_state=7, n_jobs=1)),
    ])
    best.fit(frame, target)
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=7)
    original_aggregate = research.aggregate_importance_to_original
    calls: list[int] = []

    def tracked_aggregate(names, importance_values, columns):
        calls.append(len(names))
        return original_aggregate(names, importance_values, columns)

    setattr(research, "aggregate_importance_to_original", tracked_aggregate)
    stability = research._importance_stability(best, frame, target, cv)
    assert isinstance(stability, float) and 0.0 <= stability <= 1.0, stability
    assert len(calls) == 3, calls  # once per CV fold


def main() -> int:
    test_helper_mapping()
    test_stability_uses_grouping()
    print("ok: grouped importance stability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
