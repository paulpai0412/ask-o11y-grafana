#!/usr/bin/env python3
"""Self-check for the five-act runtime ML dashboard write-gate."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def image_panel(title: str, placeholder: str, index: int, caption: str) -> dict:
    return {
        "type": "text", "title": title,
        "options": {"mode": "html", "content": f'<img src="{placeholder}" alt="{title}"><div>{caption}</div>'},
        "askO11yAssetBindings": [{"placeholder": placeholder, "$execution_ref": "artifact://run/x/sandbox-execution", "output_index": index}],
    }


def strip_bindings(value):
    if isinstance(value, dict):
        return {key: strip_bindings(child) for key, child in value.items() if key != "askO11yAssetBindings"}
    if isinstance(value, list):
        return [strip_bindings(child) for child in value]
    return value


def good_dashboard() -> dict:
    return {
        "uid": "ml-preview", "tags": ["ask-o11y-preview", "ask-o11y-ml"],
        "panels": [
            {"type": "row", "title": "1. 決策摘要", "collapsed": False},
            {"type": "text", "title": "模型驗證與營運狀態", "options": {"mode": "markdown", "content": "模型驗證：通過；營運使用：尚待確認誤判與漏判成本。每 1,000 筆約 870 筆判對、130 筆判錯。"}},
            image_panel("新舊模型每千筆結果", "$asset_url_decision", 0, "新模型每千筆較少錯。"),
            {"type": "row", "title": "2. 資料地圖", "collapsed": False},
            {"type": "text", "title": "分析目的與結論", "options": {"mode": "markdown", "content": "分析目的：預測流失。\n結論：先確認資料形狀再判讀模型。"}},
            image_panel("資料分布與集中性", "$asset_url_profile", 1, "資料分布顯示欄位集中性與偏態。"),
            image_panel("特徵與目標描述性關係", "$asset_url_relationship", 2, "關係是描述性、非因果，未用來重選門檻。"),
            {"type": "row", "title": "3. 模型歸因", "collapsed": False},
            {"type": "text", "title": "重要欄位與 SHAP", "options": {"mode": "markdown", "content": "特徵重要度與 SHAP 只表示模型關聯。"}},
            {"type": "row", "title": "4. 模型證據與技術細節", "collapsed": True, "panels": [
                image_panel("Holdout 錯誤切片", "$asset_url_errors", 3, "切片只指出調查方向，不代表公平性結論。"),
                {"type": "text", "title": "技術細節", "options": {"mode": "html", "content": "<details><summary>技術證據</summary>calibration、threshold cost、seed=42</details>"}},
            ]},
            {"type": "row", "title": "5. 部署決策", "collapsed": False},
            {"type": "text", "title": "部署前下一步", "options": {"mode": "markdown", "content": "確認成本並核准門檻後才受控部署。"}},
        ],
    }


def main() -> int:
    spec = importlib.util.spec_from_file_location("ml_gate", ROOT / "ml_dashboard_contract.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ml_dashboard_contract.py")
    contract = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contract
    spec.loader.exec_module(contract)

    good = good_dashboard()
    contract.validate_ml_dashboard_minimum(good)
    bad_cases = [
        ("missing preview tag", {**good, "tags": ["ml-preview"]}),
        ("no decision summary", {**good, "panels": [panel for panel in good["panels"] if panel.get("title") != "模型驗證與營運狀態"]}),
        ("missing data story", {**good, "panels": [panel for panel in good["panels"] if panel.get("title") != "特徵與目標描述性關係"]}),
        ("missing error slices", {**good, "panels": [panel for panel in good["panels"] if panel.get("title") != "4. 模型證據與技術細節"]}),
        ("native targets", {**good, "panels": [*good["panels"], {"type": "timeseries", "targets": [{"refId": "A"}]}]}),
        ("no opaque bindings", strip_bindings(good)),
    ]
    for name, bad in bad_cases:
        try:
            contract.validate_ml_dashboard_minimum(bad)
        except ValueError as exc:
            if not str(exc):
                raise AssertionError(f"unsafe ML dashboard rejection lacked a reason: {name}") from exc
        else:
            raise AssertionError(f"unsafe ML dashboard accepted: {name}")

    bridge_spec = importlib.util.spec_from_file_location("ml_gate_bridge", ROOT / "artifact-bridge-mcp/server.py")
    if bridge_spec is None or bridge_spec.loader is None:
        raise RuntimeError("cannot load artifact-bridge-mcp/server.py")
    bridge = importlib.util.module_from_spec(bridge_spec)
    sys.modules[bridge_spec.name] = bridge
    bridge_spec.loader.exec_module(bridge)

    with tempfile.TemporaryDirectory() as tmp:
        setattr(bridge, "ARTIFACTS", bridge.ArtifactStore(Path(tmp) / "runs"))
        context = {"org_id": "1", "user_id": "gate-check"}
        run_id = bridge.ARTIFACTS.create_run(context)
        results = [{"mime": {"image/png": "iVBORw0KGgo="}, "display_name": f"chart-{index}.png"} for index in range(4)]
        execution_ref = bridge.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": results, "error": None})

        def rebind(dashboard: dict) -> dict:
            text = json.dumps(dashboard).replace("artifact://run/x/sandbox-execution", execution_ref)
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"dashboard JSON became invalid after rebinding: {exc}") from exc

        resolved = bridge.resolve_dashboard_refs({"dashboard": rebind(good), "_server_context": context})
        assert resolved["ok"], resolved
        for name, bad in bad_cases:
            denied = bridge.resolve_dashboard_refs({"dashboard": rebind(bad), "_server_context": context})
            assert not denied["ok"], f"bridge accepted unsafe ML dashboard: {name}"

    print("ok: five-act ML dashboard write-gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
