#!/usr/bin/env python3
"""Self-check for a safe, plain-language ML Grafana Preview contract."""
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


def valid_dashboard():
    return {
        "uid": "adult-ml-preview",
        "title": "Adult 模型結果（預覽）",
        "tags": ["ask-o11y-preview", "ask-o11y-ml"],
        "panels": [
            {"type": "row", "title": "1. 決策摘要", "collapsed": False},
            {"type": "text", "title": "現在可以怎麼使用？", "options": {"mode": "markdown", "content": "# 模型驗證：通過\n## 營運使用：尚待確認誤判與漏判成本\n每 1,000 筆約 870 筆判對、130 筆判錯。\n比舊模型每 1,000 筆少錯約 36 筆。\n下一步：確認漏判與誤判成本。"}},
            {"type": "text", "title": "每 1,000 筆結果", "options": {"mode": "html", "content": "<img src=\"$asset_url_outcomes\" alt=\"每一千筆判對與判錯\">"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_outcomes", "$execution_ref": "artifact://run/result", "output_index": 0}]},
            {"type": "row", "title": "2. 分析過程", "collapsed": False},
            {"type": "text", "title": "結果怎麼產生？", "options": {"mode": "markdown", "content": "✓ 資料完整性檢查\n✓ 確認預測目標\n✓ 排除不應使用的欄位\n✓ 保留未見資料\n✓ 比較 40 組設定\n✓ 新資料驗證通過"}},
            {"type": "row", "title": "3. 實際結果", "collapsed": False},
            {"type": "text", "title": "新舊模型錯誤比較", "options": {"mode": "html", "content": "<img src=\"$asset_url_errors\" alt=\"新舊模型錯誤率比較\">"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_errors", "$execution_ref": "artifact://run/result", "output_index": 1}]},
            {"type": "row", "title": "4. 泛化、解釋與工程細節", "collapsed": True, "panels": [
                {"type": "text", "title": "技術明細", "options": {"mode": "markdown", "content": "CV-holdout gap 0.00374；PSI 0.00221；技術資訊僅供工程檢查。"}}
            ]},
        ],
    }


def main() -> int:
    contract = load_module()
    dashboard = valid_dashboard()
    contract.validate_preview_dashboard(dashboard)

    bad_cases = [
        {**dashboard, "tags": ["ask-o11y-ml"]},
        {**dashboard, "panels": dashboard["panels"][1:]},
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

    print("ok: plain-language ML Grafana Preview contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
