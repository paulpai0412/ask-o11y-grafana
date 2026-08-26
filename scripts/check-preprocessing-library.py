#!/usr/bin/env python3
"""Self-check for deterministic sandbox ML preprocessing (Workstream D)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_preprocessing.py"
    spec = importlib.util.spec_from_file_location("ml_preprocessing", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    prep = load_module()

    # Nullable string dtype must yield a pure bool mask, never pd.NA.
    target = pd.Series([">50K", pd.NA, "<=50K", ">50K"], dtype="string")
    mask = prep.safe_membership_mask(target, {"<=50K", ">50K"})
    assert str(mask.dtype) == "bool" and mask.tolist() == [True, False, True, True]

    train = pd.DataFrame({
        "age": [25, 30, np.nan, 45, 51, 60],
        "shift": ["A", "A", "B", None, "B", "A"],
        "serial": ["s1", "s2", "s3", "s4", "s5", "s6"],
        "x": [1, 2, 3, 4, 5, 6],
        "x_twice": [2, 4, 6, 8, 10, 12],
    })
    y = pd.Series([0, 0, 1, 1, 1, 0])
    test = pd.DataFrame({
        "age": [38, np.nan],
        "shift": ["C", "A"],  # unseen category must not fail
        "serial": ["new-1", "new-2"],
        "x": [7, 8],
        "x_twice": [14, 16],
    })

    kept, removed = prep.prune_numeric_vif(train, threshold=10.0)
    assert len({"x", "x_twice"}.intersection(removed)) == 1, f"kept={kept}, removed={removed}"

    result = prep.fit_transform_train_test(train[kept], y, test[kept], high_cardinality_threshold=3)
    assert result["train"].shape[0] == len(train)
    assert result["test"].shape[0] == len(test)
    assert np.isfinite(result["train"]).all() and np.isfinite(result["test"]).all()
    assert "serial" in result["high_cardinality_fields"], result
    assert "shift" in result["low_cardinality_fields"], result
    assert result["fit_scope"] == "training_only"

    print("ok: deterministic training-only preprocessing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
