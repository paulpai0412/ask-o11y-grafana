#!/usr/bin/env python3
"""Public-seam check for the bounded Plotly figure contract."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "ml_plotly_contract.py"
    spec = importlib.util.spec_from_file_location("ml_plotly_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def expect_reject(contract, figure: dict, fragment: str) -> None:
    try:
        contract.sanitize_figure(figure)
    except ValueError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected rejection containing {fragment!r}: {str(exc)[:200]}") from exc
        return
    raise AssertionError(f"expected rejection containing {fragment!r}: {json.dumps(figure)[:200]}")


def main() -> int:
    contract = load_module()

    # Valid scatter passes and gains host-owned config only.
    clean = contract.sanitize_figure({
        "data": [{"type": "scatter", "mode": "lines", "x": [1, 2, 3], "y": [0.1, 0.2, 0.3], "name": "cost"}],
        "layout": {"title": "成本曲線", "xaxis": {"title": "門檻"}, "yaxis": {"title": "成本"}},
    })
    assert set(clean) == {"data", "layout", "config"}, clean
    assert clean["config"] == {"displaylogo": False, "responsive": True}, clean["config"]

    # Allowed trace types.
    for trace_type, trace in (
        ("bar", {"type": "bar", "x": ["a", "b"], "y": [1, 2]}),
        ("heatmap", {"type": "heatmap", "z": [[1, 2], [3, 4]]}),
        ("indicator", {"type": "indicator", "value": 798, "delta": {"reference": 735}}),
        ("sankey", {"type": "sankey", "node": {"label": ["a", "b"]}, "link": {"source": [0], "target": [1], "value": [3]}}),
    ):
        contract.sanitize_figure({"data": [trace], "layout": {}})

    # Forbidden trace type and forbidden recursive keys.
    expect_reject(contract, {"data": [{"type": "scattergeo", "lat": [1], "lon": [2]}], "layout": {}}, "unsupported trace")
    for key, value in (
        ("frames", []),
        ("transforms", []),
        ("customdata", [1]),
        ("ids", ["x"]),
        ("meta", {}),
        ("hovertemplate", "x"),
        ("texttemplate", "x"),
        ("images", [{}]),
        ("template", {}),
        ("updatemenus", []),
        ("sliders", []),
        ("href", "https://x"),
        ("src", "x"),
        ("base64", "x"),
        ("script", "x"),
        ("onclick", "x"),
        ("callback", "x"),
    ):
        expect_reject(contract, {"data": [{"type": "scatter", "x": [1], key: value}], "layout": {}}, key)
        expect_reject(contract, {"data": [{"type": "scatter", "x": [1]}], "layout": {key: value}}, key)

    # Layout whitelist.
    expect_reject(contract, {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {"unknown_key": 1}}, "layout key")

    # Dangerous strings.
    expect_reject(contract, {"data": [{"type": "bar", "x": ["<b>x</b>"], "y": [1]}], "layout": {}}, "forbidden character")
    expect_reject(contract, {"data": [{"type": "bar", "x": ["javascript:alert(1)"], "y": [1]}], "layout": {}}, "forbidden character")
    expect_reject(contract, {"data": [{"type": "bar", "x": ["https://evil.example"], "y": [1]}], "layout": {}}, "forbidden character")
    expect_reject(contract, {"data": [{"type": "bar", "x": ["x" * 201], "y": [1]}], "layout": {}}, "string length")

    # Non-finite numbers and booleans-as-numbers.
    try:
        contract.sanitize_figure({"data": [{"type": "scatter", "x": [1, float("nan")], "y": [1]}], "layout": {}})
    except ValueError:
        pass
    else:
        raise AssertionError("expected NaN rejection")
    expect_reject(contract, {"data": [{"type": "scatter", "x": [True], "y": [1]}], "layout": {}}, "expected string items")
    expect_reject(contract, {"data": [{"type": "scatter", "x": [1, "two"], "y": [1, 2]}], "layout": {}}, "expected string items")

    # Point budgets.
    expect_reject(
        contract,
        {"data": [{"type": "scatter", "x": list(range(2001)), "y": list(range(2001))}], "layout": {}},
        "points",
    )
    big_z = [[0] * 101 for _ in range(101)]
    expect_reject(contract, {"data": [{"type": "heatmap", "z": big_z}], "layout": {}}, "points")

    # Figure byte budget.
    fat = contract.sanitize_figure({"data": [{"type": "scatter", "x": list(range(1500)), "y": [0.123456789] * 1500}], "layout": {}})
    assert len(json.dumps(fat).encode()) <= contract.MAX_FIGURE_BYTES
    wide = {"data": [{"type": "scatter", "x": list(range(2000)), "y": ["label_%d" % i for i in range(2000)]}], "layout": {}}
    figure = contract.sanitize_figure(wide)
    if len(json.dumps(figure).encode()) > contract.MAX_FIGURE_BYTES:
        expect_reject(contract, wide, "bytes")

    # Execution-level budget.
    figures = [{"data": [{"type": "scatter", "x": list(range(1500)), "y": list(range(1500))}], "layout": {}} for _ in range(15)]
    try:
        contract.sanitize_execution(figures)
    except ValueError as exc:
        assert "figure count" in str(exc), str(exc)
    else:
        raise AssertionError("expected figure-count rejection")
    kept = contract.sanitize_execution(figures[:14])
    assert len(kept) == 14 and all(math.isfinite(1.0) for _ in kept)

    print("ok: ml plotly contract sanitizer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
