# Plotly-first ML 圖表 + PNG Fallback 設計

日期：2026-08-26；修訂：2026-09-06。狀態：歷史 Plotly-first 路徑已有 E2E；capability-driven report manifest 與新 image/plotly 邊界已依 TDD 實作，localhost/browser 手測待使用者驗收。

現行規範以 [平台架構修訂](natural-language-analysis-platform.md) NLAP-07 為準：PNG-only 也必須在 plugin 內以明確 image mode 呈現，禁止 Text-image 路徑與靜默降級。下方既有 fallback 行為及 E2E 為歷史實作紀錄；新契約已實作，localhost/browser 驗收仍待執行。
依據：[Plotly plugin 研究](./ml-grafana-plotly-plugin-research.md)；報告編排見 [Generic LLM Report Synthesis](./ml-llm-report-synthesis.md)。本輪實作待辦：`TODO-9ae85041` 及其子項。

> Sandbox 可產生 bounded Plotly capabilities；最終選擇哪些圖、順序、寬度與 narrative 由整份報告 LLM synthesis 決定，不固定十四圖 Dashboard。
>
> **2026-09-06 capability-driven 修訂**：Host 先由成功 execution 的明確 report source 建立 `report-manifest-v1`，並回傳 opaque `report_manifest_ref`。合法 sanitized figure 使用 `plotly` mode；只有沒有 figure 且 PNG 合法時使用明確 `image` mode；figure 存在但無效時 fail closed，絕不以 PNG 靜默降級。Plotly mode 不要求 PNG fallback。模型不可猜 `manifest_output_index`；舊 trusted execution 必須先由 host `reexport_trusted_report` 產生 fresh canonical ref，Bridge 不接受手動 legacy index。

## 目標

所有分析產出圖片均由 **asko11y-plotly-panel** 呈現，不固定圖表數量。Sanitized figure 使用 plotly mode，PNG-only 使用 image mode；保留原圖、敘述與 evidence，不偽造互動能力。Plugin 未安裝或 figure 無效時明示錯誤，不繞過至 Text panel 或靜默降級。唯一讀／唯一算／唯一寫邊界不變。

## 架構

```text
Sandbox（唯一計算）
  ml_presentation.build_plotly_figures()
    同一份 aggregate → sanitize 後的 figure JSON（ml-plotly-*.json）
  PNG 可作明確 image capability（不得冒充 Plotly fallback）
       ↓ opaque execution_ref → Host-owned report_manifest_ref
Artifact Bridge
  askO11yPlotlyBindings：驗證 plugin_id + sanitize_figure
  注入 static {data, layout, config}
  askO11yAssetBindings：解析 image mode 的 PNG URL，或同一 artifact 已驗證的 Plotly fallback
       ↓
asko11y-plotly-panel（自製、無 eval）
  plotly mode → Plotly.react；image mode → <img>
  invalid figure → 明確錯誤，不靜默降級
       ↓
mcp-grafana（唯一 writer）→ Preview → 同 UID publish
```

## 契約（ml_plotly_contract.py）

- plugin pin：`asko11y-plotly-panel`（唯一允許的 panel type / plugin_id）。
- 允許 trace：`bar`、`box`（可選 `boxmean: true|false|"sd"`）、`candlestick`、`contour`、`funnel`、`funnelarea`、`histogram`、`histogram2d`、`histogram2dcontour`、`heatmap`、`indicator`、`ohlc`、`pie`、`sankey`、`scatter`、`scattergl`、`sunburst`、`treemap`、`violin`、`waterfall`。
- 禁止（遞迴）：`frames`、`transforms`、`customdata`、`ids`、`meta`、`hovertemplate`、`texttemplate`、`images`、`template`、`updatemenus`、`sliders`、`href`、`src`、`base64`、`script`、`onclick`、`callback`。
- 字串：≤200 字元、禁 `<`、`>`、`javascript:`、`http` 開頭。
- 數字：finite、非 bool。
- 上限：每 figure JSON ≤64 KiB；每執行 ≤14 figures；每 trace 陣列 ≤2,000 點；每 figure 總點數 ≤6,000；heatmap z ≤100×100。
- `config` 由 host 固定：`{displaylogo:false, responsive:true}`；sanitizer idempotent，Bridge 只接受完全相同的 fixed config。
- small-multiples layout 只允許 `xaxis/yaxis` 1..12；`axis13` 與其他 layout key fail closed。

## Dashboard contract 修訂（已實作）

- 所有圖片 artifact panel 必須使用 pinned plugin；Text 僅可敘述，禁止 HTML/CSS 圖片繞過。
- Image mode 只需受信任 PNG binding 與完整 narrative/view evidence，不要求不存在的 Plotly binding。
- Plotly mode 仍必須有 sanitized figure binding；非法 figure 要明示錯誤。更新 figure 後可重新渲染，不沿用舊 failure state。
- Render mode 只能由 Host 驗證後的 artifact MIME/capability 決定，不由 dataset、model、欄名或圖表數量推導。
- 單 view image mode 也必須顯示 view narrative，不能因去重而遺失全部說明。
- Compositor、Bridge、validator、host writer/prompt 與 browser checks 必須一起遷移；舊 dashboard 重用 artifacts，不重算。

