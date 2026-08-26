# Plotly-first ML 圖表 + PNG Fallback 設計

日期：2026-08-26　狀態：approved（TDD 一步到位實作）
依據：[Plotly plugin 研究](./ml-grafana-plotly-plugin-research.md)

## 目標

ML Preview 的 13 張圖全部改為 **Plotly 主呈現**；PNG 不再並排顯示，降級為 **自動 fallback**（plugin 錯誤、未安裝、列印、稽核）。不繞過唯一讀／唯一算／唯一寫。

## 架構

```text
Sandbox（唯一計算）
  ml_presentation.build_plotly_figures()
    同一份 aggregate → sanitize 後的 figure JSON（ml-plotly-*.json）
  PNG 仍產出（fallback / 稽核）
       ↓ opaque execution_ref
Artifact Bridge
  askO11yPlotlyBindings：驗證 plugin_id + sanitize_figure
  注入 static {data, layout, config}
  askO11yAssetBindings：解析 fallback PNG URL
       ↓
asko11y-plotly-panel（自製、無 eval）
  try Plotly.react → 成功顯示互動圖
  catch / figure 缺失 → <img fallbackUrl>
       ↓
mcp-grafana（唯一 writer）→ Preview → 同 UID publish
```

## 契約（ml_plotly_contract.py）

- plugin pin：`asko11y-plotly-panel`（唯一允許的 panel type / plugin_id）。
- 允許 trace：`bar`、`scatter`、`heatmap`、`indicator`、`sankey`。
- 禁止（遞迴）：`frames`、`transforms`、`customdata`、`ids`、`meta`、`hovertemplate`、`texttemplate`、`images`、`template`、`updatemenus`、`sliders`、`href`、`src`、`base64`、`script`、`onclick`、`callback`。
- 字串：≤200 字元、禁 `<`、`>`、`javascript:`、`http` 開頭。
- 數字：finite、非 bool。
- 上限：每 figure JSON ≤64 KiB；每執行 ≤14 figures；每 trace 陣列 ≤2,000 點；每 figure 總點數 ≤6,000；heatmap z ≤100×100。
- `config` 由 host 固定：`{displaylogo:false, responsive:true}`。

## Dashboard contract 變更

- ML dashboard 允許的 panel type 新增 pinned `asko11y-plotly-panel`。
- Plotly panel 必須：≥1 `askO11yPlotlyBindings`（placeholder 形如 `$plotly_*`）＋ fallback PNG binding（`fallbackUrl` 用 `$asset_url_*`）＋ `alt` ＋ caption。
- 仍禁止 `targets`、`script`、`onclick`、literal figure data。
- image evidence 計數將含 fallback 的 plotly panel。

## Bridge 變更

- 新 binding 形狀：`{placeholder, $execution_ref, output_index, plugin_id}`。
- 讀取 execution 輸出（`application/json` 或 plotly MIME）→ `sanitize_figure` → 以 figure dict 取代 panel 內 placeholder 字串。
- 任何 sanitize 失敗 → recoverable error（revise bindings，不重跑分析）。

## Grafana panel（grafana-panels/asko11y-plotly-panel）

- React + plotly.js-dist-min（bundle 進 module.js；@grafana/react 為 externals）。
- `resolveRenderMode()`：figure 無效或 render 丟例外 → fallback `<img>`。
- 禁止 `new Function`／eval／datasource target／動態 script（CI grep 斷言）。
- 本地以 unsigned allowlist 安裝；production 需簽章。

## TDD 驗收

| 檢查 | seam |
| --- | --- |
| `check-ml-plotly-contract.py` | sanitizer 正反向、上限、finite、禁鍵 |
| `check-ml-plotly-presentation.py` | 13 圖全數產生 PNG + sanitized figure；deterministic |
| `check-artifact-bridge-plotly.py` | binding 注入／拒絕 script、錯 plugin、literal data |
| `check-ml-dashboard-contract.py` / write-gate | 新 panel type + fallback 規則 |
| image rebuild | sandbox 內全部 ML checks 綠 |
| plugin unit test | render-mode fallback 邏輯、dist 無 `new Function` |
| E2E | 真實 artifact → bridge resolve → Grafana API 建立 Preview → 瀏覽器截圖 Plotly 渲染、fallback 邏輯、無 console error |

## 非目標

- 不改 ML 演算法／校正／門檻；Plotly 僅檢視既有 aggregate。
- 不支援 datasource targets 進分析 dashboard。
- 不做 Plotly 圖的 browser 端計算或 threshold 重選。
