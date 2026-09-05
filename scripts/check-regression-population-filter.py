#!/usr/bin/env python3
"""TDD check for generic regression population filters."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    regression = load("population_filter_regression", ROOT / "sandbox-analysis-mcp/ml_regression.py")
    frame = pd.DataFrame({
        "source_a": ["Australia", "Australia", "Indonesia", "Australia"],
        "source_b": ["Australia", "Indonesia", "Indonesia", "Australia"],
        "x": np.arange(4, dtype=float),
        "target": [10.0, 11.0, 12.0, 13.0],
    })
    filtered = regression.apply_population_filter(frame, {"source_a": "Australia", "source_b": "Australia"})
    assert len(filtered) == 2 and set(filtered["x"]) == {0.0, 3.0}
    assert list(frame.columns) == list(filtered.columns)
    for candidate in ({"source_a": "Mars"}, {"unknown": "Australia"}, {"source_a": ["Australia"]}):
        try:
            regression.apply_population_filter(frame, candidate)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe population filter accepted: {candidate}")

    server = load("population_filter_server", ROOT / "sandbox-analysis-mcp/server.py")
    plan = {
        "analysis_contract": {
            "task_kind": "regression", "target": "target", "features": ["x"],
            "split": {"kind": "chronological_holdout", "time_field": "source_a", "test_fraction": 0.2},
            "population_filter": {"source_a": "Australia"},
        },
        "analysis_input_contract": {"autoresearch": {"objective": "mae", "search_budget": 2}},
    }
    template = server.compose_regression_template(plan, plan["analysis_contract"], 42)
    assert "apply_population_filter" in template
    assert "POPULATION_FILTER = {'source_a': 'Australia'}" in template
    assert "population_filter" in template

    print("ok: generic regression population filter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