以下為舊版 contract，僅供遷移比對；衝突處由上述修訂取代：

- ML dashboard 允許的 panel type 新增 pinned `asko11y-plotly-panel`。
- Plotly panel 必須：≥1 `askO11yPlotlyBindings`（placeholder 形如 `$plotly_*`）＋ `alt` ＋ caption；PNG binding 可是明確 image mode，或同一 artifact 的已驗證 Plotly fallback。
- 仍禁止 `targets`、`script`、`onclick`、literal figure data。
- image evidence 計數將含 fallback 的 plotly panel。

## Host-owned report manifest

- Sandbox 成功結果若包含報告 source，Host 依 `report-source-v1` 建立不可由模型猜 index 的 `report-manifest-v1`，並在 `refs.report_manifest_ref` 回傳 opaque ref。
- Manifest 只保存 bounded purpose/conclusion/facts、artifact id 與 Host-derived render capability（`plotly` 或 `image`）；不向模型暴露 output index、physical path、raw rows 或 MIME body。
- 每個 artifact 先檢查 Plotly：合法 figure 直接選 `plotly`；沒有 figure 才檢查 PNG；figure 無效時拒絕整個 artifact，即使同時有合法 PNG。
- 舊 `execution_ref + manifest_output_index` 不再由 Bridge 直接讀取；需要相容既有 trusted 產物時，Sandbox host 只可在 server-owned provenance、executor 與完整輸出驗證後 re-export 成 fresh `report-manifest-v1`。generic Python、普通 summary、手動輸出不可 re-export。

## Bridge 變更

- 新 binding 形狀：`{placeholder, $execution_ref, output_index, plugin_id}`。
- 只讀取 `report_manifest_ref` 與 artifact id；legacy caller 不可提供 execution index。所有 report context、artifact binding 與 dashboard binding 都需 fresh server-owned refs。
- Plotly binding 讀取 `application/vnd.plotly.v1+json` → `sanitize_figure` → 以 figure dict 取代 panel placeholder；不要求 PNG binding。
- Image binding 僅讀取已驗證 `image/png`；產生 `renderMode: "image"` 與受信任 URL。
- 任何 sanitize／PNG 驗證失敗 → recoverable error（修正 binding，不重跑分析），不得改用另一種 mode。

## Grafana panel（grafana-panels/asko11y-plotly-panel）

- React + plotly.js-dist-min（bundle 進 module.js；React、`@grafana/data`、`@grafana/ui` 為 externals）。
- 復用 nLine panel 已驗證的 `useTheme2`／theme merge／resize 思路，但不引入其 Processing Script、event script 或 `new Function`。
- `resolveRenderMode()`：Host 已驗證的 `plotly` mode 使用 figure；Host 已驗證的 `image` mode 使用 PNG；figure 無效顯示明確錯誤，不由 plugin 靜默改 mode。正常模式以 transparent background、Grafana font/grid/text tokens 渲染。
- `ResizeObserver` + `Plotly.Plots.resize()`；figure 不寫死 width/height。多子圖使用一到十二格 responsive x/y domains。
- 同 panel 呈現 LLM 的 observation、interpretation、cross-chart context、limitation、next step 與 deterministic evidence chips。
- 禁止 `new Function`／eval／datasource target／動態 script（CI grep 斷言）。
- 本地以 unsigned allowlist 安裝；production 需簽章。

## TDD 驗收

| 檢查 | seam |
| --- | --- |
| `check-ml-plotly-contract.py` | sanitizer 正反向、上限、finite、禁鍵 |
| `check-ml-plotly-presentation.py` | 14 圖全數產生 PNG + sanitized figure；deterministic |
| `check-artifact-bridge-plotly.py` | report ref、Plotly-only、PNG-only、binding 注入／拒絕 script、錯 plugin、literal data、invalid figure fail closed |
| `check-ml-dashboard-contract.py` / write-gate | 新 panel type + fallback 規則 |
| report-manifest contract | `report-source-v1` → Host `report-manifest-v1`，ref/authorization/bounds/invalid figure fail closed |
| plugin unit test | render-mode fallback 邏輯、dist 無 `new Function` |
| E2E | 真實 artifact → report ref → bridge resolve → Grafana API 建立 Preview → 瀏覽器截圖 Plotly/image mode、錯誤後更新與無 console error |

## E2E 結果

- 實際安裝 unsigned `asko11y-plotly-panel`，Grafana 13.1.2 註冊成功。
- Telco：14 PNG + 14 Plotly；Bridge 解析 14 asset bindings + 8 Plotly bindings，Grafana API 寫入/讀回成功。
- Chromium：Plotly toolbar/graphics 實際出現；collapsed model-evidence row 可展開；figure 缺失時 PNG fallback 實際渲染；無 console/page error。
- 證據：`.scratch/telco-data-atlas-e2e/dashboard-*.png`。

## 非目標

- 不改 ML 演算法／校正／門檻；Plotly 僅檢視既有 aggregate。
- 不支援 datasource targets 進分析 dashboard。
- 不做 Plotly 圖的 browser 端計算或 threshold 重選。
