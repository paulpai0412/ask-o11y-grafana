"""Shared deterministic split indices and successful-fit accounting (no model selection)."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, cast

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold, StratifiedKFold, TimeSeriesSplit, train_test_split


GENERALIZATION_LIMITS = {"generalization_gap": 0.05, "importance_stability": 0.6, "max_psi": 0.25}


def split_receipt(train: Any, validation: Any, rows: int, *, times: Any = None, groups: Any = None) -> dict[str, Any]:
    train, validation = np.asarray(train, dtype=int), np.asarray(validation, dtype=int)
    if not len(train) or not len(validation) or len(np.unique(train)) != len(train) or len(np.unique(validation)) != len(validation):
        raise ValueError("split must contain nonempty unique indices")
    if min(train.min(), validation.min()) < 0 or max(train.max(), validation.max()) >= rows or np.intersect1d(train, validation).size:
        raise ValueError("split indices overlap or escape their population")
    identity = [train.tolist(), validation.tolist()]
    result = {"indices_sha256": hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest(),
              "population_rows": rows, "train_rows": len(train), "validation_rows": len(validation),
              "train_start": train.min().item(), "train_end": train.max().item(),
              "validation_start": validation.min().item(), "validation_end": validation.max().item()}
    if times is not None:
        timestamps = pd.DatetimeIndex(pd.to_datetime(times, utc=True, errors="raise"))
        if len(timestamps) != rows or timestamps.isna().any():
            raise ValueError("split timestamps must cover every row")
        left, right = timestamps[train], timestamps[validation]
        if cast(pd.Timestamp, left.max()) >= cast(pd.Timestamp, right.min()):
            raise ValueError("chronological split permits future or tied timestamps across partitions")
        result.update({"train_time_range": [cast(pd.Timestamp, left.min()).isoformat(), cast(pd.Timestamp, left.max()).isoformat()],
                       "validation_time_range": [cast(pd.Timestamp, right.min()).isoformat(), cast(pd.Timestamp, right.max()).isoformat()]})
    if groups is not None:
        values = pd.Series(groups).reset_index(drop=True)
        if len(values) != rows or values.isna().any():
            raise ValueError("split groups must cover every row")
        overlap = set(values.iloc[train]) & set(values.iloc[validation])
        if overlap:
            raise ValueError("groups overlap across partitions")
        result["group_overlap_count"] = len(overlap)
    return result


def outer_split(kind: str, target: Any, fraction: float, seed: int, *, times: Any = None, groups: Any = None):
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0.05 <= fraction <= 0.5:
        raise ValueError("holdout fraction must be between 0.05 and 0.5")
    rows = len(target)
    indices = np.arange(rows)
    if kind == "stratified_holdout":
        if times is not None or groups is not None:
            raise ValueError("classification cannot silently ignore time/group constraints")
        train, test = train_test_split(indices, test_size=fraction, random_state=seed, stratify=target)
    elif kind == "chronological_holdout" and times is not None:
        timestamps = pd.DatetimeIndex(pd.to_datetime(times, utc=True, errors="raise"))
        if timestamps.isna().any() or len(timestamps) != rows:
            raise ValueError("missing or invalid split timestamps")
        order = np.argsort(timestamps.asi8, kind="stable")
        cutoff = math.floor(rows * (1 - fraction))
        if not 0 < cutoff < rows:
            raise ValueError("insufficient split rows")
        # Keep tied timestamps together, never train on the validation timestamp.
        cutoff = np.searchsorted(timestamps.asi8[order], timestamps.asi8[order[cutoff]], side="left")
        train, test = order[:cutoff], order[cutoff:]
    elif kind == "grouped_holdout" and groups is not None:
        values = pd.Series(groups).reset_index(drop=True)
        unique = values.drop_duplicates().tolist()
        selected = unique[-max(1, math.ceil(len(unique) * fraction)):]
        mask = values.isin(selected).to_numpy()
        train, test = indices[~mask], indices[mask]
    else:
        raise ValueError("unsupported outer split")
    receipt = split_receipt(train, test, rows, times=times, groups=groups)
    if receipt["train_rows"] + receipt["validation_rows"] != rows:
        raise ValueError("outer split lost rows")
    receipt.update({"kind": kind, "seed": seed, "requested_test_fraction": fraction})
    return train, test, receipt


def cv_splits(kind: str, target: Any, folds: int, seed: int, *, times: Any = None, groups: Any = None):
    if isinstance(folds, bool) or not isinstance(folds, int) or not 2 <= folds <= 10:
        raise ValueError("CV folds must be between 2 and 10")
    rows = len(target)
    if kind == "stratified_holdout" and times is None and groups is None:
        splits = list(StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(np.arange(rows), target))
    elif kind == "grouped_holdout" and groups is not None:
        splits = list(GroupKFold(n_splits=folds).split(np.arange(rows), target, groups))
    elif kind == "chronological_holdout" and groups is None:
        if times is None:
            splits = list(TimeSeriesSplit(n_splits=folds).split(np.arange(rows)))
        else:
            timestamps = pd.DatetimeIndex(pd.to_datetime(times, utc=True, errors="raise"))
            unique = np.unique(timestamps.asi8)
            splits = [(np.flatnonzero(np.isin(timestamps.asi8, unique[a])), np.flatnonzero(np.isin(timestamps.asi8, unique[b])))
                      for a, b in TimeSeriesSplit(n_splits=folds).split(unique)]
    else:
        raise ValueError("unsupported CV split")
    receipts = [split_receipt(a, b, rows, times=times, groups=groups) for a, b in splits]
    return splits, receipts


def fit_counts(trials: int, folds: int, *, baseline: bool = False) -> dict[str, int]:
    return {"search_cv_fits": 0 if baseline else trials * folds, "candidate_refit_fits": 0 if baseline else 1,
            "baseline_cv_fits": trials * folds if baseline else 0, "baseline_refit_fits": 1 if baseline else 0,
            "calibration_oof_fits": 0, "calibrator_fits": 0, "stability_fits": 0, "final_holdout_fits": 0}


def sum_fit_counts(records: list[dict[str, int]]) -> dict[str, int]:
    keys = fit_counts(0, 0).keys()
    result = {key: sum(record[key] for record in records) for key in keys}
    result["total_fits"] = sum(result.values())
    return result
