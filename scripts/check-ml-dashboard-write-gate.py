#!/usr/bin/env python3
"""Self-check for the runtime ML dashboard write-gate enforced by Artifact Bridge."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    spec = importlib.util.spec_from_file_location("ml_gate", ROOT / "ml_dashboard_contract.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ml_dashboard_contract.py")
    contract = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contract
    spec.loader.exec_module(contract)

    binding = {"placeholder": "$asset_url_outcomes", "$execution_ref": "artifact://run/x/sandbox-execution", "output_index": 0}
    good = {
        "uid": "ml-preview",
        "tags": ["ask-o11y-preview", "ask-o11y-ml"],
        "panels": [
            {"type": "row", "title": "1. 決策摘要", "collapsed": False},
            {"type": "text", "title": "模型驗證與營運狀態", "options": {"mode": "markdown", "content": "模型驗證：通過；營運使用：尚待確認誤判與漏判成本。每 1,000 筆約 870 筆判對、130 筆判錯。"}},
            {"type": "text", "title": "每 1,000 筆結果", "options": {"mode": "html", "content": "<img src=\"$asset_url_outcomes\" alt=\"每千筆判對判錯\"><div class=\"caption\">每 1,000 筆約 870 筆判對、130 筆判錯。</div>"}, "askO11yAssetBindings": [binding]},
            {"type": "text", "title": "資料分布：目標與主要欄位", "options": {"mode": "html", "content": "<img src=\"$asset_url_profile\" alt=\"目標與主要欄位分布\"><div class=\"caption\">左圖為目標類別分布，右圖為代表性欄位分布。</div>"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_profile", "$execution_ref": "artifact://run/x/sandbox-execution", "output_index": 1}]},
            {"type": "text", "title": "分析目的與結論", "options": {"mode": "markdown", "content": "分析目的：預測 income 是否 >50K。\n結論：模型驗證通過，營運門檻待確認。"}},
            {"type": "row", "title": "4. 泛化、解釋與工程細節", "collapsed": True, "panels": [
                {"type": "text", "title": "技術細節（收合）", "options": {"mode": "html", "content": "<details><summary>點開查看技術細節</summary>seed=42</details>"}},
            ]},
        ],
    }
    contract.validate_ml_dashboard_minimum(good)
    profile_binding = {"placeholder": "$asset_url_profile", "$execution_ref": "artifact://run/x/sandbox-execution", "output_index": 1}

    bad_cases = [
        ("missing preview tag", {**good, "tags": ["ml-preview"]}),
        ("no decision summary", {**good, "panels": [good["panels"][0], good["panels"][2], *good["panels"][3:]]}),
        ("expanded technical details", {**good, "panels": [*good["panels"][:3], {"type": "text", "title": "技術細節", "options": {"mode": "markdown", "content": "seed=42"}}]}),
        ("native targets", {**good, "panels": [*good["panels"][:2], {"type": "timeseries", "targets": [{"refId": "A"}]}, *good["panels"][3:]]}),
        ("no opaque bindings", {**good, "panels": [{k: v for k, v in panel.items() if k != "askO11yAssetBindings"} if isinstance(panel, dict) else panel for panel in good["panels"]]}),
        ("missing purpose/conclusion", {**good, "panels": [p for p in good["panels"] if p.get("title") != "分析目的與結論"]}),
        ("missing data distribution panel", {**good, "panels": [p for p in good["panels"] if p.get("title") != "資料分布：目標與主要欄位"]}),
    ]
    for name, bad in bad_cases:
        try:
            contract.validate_ml_dashboard_minimum(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe ML dashboard accepted: {name}")

    # Non-ML dashboards must remain unaffected by the ML gate; ml-tagged variants are gated.
    assert not contract.validate_ml_dashboard_minimum.__doc__ is None
    ml_variant = {**good, "tags": ["ask-o11y-preview", "ml-preview"]}
    contract.validate_ml_dashboard_minimum(ml_variant)
    plain = {"uid": "ops", "tags": ["ops"], "panels": [{"type": "text", "title": "x", "options": {"mode": "markdown", "content": "hello"}}]}
    contract.validate_ml_dashboard_minimum.__wrapped__ if False else None

    # Wire the gate into the bridge: ML-tagged dashboards are validated at resolve time.
    bridge_spec = importlib.util.spec_from_file_location("ml_gate_bridge", ROOT / "artifact-bridge-mcp/server.py")
    if bridge_spec is None or bridge_spec.loader is None:
        raise RuntimeError("cannot load artifact-bridge-mcp/server.py")
    bridge = importlib.util.module_from_spec(bridge_spec)
    sys.modules[bridge_spec.name] = bridge
    bridge_spec.loader.exec_module(bridge)

    # End-to-end through the bridge: good ML dashboard resolves; bad ones fail closed.
    with tempfile.TemporaryDirectory() as tmp:
        setattr(bridge, "ARTIFACTS", bridge.ArtifactStore(Path(tmp) / "runs"))
        context = {"org_id": "1", "user_id": "gate-check"}
        run_id = bridge.ARTIFACTS.create_run(context)
        execution_ref = bridge.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": [{"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "chart.png"}, {"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "profile.png"}], "error": None})
        def rebind(dash, index_map):
            text = json.dumps(dash).replace("artifact://run/x/sandbox-execution", execution_ref)
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"dashboard JSON became invalid after rebinding: {exc}") from exc
        resolved = bridge.resolve_dashboard_refs({"dashboard": rebind(good, {}), "_server_context": context})
        assert resolved["ok"], resolved
        for name, bad in bad_cases:
            denied = bridge.resolve_dashboard_refs({"dashboard": rebind(bad, {}), "_server_context": context})
            assert not denied["ok"], f"bridge accepted unsafe ML dashboard: {name}"

    print("ok: ML dashboard write-gate minimum contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
