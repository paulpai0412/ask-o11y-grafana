#!/usr/bin/env python3
"""TDD check for the real Grafana Plotly panel plugin and fallback contract."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "grafana-panels/asko11y-plotly-panel"


def main() -> int:
    try:
        plugin = json.loads((PANEL / "src/plugin.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"panel plugin metadata missing or invalid: {exc}") from exc
    assert plugin["id"] == "asko11y-plotly-panel" and plugin["type"] == "panel", plugin

    source = (PANEL / "src/module.tsx").read_text()
    assert "Plotly.react" in source and "fallbackUrl" in source and "narrative" in source, source
    for required in ("useTheme2", "ResizeObserver", "Plots.resize", "applyGrafanaTheme"):
        assert required in source, f"missing Grafana-responsive behavior: {required}"
    assert "options.figure.layout, width, height" not in source, "panel must not force a fixed Plotly canvas"
    assert "eval(" not in source and "new Function" not in source, "unsafe dynamic code in panel source"

    render_mode = PANEL / "src/renderMode.mjs"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", f"import{{resolveRenderMode as r}}from'{render_mode.as_uri()}';if(r({{figure:{{data:[],layout:{{}}}}}})!=='plotly'||r({{fallbackUrl:'x'}})!=='fallback')process.exit(2)"],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise AssertionError(f"render mode contract failed: {result.stderr}")

    theme_module = PANEL / "src/theme.mjs"
    theme_result = subprocess.run(
        ["node", "--input-type=module", "-e", f"import{{applyGrafanaTheme as a}}from'{theme_module.as_uri()}';const t={{colors:{{text:{{primary:'#eee'}},border:{{weak:'#333'}},background:{{primary:'#111'}}}},typography:{{fontFamily:'Grafana Sans'}}}};const x=a({{xaxis:{{title:'x'}},yaxis2:{{title:'y'}}}},t);if(x.paper_bgcolor!=='rgba(0,0,0,0)'||x.plot_bgcolor!=='rgba(0,0,0,0)'||x.font.color!=='#eee'||x.xaxis.gridcolor!=='#333'||x.yaxis2.gridcolor!=='#333'||x.autosize!==true)process.exit(3)"],
        check=False, capture_output=True, text=True,
    )
    if theme_result.returncode:
        raise AssertionError(f"Grafana theme contract failed: {theme_result.stderr}")

    dist = PANEL / "dist/module.js"
    if not dist.exists():
        subprocess.run(["npm", "ci", "--prefix", str(PANEL)], check=True)
        subprocess.run(["npm", "--prefix", str(PANEL), "run", "build"], check=True)
    assert dist.stat().st_size > 1_000_000, dist
    bundle = dist.read_text(errors="ignore")
    assert "Plotly" in bundle and "fallbackUrl" in bundle, "Plotly/fallback missing from built panel"
    assert "new Function" not in bundle and "eval(" not in bundle, "dynamic code execution remains in built panel"

    compose = (ROOT / "compose.yaml").read_text()
    installer = (ROOT / "scripts/build-install-ask-o11y.sh").read_text()
    assert "asko11y-plotly-panel" in compose, "unsigned plugin allowlist missing"
    assert "grafana-panels/asko11y-plotly-panel" in installer, "panel build/install step missing"

    print("ok: real Grafana Plotly panel plugin + fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
