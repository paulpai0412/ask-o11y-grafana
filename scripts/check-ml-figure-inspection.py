#!/usr/bin/env python3
"""TDD check for bounded Plotly view/axis/scale inspection specs."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_module():
    path = ROOT / "ml_figure_inspection.py"
    spec = importlib.util.spec_from_file_location("figure_inspection_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    figure = {
        "data": [
            {"type": "bar", "name": "样本数量", "x": ["甲", "乙"], "y": [10, 20]},
            {"type": "scatter", "name": "温度趋势", "x": [1, 2, 3], "y": [30.0, 32.0, 31.0], "xaxis": "x2", "yaxis": "y2"},
            {"type": "heatmap", "name": "相关结构", "x": ["甲", "乙"], "y": ["甲", "乙"], "z": [[1.0, 0.4], [0.4, 1.0]], "xaxis": "x3", "yaxis": "y3"},
        ],
        "layout": {
            "title": "完整证据图",
            "xaxis": {"title": "类别", "type": "category"}, "yaxis": {"title": "数量", "range": [0, 25]},
            "xaxis2": {"title": "时间", "type": "linear"}, "yaxis2": {"title": "温度 (°C)", "range": [28, 34]},
            "xaxis3": {"title": "栏位"}, "yaxis3": {"title": "栏位"},
        },
    }
    spec = module.inspect_figure("evidence", figure)
    if spec["trace_count"] != 3 or spec["point_count"] != 9:
        raise AssertionError(spec)
    views = {item["view_id"]: item for item in spec["views"]}
    if list(views) != ["view-1", "view-2", "view-3"]:
        raise AssertionError(views)
    if views["view-1"]["x"]["label"] != "类别" or views["view-1"]["y"]["scale"] != "linear":
        raise AssertionError(views["view-1"])
    if views["view-2"]["y"]["label"] != "温度" or views["view-2"]["y"]["unit"] != "°C" or views["view-2"]["y"]["range"] != [28.0, 34.0]:
        raise AssertionError(views["view-2"])
    if views["view-2"]["y"]["data_min"] != 30.0 or views["view-2"]["y"]["data_max"] != 32.0:
        raise AssertionError(views["view-2"])
    if views["view-3"]["trace_types"] != ["heatmap"] or views["view-3"]["value_min"] != 0.4 or views["view-3"]["value_max"] != 1.0:
        raise AssertionError(views["view-3"])

    image = module.inspect_png_only("static-evidence", "静态证据")
    if image["views"] != [{"view_id": "image", "title": "静态证据", "kind": "image"}]:
        raise AssertionError(image)

    print("ok: bounded Plotly figure/view inspection specs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
