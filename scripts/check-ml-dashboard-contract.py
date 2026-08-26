#!/usr/bin/env python3
"""Self-check for the five-act, plain-language ML Grafana Preview contract."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "ml_dashboard_contract.py"
    spec = importlib.util.spec_from_file_location("ml_dashboard_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def image_panel(title: str, placeholder: str, index: int, caption: str) -> dict:
    return {
        "type": "text", "title": title,
        "options": {"mode": "html", "content": f'<img src="{placeholder}" alt="{title}"><div>{caption}</div>'},
        "askO11yAssetBindings": [{"placeholder": placeholder, "$execution_ref": "artifact://run/result", "output_index": index}],
    }


def valid_dashboard():
    return {
        "uid": "adult-ml-preview",
        "title": "Adult 模型結果（預覽）",
        "tags": ["ask-o11y-preview", "ask-o11y-ml"],
        "panels": [
            {"type": "row", "title": "1. 決策摘要", "collapsed": False},
            {"type": "text", "title": "現在可以怎麼使用？", "options": {"mode": "markdown", "content": "# 模型驗證：通過\n## 營運使用：尚待確認誤判與漏判成本\n每 1,000 筆約 870 筆判對、130 筆判錯。\n比舊模型每 1,000 筆少錯約 36 筆。\n下一步：確認漏判與誤判成本。"}},
            image_panel("新舊模型每千筆比較", "$asset_url_decision", 0, "新模型每千筆少錯約 36 筆。"),
            {"type": "row", "title": "2. 資料地圖", "collapsed": False},
            {"type": "text", "title": "分析目的與結論", "options": {"mode": "markdown", "content": "分析目的：預測收入。\n結論：先看資料形狀再判讀模型。"}},
            image_panel("資料分布與集中性", "$asset_url_distribution", 1, "欄位分布顯示偏態與集中性。"),
            image_panel("特徵與目標的描述性關係", "$asset_url_relationship", 2, "描述性關係非因果，也未用來重選門檻。"),
            {"type": "row", "title": "3. 模型歸因", "collapsed": False},
            {"type": "text", "title": "模型學到什麼", "options": {"mode": "markdown", "content": "先看 feature importance，再看 SHAP；關聯不代表因果。"}},
            {"type": "row", "title": "4. 模型證據與技術細節", "collapsed": True, "panels": [
                image_panel("Holdout 錯誤切片", "$asset_url_errors", 3, "錯誤切片只指出調查方向，不代表公平性結論。"),
                {"type": "text", "title": "技術細節", "options": {"mode": "html", "content": "<details><summary>技術證據</summary>calibration、threshold cost、seed=42</details>"}},
            ]},
            {"type": "row", "title": "5. 部署決策", "collapsed": False},
            {"type": "text", "title": "部署前下一步", "options": {"mode": "markdown", "content": "確認漏判與誤判成本，核准門檻後才受控部署。"}},
        ],
    }


def main() -> int:
    contract = load_module()
    dashboard = valid_dashboard()
    contract.validate_preview_dashboard(dashboard)

    bad_cases = [
        {**dashboard, "tags": ["ask-o11y-ml"]},
        {**dashboard, "panels": dashboard["panels"][1:]},
        {**dashboard, "panels": [panel for panel in dashboard["panels"] if panel.get("title") != "2. 資料地圖"]},
        {**dashboard, "panels": [*dashboard["panels"], {"type": "timeseries", "targets": [{"refId": "A"}]}]},
        {**dashboard, "panels": [*dashboard["panels"], {"type": "text", "options": {"content": "http://secret/assets/x?token=abc"}}]},
    ]
    for bad in bad_cases:
        try:
            contract.validate_preview_dashboard(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe or unreadable ML dashboard was accepted")

    print("ok: five-act plain-language ML Grafana Preview contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
