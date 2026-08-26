#!/usr/bin/env python3
"""Create, resolve, write, and read back the five-act Telco ML dashboard in Grafana."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".scratch/telco-data-atlas-e2e"
GRAFANA = "http://127.0.0.1:3000"
UID = "telco-ontology-ml-e2e"
EXECUTION_REF = ""

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "artifact-bridge-mcp"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def http_json(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        GRAFANA + path, data=payload, method=method,
        headers={"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}: {exc}") from exc


def binding(name: str, png_index: int) -> list[dict[str, Any]]:
    return [{"placeholder": f"$asset_url_{name}", "$execution_ref": EXECUTION_REF, "output_index": png_index}]


def image_panel(panel_id: int, title: str, name: str, png_index: int, caption: str, grid: dict[str, int]) -> dict[str, Any]:
    return {
        "id": panel_id, "type": "text", "title": title, "gridPos": grid,
        "options": {"mode": "html", "content": f'<img src="$asset_url_{name}" alt="{title}" style="width:100%;height:calc(100% - 56px);object-fit:contain"><div>{caption}</div>'},
        "askO11yAssetBindings": binding(name, png_index),
    }


def plotly_panel(panel_id: int, title: str, name: str, png_index: int, json_index: int, caption: str, grid: dict[str, int]) -> dict[str, Any]:
    return {
        "id": panel_id, "type": "asko11y-plotly-panel", "title": title, "gridPos": grid,
        "options": {"figure": f"$plotly_{name}", "fallbackUrl": f"$asset_url_{name}", "alt": title, "caption": caption},
        "askO11yAssetBindings": binding(name, png_index),
        "askO11yPlotlyBindings": [{"placeholder": f"$plotly_{name}", "$execution_ref": EXECUTION_REF, "output_index": json_index, "plugin_id": "asko11y-plotly-panel"}],
    }


def text_panel(panel_id: int, title: str, content: str, grid: dict[str, int], *, html: bool = False) -> dict[str, Any]:
    return {"id": panel_id, "type": "text", "title": title, "gridPos": grid, "options": {"mode": "html" if html else "markdown", "content": content}}


def row(panel_id: int, title: str, y: int, *, collapsed: bool = False, panels: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"id": panel_id, "type": "row", "title": title, "collapsed": collapsed, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}
    if panels is not None:
        value["panels"] = panels
    return value


def build_dashboard(manifest: dict[str, Any], indices: dict[str, tuple[int, int]]) -> dict[str, Any]:
    decision = manifest["decision"]
    purpose = manifest["purpose"]
    conclusion = manifest["conclusion"]
    summary = (
        f"# {decision['model_evidence_status']}\n"
        f"## {decision['operational_status']}\n"
        f"每 1,000 筆約 {decision['correct_per_1000']} 筆判對、{decision['errors_per_1000']} 筆判錯。\n\n"
        f"每 1,000 筆少錯約 {decision['fewer_errors_per_1000']} 筆。\n\n"
        f"下一步：{decision['recommended_action']}。"
    )
    panel_id = 1

    def next_id() -> int:
        nonlocal panel_id
        value = panel_id
        panel_id += 1
        return value

    def interactive(title: str, name: str, caption: str, grid: dict[str, int]) -> dict[str, Any]:
        png_index, json_index = indices[name]
        return plotly_panel(next_id(), title, name, png_index, json_index, caption, grid)

    def static(title: str, name: str, caption: str, grid: dict[str, int]) -> dict[str, Any]:
        png_index, _ = indices[name]
        return image_panel(next_id(), title, name, png_index, caption, grid)

    panels: list[dict[str, Any]] = [
        row(next_id(), "1. 決策摘要", 0),
        text_panel(next_id(), "模型驗證與營運狀態", summary, {"h": 6, "w": 8, "x": 0, "y": 1}),
        interactive("新舊模型每千筆比較", "baseline_error_comparison", "新模型相較基準每千筆少錯的實際數量。", {"h": 6, "w": 16, "x": 8, "y": 1}),
        row(next_id(), "2. 資料地圖", 7),
        text_panel(next_id(), "分析目的與結論", f"## 分析目的\n{purpose}\n\n## 結論\n{conclusion}", {"h": 5, "w": 24, "x": 0, "y": 8}),
        static("Ontology 欄位地圖", "ontology_field_map", "先確認欄位角色、被排除欄位、缺失與集中警示。", {"h": 11, "w": 12, "x": 0, "y": 13}),
        interactive("資料分布與目標形狀", "data_profile", "目標比例與主要欄位的實際資料分布。", {"h": 11, "w": 12, "x": 12, "y": 13}),
        interactive("欄位分布與集中性", "distribution_small_multiples", "比較核可欄位的偏態、集中與低 cardinality 形狀。", {"h": 12, "w": 24, "x": 0, "y": 24}),
        interactive("Ontology 語義相關性", "semantic_correlation", "相關係數只表示一起變動，不代表因果。", {"h": 11, "w": 12, "x": 0, "y": 36}),
        interactive("特徵與目標的描述性關係", "feature_target_relationships", "實際正類比例差異是描述性、非因果，也未用來重選模型或門檻。", {"h": 11, "w": 12, "x": 12, "y": 36}),
        row(next_id(), "3. 模型歸因", 47),
        interactive("模型主要參考欄位", "feature_importance", "重要性表示模型參考程度，不代表因果。", {"h": 10, "w": 12, "x": 0, "y": 48}),
        static("SHAP 影響方向", "shap_summary", "點的位置表示推向哪個預測方向；顏色代表欄位值高低。", {"h": 10, "w": 12, "x": 12, "y": 48}),
        text_panel(next_id(), "前三大規格建議與敘事", "規格建議來自 manifest 的 SHAP 關聯，不是因果規則；實際部署仍須實驗驗證。", {"h": 4, "w": 24, "x": 0, "y": 58}),
    ]
    evidence_panels = [
        interactive("Holdout 錯誤切片", "error_slice_analysis", "紅色為漏判 FN、橙色為誤報 FP；只指出調查方向，不代表公平性結論。", {"h": 12, "w": 24, "x": 0, "y": 0}),
        static("混淆矩陣", "confusion_matrix", "四格呈現鎖定門檻下的判對、漏判與誤報。", {"h": 10, "w": 12, "x": 0, "y": 12}),
        static("ROC 與 PR", "roc_pr_curves", "排序能力與少數類別辨識的門檻取捨。", {"h": 10, "w": 12, "x": 12, "y": 12}),
        static("機率校正", "calibration_curve", "校正器以 train OOF 選擇；本圖只用 holdout 評估。", {"h": 10, "w": 12, "x": 0, "y": 22}),
        interactive("門檻成本曲線", "threshold_cost_curve", "門檻由 train OOF 鎖定，holdout 不再重選。", {"h": 10, "w": 12, "x": 12, "y": 22}),
        static("泛化健康", "generalization_health", "泛化差距、重要性穩定度與 PSI 的 bounded guard。", {"h": 9, "w": 24, "x": 0, "y": 32}),
        text_panel(next_id(), "技術細節", "<details><summary>展開技術證據</summary>calibration、threshold-cost、seed、ontology hash 與限制均保留於 manifest。</details>", {"h": 4, "w": 24, "x": 0, "y": 41}, html=True),
    ]
    panels.extend([
        row(next_id(), "4. 模型證據與技術細節", 62, collapsed=True, panels=evidence_panels),
        row(next_id(), "5. 部署決策", 63),
        text_panel(next_id(), "部署前下一步", f"## 部署決策\n{decision['recommended_action']}。\n\n先確認 FN/FP 成本與監控規則，再核准受控部署。", {"h": 5, "w": 24, "x": 0, "y": 64}),
    ])
    return {
        "uid": UID, "title": "Telco Ontology-first ML E2E", "tags": ["ask-o11y-preview", "ask-o11y-ml", "telco", "ontology-data-atlas"],
        "timezone": "browser", "schemaVersion": 41, "version": 0, "refresh": "", "panels": panels,
    }


def main() -> int:
    global EXECUTION_REF
    contract = load_module("telco_dashboard_contract", ROOT / "ml_dashboard_contract.py")
    bridge = load_module("telco_dashboard_bridge", ROOT / "artifact-bridge-mcp/server.py")
    try:
        manifest = json.loads((OUTPUT / "ml-presentation.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telco manifest unavailable: {exc}") from exc

    order = [item["name"].removesuffix(".png") for item in manifest["artifacts"]]
    results: list[dict[str, Any]] = []
    indices: dict[str, tuple[int, int]] = {}
    for name in order:
        png = (OUTPUT / f"{name}.png").read_bytes()
        png_index = len(results)
        results.append({"mime": {"image/png": base64.b64encode(png).decode()}, "display_name": f"{name}.png"})
        figure = (OUTPUT / f"ml-plotly-{name}.json").read_text()
        json_index = len(results)
        results.append({"mime": {"application/json": figure}, "display_name": f"ml-plotly-{name}.json"})
        indices[name] = (png_index, json_index)

    context = {"org_id": "1", "user_id": "telco-dashboard-e2e"}
    artifacts = bridge.ArtifactStore(ROOT / ".analysis-artifacts/runs")
    setattr(bridge, "ARTIFACTS", artifacts)
    run_id = artifacts.create_run(context)
    EXECUTION_REF = artifacts.write_json(context, run_id, "sandbox-execution", {"results": results, "error": None})

    dashboard = build_dashboard(manifest, indices)
    contract.validate_preview_dashboard(dashboard, manifest)
    contract.validate_ml_dashboard_minimum(dashboard)
    resolved = bridge.resolve_dashboard_refs({"dashboard": dashboard, "_server_context": context})
    if not resolved.get("ok"):
        raise RuntimeError(f"dashboard binding resolution failed: {resolved}")
    resolved_dashboard = resolved["dashboard"]
    OUTPUT.joinpath("telco-dashboard-raw.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2))
    OUTPUT.joinpath("telco-dashboard-resolved.json").write_text(json.dumps(resolved_dashboard, ensure_ascii=False, indent=2))

    written = http_json("/api/dashboards/db", method="POST", body={"dashboard": resolved_dashboard, "overwrite": True, "message": "Telco ontology ML E2E"})
    fetched = http_json(f"/api/dashboards/uid/{UID}")
    stored = fetched["dashboard"]
    row_titles = [panel.get("title") for panel in stored["panels"] if panel.get("type") == "row"]
    expected_rows = ["1. 決策摘要", "2. 資料地圖", "3. 模型歸因", "4. 模型證據與技術細節", "5. 部署決策"]
    if row_titles != expected_rows:
        raise RuntimeError(f"stored dashboard row order mismatch: {row_titles}")
    flattened = []
    for panel in stored["panels"]:
        flattened.append(panel)
        flattened.extend(panel.get("panels") or [])
    plotly_count = sum(panel.get("type") == "asko11y-plotly-panel" for panel in flattened)
    if plotly_count != 8:
        raise RuntimeError(f"stored dashboard Plotly count mismatch: {plotly_count}")
    serialized = json.dumps(stored)
    if "$asset_url_" in serialized or "$plotly_" in serialized:
        raise RuntimeError("stored dashboard contains unresolved placeholders")
    if "127.0.0.1:8777/assets/" not in serialized:
        raise RuntimeError("stored dashboard contains no signed artifact URLs")

    fallback_url = next(str((panel.get("options") or {}).get("fallbackUrl")) for panel in flattened if panel.get("type") == "asko11y-plotly-panel")
    fallback_uid = "telco-plotly-fallback-e2e"
    fallback_dashboard = {
        "uid": fallback_uid, "title": "Telco Plotly PNG Fallback E2E", "tags": ["telco", "plotly-fallback-e2e"],
        "schemaVersion": 41, "version": 0,
        "panels": [{
            "id": 1, "type": "asko11y-plotly-panel", "title": "缺少 figure 時顯示 PNG fallback",
            "gridPos": {"h": 14, "w": 24, "x": 0, "y": 0},
            "options": {"fallbackUrl": fallback_url, "alt": "Telco fallback 圖", "caption": "figure 缺失時安全降級為同一份 PNG 證據。"},
        }],
    }
    http_json("/api/dashboards/db", method="POST", body={"dashboard": fallback_dashboard, "overwrite": True, "message": "Plotly fallback E2E"})

    summary = {"uid": UID, "fallback_uid": fallback_uid, "url": written.get("url"), "rows": row_titles, "plotly_panels": plotly_count, "resolved_assets": resolved["evidence"]["resolved_assets"], "resolved_plotly": resolved["evidence"]["resolved_plotly"]}
    OUTPUT.joinpath("telco-dashboard-readback.json").write_text(json.dumps(fetched, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
