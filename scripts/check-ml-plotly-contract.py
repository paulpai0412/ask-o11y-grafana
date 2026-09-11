#!/usr/bin/env python3
"""Frozen v1 readback compatibility plus the native v2 producer regression."""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import math
import sys
import subprocess
import struct
from pathlib import Path

import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "_plotly_legacy.py"
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

    class IntendedRed(Exception):
        pass

    outcomes: list[tuple[str, str, str]] = []

    def run_case(name: str, case) -> None:
        try:
            case()
        except IntendedRed as exc:
            outcomes.append((name, "intended-red", str(exc)))
        except Exception as exc:
            outcomes.append((name, "failed", f"{type(exc).__name__}: {exc}"))
        else:
            outcomes.append((name, "current-pass", ""))

    def valid_scatter_case() -> None:
        clean = contract.sanitize_figure({
            "data": [{"type": "scatter", "mode": "lines", "x": [1, 2, 3], "y": [0.1, 0.2, 0.3], "name": "cost"}],
            "layout": {"title": "成本曲線", "xaxis": {"title": "門檻"}, "yaxis": {"title": "成本"}},
        })
        assert set(clean) == {"data", "layout", "config"}, clean
        assert clean["config"] == {"displaylogo": False, "responsive": True}, clean["config"]
        assert contract.sanitize_figure(clean) == clean, "sanitizer must be idempotent across Sandbox → Bridge"

    def reference_lines_case() -> None:
        produced = go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))
        produced.add_hline(y=3.5, line_dash="dash", line_color="#e45756")
        produced.add_vline(x=1.5)
        figure = produced.to_plotly_json()
        clean = contract.sanitize_figure(figure)
        assert clean["layout"]["shapes"] == figure["layout"]["shapes"]
        assert contract.sanitize_figure(clean) == clean
        line = clean["layout"]["shapes"][0]
        for changed, fragment in (
            ({**line, "type": "path", "path": "M0,0L1,1"}, "shape"),
            ({**line, "x0": math.nan}, "finite"),
            ({**line, "xref": "paper"}, "reference"),
            ({**line, "line": {"color": "https://invalid.example"}}, "color"),
            ({**line, "line": {"width": -1}}, "width"),
            ({**line, "line": {"dash": "anything"}}, "dash"),
        ):
            expect_reject(contract, {**clean, "layout": {"shapes": [changed]}}, fragment)
        expect_reject(contract, {**clean, "layout": {"shapes": [line] * 9}}, "budget")
        expect_reject(contract, {**clean, "data": [{**clean["data"][0], "xaxis": "x2"}]}, "single Cartesian view")

    def typed_heatmap_case() -> None:
        matrix = {"dtype": "f8", "bdata": base64.b64encode(struct.pack("<4d", 1, 0.5, 0.5, 1)).decode(), "shape": "2, 2"}
        figure = {"data": [{"type": "heatmap", "z": matrix, "zmin": -1, "zmax": 1, "reversescale": True, "colorscale": go.Heatmap(colorscale="RdBu").to_plotly_json()["colorscale"], "colorbar": {"title": {"text": "Spearman"}}}], "layout": {}}
        clean = contract.sanitize_figure(figure)
        assert clean["data"][0]["z"] == [[1, 0.5], [0.5, 1]]
        assert clean["data"][0]["colorbar"] == figure["data"][0]["colorbar"]
        assert clean["data"][0]["colorscale"][0] == [0.0, "#67001f"]
        expect_reject(contract, {**figure, "data": [{**figure["data"][0], "colorscale": [[0, "rgb(999,0,0)"]]}]}, "color")
        assert contract.sanitize_figure(clean) == clean
        for shape in ("1, 4, 1", "0, 2", "10000, 2", "3, 2"):
            expect_reject(contract, {**figure, "data": [{**figure["data"][0], "z": {**matrix, "shape": shape}}]}, "shape")
        expect_reject(contract, {**figure, "data": [{**figure["data"][0], "z": {**matrix, "bdata": base64.b64encode(struct.pack("<4d", 1, math.nan, 0.5, 1)).decode()}}]}, "finite")
        expect_reject(contract, {**figure, "data": [{**figure["data"][0], "reversescale": "true"}]}, "boolean")

    def fixed_config_case() -> None:
        clean = contract.sanitize_figure({
            "data": [{"type": "scatter", "mode": "lines", "x": [1, 2, 3], "y": [0.1, 0.2, 0.3], "name": "cost"}],
            "layout": {},
        })
        expect_reject(contract, {**clean, "config": {"displaylogo": True}}, "config")

    def allowed_trace_types_case() -> None:
        for trace_type, trace in (
            ("bar", {"type": "bar", "x": ["a", "b"], "y": [1, 2]}),
            ("box", {"type": "box", "name": "heat rate", "y": [1, 2, 3, 4], "boxmean": True}),
            ("candlestick", {"type": "candlestick", "x": ["a", "b"], "open": [1, 2], "high": [2, 3], "low": [0, 1], "close": [1.5, 2.5]}),
            ("contour", {"type": "contour", "z": [[1, 2], [3, 4]]}),
            ("funnel", {"type": "funnel", "y": ["a", "b"], "x": [10, 5]}),
            ("funnelarea", {"type": "funnelarea", "labels": ["a", "b"], "values": [10, 5]}),
            ("histogram", {"type": "histogram", "x": [1, 2, 2, 3], "nbinsx": 4, "cumulative": {"enabled": True}}),
            ("histogram2d", {"type": "histogram2d", "x": [1, 2], "y": [3, 4]}),
            ("histogram2dcontour", {"type": "histogram2dcontour", "x": [1, 2], "y": [3, 4]}),
            ("heatmap", {"type": "heatmap", "z": [[1, 2], [3, 4]]}),
            ("indicator", {"type": "indicator", "value": 798, "delta": {"reference": 735}}),
            ("ohlc", {"type": "ohlc", "x": ["a", "b"], "open": [1, 2], "high": [2, 3], "low": [0, 1], "close": [1.5, 2.5]}),
            ("pie", {"type": "pie", "labels": ["a", "b"], "values": [2, 3], "hole": 0.3}),
            ("sankey", {"type": "sankey", "node": {"label": ["a", "b"]}, "link": {"source": [0], "target": [1], "value": [3]}}),
            ("scattergl", {"type": "scattergl", "x": [1, 2], "y": [3, 4]}),
            ("sunburst", {"type": "sunburst", "labels": ["A", "B"], "parents": ["", "A"], "values": [10, 5]}),
            ("treemap", {"type": "treemap", "labels": ["A", "B"], "parents": ["", "A"], "values": [10, 5]}),
            ("violin", {"type": "violin", "y": [1, 2, 3, 4], "box": {"visible": True}, "meanline": {"visible": True}, "points": False}),
            ("waterfall", {"type": "waterfall", "x": ["a", "b", "total"], "y": [1, -0.5, 0], "measure": ["relative", "relative", "total"]}),
        ):
            contract.sanitize_figure({"data": [trace], "layout": {}})
        for boxpoints in (False, "all", "outliers", "suspectedoutliers"):
            clean = contract.sanitize_figure({"data": [{"type": "box", "y": [1, 2, 3], "boxpoints": boxpoints}], "layout": {}})
            assert clean["data"][0]["boxpoints"] == boxpoints
        expect_reject(contract, {"data": [{"type": "box", "y": [1, 2], "boxpoints": True}], "layout": {}}, "points")
        for boxmean in (False, "sd"):
            clean = contract.sanitize_figure({"data": [{"type": "box", "y": [1, 2, 3], "boxmean": boxmean}], "layout": {}})
            assert clean["data"][0]["boxmean"] == boxmean, clean
        expect_reject(contract, {"data": [{"type": "box", "y": [1, 2], "boxmean": "mean"}], "layout": {}}, "boxmean")
        expect_reject(contract, {"data": [{"type": "waterfall", "x": ["a"], "y": [1], "measure": ["invalid"]}], "layout": {}}, "waterfall measure")
        expect_reject(contract, {"data": [{"type": "pie", "labels": ["a"], "values": [1], "hole": 1.1}], "layout": {}}, "hole")

    def forbidden_keys_case() -> None:
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

    def default_template_case() -> None:
        # Use the installed GO producer's exact default; arbitrary or mutated
        # templates must remain rejected rather than silently stripped.
        produced = go.Figure(go.Bar(x=["a"], y=[1])).to_plotly_json()
        clean = contract.sanitize_figure(produced)
        assert "template" not in clean["layout"], clean
        mutated = copy.deepcopy(produced)
        mutated["layout"]["template"]["layout"]["font"]["size"] = 12
        expect_reject(contract, mutated, "installed Plotly default")

    def axis_layout_case() -> None:
        contract.sanitize_figure({
            "data": [{"type": "bar", "x": ["a"], "y": [1], "xaxis": "x12", "yaxis": "y12"}],
            "layout": {"xaxis12": {"title": "x"}, "yaxis12": {"title": "y"}},
        })
        rotated = contract.sanitize_figure({
            "data": [{"type": "bar", "x": ["long label"], "y": [1]}],
            "layout": {"xaxis": {"tickangle": -35}},
        })
        assert rotated["layout"]["xaxis"]["tickangle"] == -35
        contract.sanitize_figure({
            "data": [{"type": "bar", "x": ["long label"], "y": [1]}],
            "layout": {"xaxis": {"tickangle": "auto"}},
        })
        expect_reject(contract, {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {"xaxis": {"tickangle": 181}}}, "tickangle")
        capabilities = contract.capability_summary()
        assert "tickangle" in capabilities["axis_keys"] and capabilities["limits"]["tickangle"]["max"] == 180
        expect_reject(contract, {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {"xaxis13": {"title": "x"}}}, "layout key")
        expect_reject(contract, {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {"unknown_key": 1}}, "layout key")

    def dangerous_strings_case() -> None:
        expect_reject(contract, {"data": [{"type": "bar", "x": ["<b>x</b>"], "y": [1]}], "layout": {}}, "forbidden character")
        expect_reject(contract, {"data": [{"type": "bar", "x": ["javascript:alert(1)"], "y": [1]}], "layout": {}}, "forbidden character")
        expect_reject(contract, {"data": [{"type": "bar", "x": ["https://evil.example"], "y": [1]}], "layout": {}}, "forbidden character")
        expect_reject(contract, {"data": [{"type": "bar", "x": ["x" * 201], "y": [1]}], "layout": {}}, "string length")

    def numeric_validation_case() -> None:
        expect_reject(contract, {"data": [{"type": "scatter", "x": [1, math.nan], "y": [1]}], "layout": {}}, "finite")
        expect_reject(contract, {"data": [{"type": "scatter", "x": [True], "y": [1]}], "layout": {}}, "expected string items")
        expect_reject(contract, {"data": [{"type": "scatter", "x": [1, "two"], "y": [1, 2]}], "layout": {}}, "expected string items")

    def point_budget_array_case() -> None:
        expect_reject(
            contract,
            {"data": [{"type": "scatter", "x": list(range(2001)), "y": list(range(2001))}], "layout": {}},
            "points",
        )

    def point_budget_heatmap_case() -> None:
        big_z = [[0] * 101 for _ in range(101)]
        expect_reject(contract, {"data": [{"type": "heatmap", "z": big_z}], "layout": {}}, "points")

    def aggregate_point_budget_case() -> None:
        aggregate_over_budget = {
            "data": [
                {"type": "scatter", "x": list(range(2000)), "y": list(range(2000)), "text": ["point"] * 2000},
                {"type": "bar", "x": ["overflow"], "y": [1]},
            ],
            "layout": {},
        }
        try:
            contract.sanitize_figure(aggregate_over_budget)
        except ValueError as exc:
            if "points" not in str(exc):
                raise AssertionError(f"aggregate budget rejected for the wrong reason: {exc}") from exc
            return
        raise IntendedRed("aggregate point budget accepts 6004 bounded points")

    def figure_byte_budget_case() -> None:
        fat = contract.sanitize_figure({"data": [{"type": "scatter", "x": list(range(1500)), "y": [0.123456789] * 1500}], "layout": {}})
        assert len(json.dumps(fat).encode()) <= contract.MAX_FIGURE_BYTES
        wide = {"data": [{"type": "scatter", "x": list(range(2000)), "y": ["label_%d" % i for i in range(2000)]}], "layout": {}}
        figure = contract.sanitize_figure(wide)
        if len(json.dumps(figure).encode()) > contract.MAX_FIGURE_BYTES:
            expect_reject(contract, wide, "bytes")

    def execution_count_budget_case() -> None:
        figures = [{"data": [{"type": "scatter", "x": list(range(1500)), "y": list(range(1500))}], "layout": {}} for _ in range(15)]
        try:
            contract.sanitize_execution(figures)
        except ValueError as exc:
            assert "figure count" in str(exc), str(exc)
        else:
            raise AssertionError("expected figure-count rejection")
        kept = contract.sanitize_execution(figures[:14])
        assert len(kept) == 14 and all(math.isfinite(1.0) for _ in kept)

    for name, case in (
        ("valid-scatter", valid_scatter_case),
        ("reference-lines", reference_lines_case),
        ("typed-heatmap", typed_heatmap_case),
        ("fixed-config-rejects", fixed_config_case),
        ("allowed-trace-types", allowed_trace_types_case),
        ("forbidden-keys", forbidden_keys_case),
        ("default-template-stripped", default_template_case),
        ("axis-layout-whitelist", axis_layout_case),
        ("dangerous-strings", dangerous_strings_case),
        ("numeric-validation", numeric_validation_case),
        ("point-budget-array", point_budget_array_case),
        ("point-budget-heatmap", point_budget_heatmap_case),
        ("aggregate-point-budget", aggregate_point_budget_case),
        ("figure-byte-budget", figure_byte_budget_case),
        ("execution-count-budget", execution_count_budget_case),
    ):
        run_case(name, case)

    for name, status, detail in outcomes:
        suffix = f": {detail}" if detail else ""
        print(f"scenario {name}: {status}{suffix}")
    failed = [item for item in outcomes if item[1] == "failed"]
    intended_red = [item for item in outcomes if item[1] == "intended-red"]
    if failed:
        print(f"failed: ml plotly contract ({len(failed)} unexpected scenario failure(s))")
        return 2
    if intended_red:
        print(f"red: ml plotly contract ({len(intended_red)} attributable production gap(s))")
        return 1
    print("ok: ml plotly contract sanitizer")
    return 0


if __name__ == "__main__":
    result = main()
    if result == 0:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/check-plotly-native-delivery.py')], check=False).returncode
    raise SystemExit(result)
