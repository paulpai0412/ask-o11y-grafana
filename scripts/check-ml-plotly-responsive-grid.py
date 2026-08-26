#!/usr/bin/env python3
"""TDD check for responsive 1..12 Plotly subplot grids."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_presentation.py"
    spec = importlib.util.spec_from_file_location("responsive_grid_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def overlaps(left: dict, right: dict) -> bool:
    return min(left["x"][1], right["x"][1]) > max(left["x"][0], right["x"][0]) and min(left["y"][1], right["y"][1]) > max(left["y"][0], right["y"][0])


def main() -> int:
    module = load_module()
    expected = {1: (1, 1), 2: (1, 2), 3: (2, 2), 4: (2, 2), 5: (2, 3), 6: (2, 3), 7: (3, 3), 8: (3, 3), 9: (3, 3), 10: (3, 4), 11: (3, 4), 12: (3, 4)}
    for count, shape in expected.items():
        grid = module.responsive_subplot_grid(count)
        if len(grid) != count:
            raise AssertionError(f"grid count mismatch: {count}")
        rows = max(item["row"] for item in grid) + 1
        columns = max(item["column"] for item in grid) + 1
        if (rows, columns) != shape:
            raise AssertionError(f"grid shape mismatch for {count}: {(rows, columns)}")
        for item in grid:
            if not (0 <= item["x"][0] < item["x"][1] <= 1 and 0 <= item["y"][0] < item["y"][1] <= 1):
                raise AssertionError(f"invalid domain: {item}")
        for left_index, left in enumerate(grid):
            for right in grid[left_index + 1:]:
                if overlaps(left, right):
                    raise AssertionError(f"overlapping domains for {count}: {left}, {right}")
    try:
        module.responsive_subplot_grid(13)
    except ValueError:
        pass
    else:
        raise AssertionError("grid accepted more than twelve subplots")

    print("ok: responsive Plotly subplot grids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
