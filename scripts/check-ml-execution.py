#!/usr/bin/env python3
"""Shared split/fit receipts: tie safety, group isolation, full rows, real fit-call counts."""
from pathlib import Path
import sys
import importlib
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.dummy import DummyRegressor
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sandbox-analysis-mcp"))
execution = importlib.import_module("ml_execution")
outer_split, cv_splits, split_receipt = execution.outer_split, execution.cv_splits, execution.split_receipt
run_multi_model_comparison = importlib.import_module("ml_autoresearch").run_multi_model_comparison
run_multi_model_regression = importlib.import_module("ml_regression").run_multi_model_regression


def main():
    times = pd.date_range("2026-01-01", periods=40).repeat(3)
    target = np.tile([0, 1], 60)
    a, b, receipt = outer_split("chronological_holdout", target, 0.21, 7, times=times)
    assert len(a) + len(b) == len(target) and times.asi8[a].max() < times.asi8[b].min()
    assert receipt == outer_split("chronological_holdout", target, 0.21, 7, times=times)[2]
    _, changed = cv_splits("chronological_holdout", target[a], 3, 7, times=times[a])
    assert all(row["train_time_range"][1] < row["validation_time_range"][0] for row in changed)
    # Same extrema/counts but different membership must have different digests.
    assert split_receipt([0, 1, 4], [5, 6], 7)["indices_sha256"] != split_receipt([0, 2, 4], [5, 6], 7)["indices_sha256"]
    groups = np.repeat(np.arange(12), 10)
    a, b, receipt = outer_split("grouped_holdout", target, 0.2, 7, groups=groups)
    assert not (set(groups[a]) & set(groups[b])) and receipt["group_overlap_count"] == 0
    _, folds = cv_splits("grouped_holdout", target[a], 3, 7, groups=groups[a])
    assert all(row["group_overlap_count"] == 0 for row in folds)
    for kind, options in [("stratified_holdout", {"times": times}), ("unknown", {})]:
        try:
            outer_split(kind, target, 0.2, 7, **options)
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported split accepted")

    calls = []
    def counted(original):
        def fit(self, *args, **kwargs):
            calls.append(type(self).__name__)
            return original(self, *args, **kwargs)
        return fit
    x = pd.DataFrame(np.random.default_rng(7).normal(size=(120, 3)), columns=["a", "b", "c"])
    y = (x["a"] > 0).astype(int)
    a, b, _ = outer_split("stratified_holdout", y, 0.25, 7)
    with patch.object(LogisticRegression, "fit", counted(LogisticRegression.fit)), patch.object(IsotonicRegression, "fit", counted(IsotonicRegression.fit)):
        result = run_multi_model_comparison(x.iloc[a], y.iloc[a], x.iloc[b], y.iloc[b], kinds=["logistic_regression"], n_iter=2, cv_folds=3)
    counts = result["fit_counts"]
    assert counts["total_fits"] == len(calls) == 14, f"{counts}: {calls}"
    assert counts["search_cv_fits"] == 6 and counts["calibration_oof_fits"] == counts["stability_fits"] == 3
    assert counts["baseline_cv_fits"] == counts["final_holdout_fits"] == 0
    calls.clear()
    with patch.object(Ridge, "fit", counted(Ridge.fit)), patch.object(DummyRegressor, "fit", counted(DummyRegressor.fit)):
        result = run_multi_model_regression(x.iloc[:90], x["a"].iloc[:90], x.iloc[90:], x["a"].iloc[90:], kinds=["ridge"], n_iter=2, cv_folds=3, bootstrap_samples=100, times=times[:90])
    counts = result["fit_counts"]
    assert counts["total_fits"] == len(calls) == 8, f"{counts}: {calls}"
    assert counts["baseline_cv_fits"] == 3 and counts["baseline_refit_fits"] == 1 and counts["final_holdout_fits"] == 0
    print("ok: shared splits, timestamp ties, exact index digests, groups, and measured estimator/calibrator fit counts")


if __name__ == "__main__":
    main()
