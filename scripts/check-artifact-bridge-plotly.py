#!/usr/bin/env python3
"""Bridge-level check: askO11yPlotlyBindings resolve, sanitize and reject."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def expect_reject(fn, fragment: str) -> None:
    try:
        result = fn()
    except Exception as exc:  # WorkflowContractError or ValueError
        if fragment not in str(exc):
            raise AssertionError(f"expected rejection containing {fragment!r}: {str(exc)[:200]}") from exc
        return
    if isinstance(result, dict) and result.get("ok") is not True and fragment in str(result.get("error")):
        return
    raise AssertionError(f"expected rejection containing {fragment!r}")


def main() -> int:
    bridge = load_module("plotly_bridge", ROOT / "artifact-bridge-mcp/server.py")
    store = bridge.ArtifactStore(Path(tempfile.mkdtemp()) / "runs")
    setattr(bridge, "ARTIFACTS", store)
    context = {"org_id": "1", "user_id": "plotly-e2e"}

    figure = {
        "data": [{"type": "scatter", "mode": "lines", "name": "cost", "x": [0.0, 0.5, 1.0], "y": [300.0, 260.0, 310.0]}],
        "layout": {"title": "門檻取捨"},
    }
    run_id = store.create_run(context)
    store.write_json(context, run_id, "sandbox-execution", {"results": [
        {"text": None, "timestamp": 0, "mime": {"image/png": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")}, "display_name": "fallback.png"},
        {"text": None, "timestamp": 0, "mime": {"application/json": json.dumps(figure)}, "display_name": "Plotly: cost"},
    ]})
    execution_ref = f"artifact://{run_id}/sandbox-execution"

    def resolve(panel: dict):
        return bridge.resolve_dashboard_refs({"dashboard": {
            "uid": "plotly-check", "title": "Plotly check", "tags": ["ask-o11y-preview"],
            "panels": [panel],
        }, "_server_context": context})

    # 1. Plotly binding injects sanitized static options; PNG fallback binding resolves too.
    panel = {
        "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
        "title": "門檻取捨",
        "options": {
            "figure": "$plotly_figure_cost",
            "fallbackUrl": "$asset_url_cost_fallback",
            "alt": "門檻成本圖 fallback",
            "caption": "已選門檻為 train OOF 事實；holdout 僅評估。",
        },
        "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": execution_ref, "output_index": 1, "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
        "askO11yAssetBindings": [{"placeholder": "$asset_url_cost_fallback", "$execution_ref": execution_ref, "output_index": 0}],
    }
    result = resolve(panel)
    assert result["ok"], result
    resolved_panel = result["dashboard"]["panels"][0]
    options = resolved_panel["options"]
    assert isinstance(options["figure"], dict) and "data" in options["figure"] and "config" in options["figure"], options["figure"]
    assert str(options["fallbackUrl"]).startswith("http"), options["fallbackUrl"]
    assert "askO11yPlotlyBindings" not in resolved_panel and "askO11yAssetBindings" not in resolved_panel

    import copy

    # 2. Reject: wrong plugin id.
    bad_plugin = copy.deepcopy(panel)
    bad_plugin["askO11yPlotlyBindings"][0]["plugin_id"] = "nline-plotlyjs-panel"
    expect_reject(lambda: resolve(bad_plugin), "plugin id")

    # 3. Reject: non-empty script in panel options.
    bad_script = copy.deepcopy(panel)
    bad_script["options"]["script"] = "return data"
    expect_reject(lambda: resolve(bad_script), "script")

    # 4. Reject: literal figure data instead of placeholder.
    bad_literal = copy.deepcopy(panel)
    bad_literal["options"]["figure"] = figure
    expect_reject(lambda: resolve(bad_literal), "placeholder")

    # 5. Reject: unsanitizable figure (customdata carrier).
    bad_figure = {"data": [{"type": "scatter", "x": [1], "y": [1], "customdata": ["row-1"]}], "layout": {}}
    run_bad = store.create_run(context)
    store.write_json(context, run_bad, "sandbox-execution", {"results": [
        {"text": None, "timestamp": 0, "mime": {"image/png": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")}, "display_name": "fallback.png"},
        {"text": None, "timestamp": 0, "mime": {"application/json": json.dumps(bad_figure)}, "display_name": "Plotly: bad"},
    ]})
    bad_ref = f"artifact://{run_bad}/sandbox-execution"
    bad_carrier = copy.deepcopy(panel)
    bad_carrier["askO11yPlotlyBindings"][0]["$execution_ref"] = bad_ref
    expect_reject(lambda: resolve(bad_carrier), "customdata")

    # 6. Reject: plotly panel without PNG fallback binding.
    try:
        no_fallback = json.loads(json.dumps(panel))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("fixture clone failed") from exc
    no_fallback.pop("askO11yAssetBindings")
    no_fallback["options"].pop("fallbackUrl")
    expect_reject(lambda: resolve(no_fallback), "fallback")

    print("ok: artifact bridge plotly bindings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
