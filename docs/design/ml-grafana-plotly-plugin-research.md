# Ask O11y／Grafana ML 圖表採用 Plotly panel plugin 研究

- 日期：2026-08-26
- 範圍：研究與 PoC 設計；**不實作、不變更執行路徑**。
- 問題：目前 ML 圖表能否、是否應改用 Grafana Plotly panel plugin 互動呈現？

## 結論先行

1. **現在不能直接在既有受控 ML 路徑使用 Plotly panel。** Grafana 雖是 `13.1.2`，Sandbox 也已安裝 Plotly，且 capture 可保留 `application/vnd.plotly.v1+json`；但標準 ML renderer 只產生 PNG、ML dashboard contract 只准 `row`／`text`、Artifact Bridge 只會把 `image/png` 的 opaque binding 轉成 URL。因此 Plotly MIME 目前既不會成為 dashboard asset，也不能通過 ML write gate。【R2】【R3】【R4】【R5】【R6】【R7】【R8】【R9】
2. **不建議現在直接切成全 Plotly（方案 C）。** 最合理的方向是受控的 **B：PNG 證據保留 + 少數技術圖可選 Plotly 探索**；但它應先是可撤回 PoC，PoC 未全數通過前仍採 A（PNG）。
3. **`natel-plotly-panel` 不可用於 Grafana 13。** 官方 catalog 已標示 deprecated、偵測為 Angular，upstream 宣稱支援 Grafana 4–6；Grafana 官方說自 v12 起已無法使用任何 Angular plugin，故在本專案的 13.1.2 上應排除。【G4】【G7】
4. **`ae3e-plotly-panel` 與較新的 `nline-plotlyjs-panel` 都不應被當成「只渲染 JSON」的無害元件。** 兩者 upstream source 都以 `new Function(...)` 執行 panel 的處理／事件腳本。即使 Ask O11y 永不產生 script，具有 dashboard 編輯權的人仍可把 script 寫進 panel options；這是瀏覽器端可執行碼的治理問題，不是單靠 opaque ref 可解決。【G5】【G6】【G8】
5. **最小可行 seam 是「受限 Plotly figure artifact → Bridge 注入靜態 `data/layout/config`」而不是「Grafana DataFrame + panel script」。** 後者會要求 PanelData／datasource target 或 browser script，與既有 Sandbox ML dashboard 的 image/text-only 邊界相衝突；前者可讓 Sandbox 保持唯一計算者、Bridge 只解析 opaque artifact、內建 Grafana MCP 保持唯一 writer。【R1】【R6】【G2】【P1】【P2】

---

## 1. 已核對的現況

### 1.1 現有邊界與實際程式行為

| 已驗證事實 | Primary source | 對 Plotly 的意思 |
| --- | --- | --- |
| `CONTEXT.md` 規定 Grafana Query 是唯一 datasource-read executor、Sandbox 是分析執行者、Artifact Bridge 不選 panel 不寫 Grafana、內建 `mcp-grafana_update_dashboard` 是唯一 Dashboard writer。Sandbox 分析 dashboard 是 image/text panel + opaque asset binding。 | 【R1】 | 新路徑不能讓 panel 再查原 datasource、不能新增 renderer/writer、也不能讓 Bridge 生成圖表。 |
| 標準 ML `render_assets()` 用 Matplotlib 畫出資料 profile、相關熱圖、每千筆、baseline、health、流程、trials、importance、confusion、ROC/PR、calibration、threshold-cost；`render_shap_summary()` 也產生 PNG。`compose_ml_template()` 呼叫這些 renderer，沒有建立或 emit Plotly figure。 | 【R2】【R4】 | 標準 ML 執行目前**沒有**可供 dashboard 消費的 Plotly figure；「Sandbox 已裝 Plotly」不等於 ML chart 已有 Plotly artifact。 |
| `capture.emit()` 偵測同時有 `to_plotly_json` 與 `to_json` 的物件時，會寫出 `application/vnd.plotly.v1+json`。`read_captured_outputs()` 也把此 MIME 放入 allowlist。 | 【R3】【R4】 | 有一個可重用的**捕捉 seam**；generic Python 中 `display(fig)` 可以讓 figure JSON 留在 execution artifact。這還不是安全／可呈現契約。 |
| `output_asset_summary()` 只把 `image/png` 列為 assets；inline result 只取 text/plain、application/json；download summary 只提供 CSV。`artifact_assets.SUPPORTED_MIME` 也不含 Plotly MIME。 | 【R4】【R5】 | 現有 Plotly MIME 可以被保留，卻不會變成 model-visible image asset、可簽 URL，或 Bridge 可綁定輸出。 |
| Artifact Bridge 的 `resolve_asset_bindings()` 只接受 `askO11yAssetBindings`，並明確要求輸出 MIME 為 `image/png`；ML dashboard 也拒絕同時含 asset 和 datasource target。 | 【R6】 | Bridge 沒有 `askO11yPlotlyBindings`，也沒有「驗證 JSON 後注入 panel options」功能。 |
| `validate_ml_dashboard_minimum()`／`validate_preview_dashboard()` 只允許 `row`、`text`，拒絕任何 `targets`，並要求 opaque image bindings、alt text、可見 caption、Preview tag。 | 【R7】 | 即使 Grafana 已安裝 Plotly plugin，ML panel type 仍會被 contract 擋下。 |
| `compose.yaml` 固定 `grafana/grafana:13.1.2`；安裝的 catalog plugins 僅 Infinity 3.11.2、Matrix 2.0.1；unsigned allowlist 只包含本地 `consensys-asko11y-app`。 | 【R8】 | 沒有 Plotly plugin 安裝／pin；不得為了候選 plugin 擴大 unsigned allowlist。 |
| reuse manifest 顯示 network-denied sandbox image 已釘選 `plotly==6.9.0`。 | 【R9】 | 不需要新增 Python Plotly dependency；前端 Grafana plugin 仍是獨立供應鏈。 |

**直接答案：** 在目前 production contract 中，不能把 `ae3e-plotly-panel`、`nline-plotlyjs-panel` 或任何 Plotly panel 直接放進 ML Preview JSON。若手動安裝 plugin，Grafana 本身也許能認得 panel type；但它仍不會繞過 ML validator、PNG-only Bridge binding、以及標準 ML 沒有 Plotly artifact 這三個閘門。

### 1.2 既有文件／runtime 的小差異

舊的 presentation proposal 寫「最多 8 個 PNG」，但現行 `ml_presentation.validate_manifest()` 的 runtime 上限是 14，P0/P1 設計也說已從 12 調整為 14。PoC 應以 runtime 14 為準，並在接受條件中釘住「logical artifact 數」而非再新增一套不一致上限。【R2】【R10】【R11】

---

## 2. Grafana、候選 plugin 與 Plotly JSON 的 primary-source 查核

### 2.1 Grafana plugin 與 DataFrame 規則

Grafana 官方說 plugin 在啟動時驗證簽章；unsigned plugin 預設不載入，Grafana Cloud 也不支援 unsigned plugin。`plugin.json.dependencies.grafanaDependency` 是 npm semver 範圍，舊的 `grafanaVersion` 欄位已 deprecated。【G1】【G3】

Grafana 的 panel 資料管線是：`Data source query → Transformations → Field overrides → Panel plugin`；panel 收到的是已轉換的 `PanelData`，不能取得未轉換資料，且官方要求 panel 能處理不同 DataFrame 結構。【G2】這對一般 datasource dashboard 正確，但不適合本案直接接上 Sandbox ML：分析完成後若再以 `targets` 餵 panel，就會從 Grafana 走另一個 query／PanelData 路徑，與現有「Sandbox 被呼叫的 dashboard 不准 targets」契約衝突。【R1】【R6】【R7】

Plotly 官方定義 `Plotly.newPlot(graphDiv, data, layout, config)`，也接受含 `data`、`layout`、`config`、`frames` 的單一物件；Python `Figure` 可表示為 dict／graph object 並序列化為 JSON。【P1】【P2】因此，**靜態受限的 figure JSON** 足以讓 panel render，不必用 Grafana DataFrame 或 user-defined JavaScript 做轉換。

### 2.2 候選比較

| 候選 | catalog／維護狀態 | Grafana 13.1.2 相容性結論 | 接收資料／設定方式 | custom JS／eval | 決定 |
| --- | --- | --- | --- | --- | --- |
| `ae3e-plotly-panel` | catalog API 仍為 `active`、community-signed、v0.5.0；catalog metadata 更新於 2021-09，upstream latest release 是 2021-08、release note 僅提升至 Grafana 8.0.3。 | `grafanaDependency: >=7.5.5` 沒有上限，機械上不排除 13.1.2；但同時留下 deprecated 的 `grafanaVersion: 7.x`、source package 用 Grafana 8.0.3。**沒有 primary-source 的 13.1.2 實測證據，不能宣稱相容。** | static `data`／`layout`／`config`／`frames` options 可以直接交給 `react-plotly.js`；也可讀 `props.data`，再由 script 轉成 Plotly traces。 | 是。`SimplePanel.tsx` 對處理 script 與 click handler 使用 `new Function`。 | 不選為 production 預設；若要測，只能做隔離相容性比較。 |
| `natel-plotly-panel` | catalog 狀態是 `deprecated`，catalog 明示改用 `nline-plotlyjs-panel`；`angularDetected: true`。upstream README 說支援 Grafana 4、5、6，latest GitHub release 為 2019、稱 tested with Grafana 6。 | **不相容／不可採用。** Grafana 官方說 v12 起無法再用任何 Angular plugin；13.1.2 已在該門檻後。 | 舊式 Angular `MetricsPanelCtrl`／舊 query series mapping，不是本案需要的受控 figure artifact 消費者。 | 未作為可採用目標；另有 `loadFromCDN` 選項可載入 `cdn.plot.ly`，不符合 offline 預設。 | 排除。 |
| `nline-plotlyjs-panel`（Natel catalog 指定的替代名稱） | catalog `active`、community-signed、v1.8.1，2024-09；upstream latest release 同為 v1.8.1。main branch package 是 1.8.2，**不可拿 main branch 取代 catalog v1.8.1**。 | `grafanaDependency: >=9.0.0` 無上限，故 13.1.2 不會被該範圍排除；舊 `grafanaVersion: 9.x` 已 deprecated。發行 source package 依賴 Grafana 11.2.x／React 18，未見 upstream 對 13.1.2 的明確驗證。**僅可列為有條件 PoC 候選。** | `options.data/layout/config/frames` 可直接渲染；也可把 Grafana `PanelData` 經 script 轉換。source 以 bundled `plotly.js-dist-min` 載入 Plotly。 | 是。`useScriptEvaluation.ts` 用 `new Function(...Object.keys(context), script)`；`SimplePanel.tsx` 對 processing script 和 on-event trigger 都提供該能力，並傳入 data、variables、options、utils。 | 最佳的**短期 PoC 候選**，但只允許空 script／空 onclick、嚴格 RBAC，且必須先通過 13.1.2 live test。 |

來源：ae3e catalog／plugin.json／release／source【G5】【G6】；Natel catalog／plugin.json／release／source【G7】；nLine catalog／plugin.json／release／source【G8】；Angular removal【G4】。

### 2.3 簽章、維護與供應鏈判讀

- catalog 的 community signature 是必要門檻，**不是**對版本相容性、任意 script 安全性或長期維護的保證。Grafana 的簽章機制只驗證 plugin package 未被竄改／是否可載入。【G1】
- `natel-plotly-panel` 雖可能仍有 catalog package／簽章資料，但 deprecated + Angular removal 已是硬阻擋；不可因下載量或舊 dashboard 而例外。【G4】【G7】
- `ae3e` 的前端依賴是 Plotly 1.58.4、Grafana 8.0.3 工具鏈；`nline` 比較新，但 published catalog v1.8.1 仍不是 Grafana 13 qualification。兩者都要用真正的 13.1.2 Preview 實測，而非把 semver 下限當兼容證明。【G5】【G6】【G8】
- 安裝應釘住 **plugin id + catalog release version + 已驗證 package hash + 簽章狀態**，以 build-time 預載 Grafana image 的方式供應；不應在 production 啟動時無控制地下載，也不應新增 `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS`。現有 compose 的 unsigned 例外只限本地 app。【R8】【G1】
- Sandbox 的 `network_default_action: deny` 不會自動限制 Grafana server 或瀏覽器的 plugin。PoC 必須用瀏覽器 network log／egress policy 驗證沒有 CDN 或其他外連；Natel source 的 `loadFromCDN` 已證明「Plotly panel」不能預設假設 offline。【R4】【G7】
- 最重要的風險是 browser code：候選 source 的 `new Function` 讓 dashboard panel option 成為可執行 JavaScript。即便 Bridge 只產生空 script，之後的 dashboard 編輯者仍可能寫入 script。若 Grafana Editor 不被視為可執行 browser code 的受信任作者，這些通用 plugin 都不能進 production；應保留 PNG，或另行選／做沒有 eval surface 的專用 panel。這是由 upstream source 導出的安全結論。【G6】【G8】

---

## 3. 逐圖取捨與 bounded data

原則：Sandbox 只輸出已聚合／下採樣的事實，不輸出 raw rows、sample id、完整 holdout probability 或完整 SHAP feature values。Plotly 的 hover／zoom 只是檢視既有事實，**不得**在 browser 或 plugin script 重新計算閾值、選模型、校正器或資料轉換。

| 現有 asset | Plotly 判定 | 最小 bounded payload（由 Sandbox deterministic 產生） | 隱私／大小／解釋 guard | B 的優先度 |
| --- | --- | --- | --- | --- |
| 資料 profile (`data_profile.png`) | **Plotly 原生容易**：bar + histogram。 | target class counts；最多 3 個特徵；數值每特徵最多 25 個 bin `(edge,count)`；類別最多前 8 + `其他`。 | 不傳原始 feature 值；稀有類別先依最小群組門檻合併為「其他」；caption 保留樣本數與資料範圍。 | 中 |
| 相關 heatmap (`correlation_analysis.png`) | **Plotly 原生容易**：`heatmap`。 | 最多 10×10 correlation matrix、欄位標籤、固定 `[-1,1]` 色階。 | 欄位名可能敏感；不輸出原始列；hover 明示「相關非因果」。 | 高 |
| 每 1,000 (`per_1000_outcomes.png`) | **不值得互動**。 | `correct_per_1000`、`errors_per_1000` 兩值即可。 | 最適合保留 PNG + text；互動不會新增決策資訊，仍需文字而非只靠顏色。 | 不做 |
| baseline error (`baseline_error_comparison.png`) | **不值得互動**（若改是原生 bar）。 | baseline／selected error rate、每千筆差值。 | 兩柱資料極小；PNG already 可讀，避免把第一視窗決策資訊藏進 hover。 | 不做 |
| generalization health (`generalization_health.png`) | **不值得互動**；若要做需自訂 transform 成固定 card／indicator。 | gap、importance stability、PSI、各自 threshold、verdict 和一段固定文字。 | 不准由 browser 依顏色／閾值自行判定；必須顯示值、threshold、pass/fail 文字。 | 不做 |
| trial history (`trial_history.png`) | **Plotly 原生容易**：top-5 line/scatter。 | rank 1–5、CV score、objective label、`completed_trials`。 | 明示這是 train/CV，不是 holdout；不輸出完整 tuning history／parameters。 | 中 |
| feature importance (`feature_importance.png`) | **Plotly 原生容易**：horizontal bar。 | 最多 10 個 ontology feature、grouped importance、固定「關聯非因果」caption。 | sensitive feature name 需 policy 核可；不可泄漏 one-hot category／個人值；不提供可反推 training row 的 customdata。 | 中 |
| confusion matrix (`confusion_matrix.png`) | **Plotly 原生容易**：2×2 heatmap。 | TN/FP/FN/TP，選定 threshold，holdout n。 | 顯示 holdout 與 threshold；小群組／極小 n 時只顯示 PNG 或拒絕細分。 | 高 |
| ROC/PR (`roc_pr_curves.png`) | **需自訂 transform**（圖本身是原生 scatter）。 | ROC 與 PR 各自最多 200 個單調下採樣點、端點、positive rate baseline、holdout n。 | 不傳逐筆 probability／label；保留 ROC `[0,0]→[1,1]`、PR 端點與原始排序；caption 明示 curve 不是選門檻依據。 | 高 |
| calibration (`calibration_curve.png`) | **Plotly 原生容易**：10 bin line + ideal diagonal。 | 最多 10 組 `(predicted_rate, observed_rate, bin_count)`、Brier、holdout n、method。 | 只輸出 bin aggregate；保留「train OOF 選、holdout 評估」，不能從 hover 重新選 calibrator。 | 高 |
| threshold-cost (`threshold_cost_curve.png`) | **Plotly 原生容易**：三條 line／雙 y 軸／固定 annotation。 | 101 個既有 threshold 點的 cost、recall、precision；選定 train-OOF threshold、FN:FP 成本、holdout n。 | 選定 threshold 是不可變事實；禁止 browser 找曲線最低點後改寫決策；caption 要說 holdout 僅評估。 | **最高** |
| SHAP summary (`shap_summary.png`) | **需自訂 transform，首輪不做**。 | 若日後核准：最多 10 features × 400 無 ID 點，SHAP 值與**量化後** feature-color bin（不是原始值）。更保守選項是每 feature 的分位數／密度摘要。 | 400 個點仍可能暴露樣本特徵輪廓，且 beeswarm 的顏色／左右含義容易被錯讀；先保留 PNG + LLM caption。 | 低／延後 |
| process diagram (`analysis_process.png`) | **不值得互動**。 | 固定六個完成 stage、不可由 chart 更新。 | 應保留 PNG/text；交互沒有價值，亦不應讓人誤以為可從 dashboard 重跑 pipeline。 | 不做 |

這張表刻意不把每一張 PNG 都轉成 plugin panel。B 的第一輪最多選四個技術圖：**相關 heatmap、ROC/PR、calibration、threshold-cost**；其餘仍是現有 PNG/text。這同時測到 matrix、multi-trace curve、二次 y 軸、敏感的 holdout evidence，卻不讓第一視窗與 SHAP 承擔不必要風險。【R2】【R11】

---

## 4. 三種方案

| 方案 | 內容 | 優點 | 成本／風險 | 結論 |
| --- | --- | --- | --- | --- |
| **A. 維持 PNG** | 維持當前 bounded manifest + PNG + text + opaque image binding。 | 已符合 image/text contract、容易固定證據與 Traditional Chinese caption、沒有 panel script 或 inline data 擴張。 | 沒有 hover/zoom；曲線探索性有限。 | **現在的正確預設。** |
| **B. Hybrid：PNG 證據 + 可選 Plotly 探索** | 同一 logical chart 保留 Sandbox 產生的 canonical PNG；只有選定技術圖另帶受限 Plotly JSON，由 Bridge 注入 allowlisted plugin 的靜態 options。 | 不犧牲 evidence、alt/caption、fallback 或 export；可把 hover/zoom 限在真正有判讀價值的曲線／matrix。 | 需要 JSON sanitizer、Bridge／contract seam、plugin pin、Grafana 13 test、RBAC/script policy。 | **推薦的可撤回 PoC 方向。** 未過 gate 立刻回到 A。 |
| **C. 全 Plotly** | 所有 ML asset 都換 interactive plugin panel，PNG 只剩備援或移除。 | UI 一致、理論上高度探索性。 | 多數決策圖沒有互動價值；擴大資料／隱私、panel script、render/export、accessible caption、plugin upgrade、publish retention 風險；目前 contract 幾乎全部要重寫。 | **不建議。** |

### 推薦的決策規則

- 立刻的 production 答案是 **A**。
- 若產品要驗證 interactivity，核准一個 **B 的有限 PoC**，不是導入承諾。
- `nline-plotlyjs-panel@1.8.1` 是唯一值得先測的 catalog 候選；它不是 Grafana 13 已驗證答案，也不是 security approval。`ae3e` 可作 compatibility 對照但不應成為選定基線；Natel 排除。
- 若「Grafana dashboard Editor 可把 arbitrary JS 跑在 viewer browser」不被安全政策接受，B 的外部 generic-plugin 分支停止，保留 A；不要用 allowlist／提示詞來掩蓋這個產品安全決策。

---

## 5. B 所需的最小架構 seam（不繞過唯一讀／唯一算／唯一寫）

### 5.1 建議資料流

```text
Grafana Query（唯一 datasource read）
  → opaque grafana-frame ref
Sandbox ML（唯一計算；已有原始 frame 的授權）
  → canonical PNG + bounded ml-plotly-figure-v1 artifact
  → opaque execution_ref
Artifact Bridge（只授權讀取、驗證、注入；不重算、不選 panel、不寫）
  → resolved static Plotly panel options + existing PNG bindings
built-in mcp-grafana_update_dashboard（唯一 write）
  → ask-o11y-preview same UID → explicit publish same UID
```

**不採用** `Grafana DataFrame → plugin processing script` 作為 ML artifact 路徑。Grafana 官方的 PanelData pipeline 適合 live datasource panel，但這裡會重開 query/target 或依賴 browser transform；受控 ML 結果已由 Sandbox 算完，應只交付經驗證的靜態 figure options。【G2】【R1】【R6】【R7】

### 5.2 最小變更表

| seam | 現況 | B 所需最小能力 | 邊界保證 |
| --- | --- | --- | --- |
| `ml_presentation.py` | 只產生 Matplotlib PNG。 | 對上述四個 PoC asset 產生**同一個既有 aggregate**的 PNG 與 `ml-plotly-figure-v1`；不讓 LLM 撰寫 Plotly JSON。 | 計算仍只在 Sandbox；PNG 是 canonical evidence。 |
| `capture.py` + Sandbox server | 已能保留一般 `application/vnd.plotly.v1+json`，但只是任意 figure MIME。 | 新增正式、狹窄的 artifact contract，而不是把 generic Plotly MIME 直接暴露：例如 `{format, asset_kind, figure:{data,layout}}`。host 在 persist 前 parse／validate／normalise。 | Sandbox 不被盲信為資料外洩豁免；generic Python 的任意 full figure 不可自動變成 dashboard panel。 |
| bounded sanitizer | 無。 | PoC 只准 `bar`、`box`（含有限 `boxmean`）、`candlestick`、`contour`、`funnel`、`funnelarea`、`histogram`、`histogram2d`、`histogram2dcontour`、`scatter`、`scattergl`、`heatmap`、`indicator`、`ohlc`、`pie`、`sankey`、`sunburst`、`treemap`、`violin`、`waterfall`；固定 host-owned config；不收 `frames`、`transforms`、`customdata`、`ids`、`meta`、`text/hovertemplate`、`layout.images/template/updatemenus/sliders`、URL、HTML、script、onclick；所有數字 finite。 | 不存在 browser-side compute、external resource、任意 HTML/JS 或 raw-row carrier。 |
| size／privacy budget | capture 單一輸出最多 4 MiB、總 execution 5 MiB；Bridge dashboard 最多 384 KiB，兩者差距很大。 | PoC 提議每 figure JSON ≤64 KiB、最多 4 個 interactive figures、合計 ≤256 KiB，另保留 dashboard text；每 asset 套用上表點數／群組上限。超限 fail closed 並只保留 PNG。 | 不把 4 MiB capture 直接寫進 dashboard；避免 dashboard 和 browser 記憶體失控。 |
| `artifact-bridge-mcp/server.py` | 只有 `$asset_url_…`／`askO11yAssetBindings`，且只接 PNG。 | 新增 opaque `askO11yPlotlyBindings`，形狀只含 `plugin_id`、`$execution_ref`、`output_index`、`asset_kind`；Bridge 驗證後才把 figure 的 static `options.data/layout/config` 放進 resolved panel。 | model 不看 raw figure JSON、不能選任意 plugin／config；Bridge 仍不產生 chart／不寫 Grafana。 |
| `ml_dashboard_contract.py` | 只准 `row`、`text`；禁止 targets。 | 只額外 allowlist **一個釘住 plugin id**；interactive ML panel 仍必須 `targets: []`，只可有 opaque Plotly binding，且拒絕 literal data、script、onclick、datasource、URL。 | 不把 target／direct datasource read 偷放回分析 dashboard。 |
| Grafana image／plugin | 沒有 Plotly plugin，且 unsigned allowlist 很窄。 | build-time 預載選定 catalog v1.8.1、驗證簽章／hash；不允許 unsigned；做 13.1.2 start/render smoke。 | frontend supply chain 明確可追溯；不擴大載入政策。 |
| Preview／publish／fallback | Preview 以 same UID lifecycle；PNG 使用短期 signed asset URL。 | 每個 Plotly logical asset 留一張 PNG/text panel；plugin render error、plugin 未安裝或 JSON 被拒時，PNG 仍可讀。browser export 只屬便利功能，不能取代 Sandbox PNG／provenance。 | Preview/publish 不重跑 query 或 ML；故障時不失去證據。 |

### 5.3 Publish retention 是先決開放問題

現行 Bridge 的 asset URL 有 expiry，`artifact_assets.py` 的讀取 MIME allowlist 也只含 PNG／CSV／HTML／text／JSON；另一方面 presentation design 的目標是 published dashboard freeze run assets。B 不能假設「interactive JSON 內嵌」自動解決既有 PNG URL 的 retention 差異。PoC 可以只驗證 Preview；若要把 B 納入正式 publish acceptance，產品需先定義／驗證 published PNG 和 figure artifact 的不可變保存策略。【R5】【R6】【R10】

---

## 6. 最小 PoC acceptance matrix

以下是**要執行的驗收**，不是本研究已執行的測試。

| gate | 通過條件 | 失敗時的處置 |
| --- | --- | --- |
| Plugin provenance | Grafana 13.1.2 啟動後顯示選定 plugin 的 catalog 簽章有效；id/version/hash 都等於核准 pin；不新增 unsigned allowlist。 | 停止 B，保留 A。 |
| Grafana 13 render | 在相同 `grafana/grafana:13.1.2` image，用 nLine catalog v1.8.1 render 四個 fixture（heatmap、ROC/PR、calibration、threshold-cost），無 console exception／panel error。 | 不宣稱 semver compatibility；回到 A 或重新選 plugin。 |
| No arbitrary script | dashboard contract／Bridge fixture 必須拒絕非空 `script`、`onclick`、未知 plugin id、literal figure JSON、`targets`、datasource、URL、HTML，且拒絕輸出中的 disallowed Plotly key。 | 視為 security blocker。 |
| Opaque binding | model-authored dashboard 只含 binding ref；Bridge 解出 figure 後 dashboard 才有 static options。驗證 model-visible payload、pre-Bridge dashboard、persisted Preview 都不含 raw rows、frame、credentials、paths、signed URLs 或 sample IDs。 | 修 sanitizer／Bridge；不得以 prompt workaround 放行。 |
| Single read/calculate/write | trace 顯示只由 Grafana Query 讀 datasource 一次、Sandbox 計算一次、Bridge 無 query／compute、built-in MCP 寫 Preview 一次；publish 用同 UID 且不重跑。 | 視為 architecture regression。 |
| Size／privacy | 接受上表最大 payload；每一種最大值 + 1 都 fail closed；SHAP raw sample fixture、rare-category fixture、NaN/Inf、URL、`customdata` fixture 都被拒絕或退回 PNG。 | 不擴張上限；修 boundary validator。 |
| Evidence／fallback | 每個 interactive chart 旁保留相同 run 的 PNG、Traditional Chinese caption、alt/caption、holdout/CV 身分；停用 plugin 或餵無效 JSON 時 PNG 仍可用。 | 不允許 Plotly-only 發布。 |
| Decision integrity | threshold-cost 的 selected threshold 僅等於 train-OOF fact；改動曲線 hover／zoom／export 不改 manifest、verdict、operational status。 | 視為 ML governance blocker。 |
| Offline/egress | Sandbox 維持 deny；Grafana/browser 觀測不到 CDN／未知外連；不使用 Natel 的 CDN path。 | 封鎖 release，補 deployment network policy 或回到 A。 |
| Publish retention | 明確測試 Preview URL expiry 與正式 publish 後 PNG／figure 的保存、授權、撤銷行為。 | B 僅限 Preview PoC，不宣稱正式 publish 支援。 |

---

## 7. 對 scope、architecture、acceptance、validation 的影響

- **Scope：** B 第一輪只加四種 technical figure；不重做 13 個 chart、不加入 dashboard generator、不新增 MCP/writer、不改 ML 演算法／calibration／threshold selection。
- **Architecture：** 重用既有 Sandbox capture、ArtifactStore、Artifact Bridge、built-in Grafana writer；新增的是受限 figure artifact 和其 resolver，而不是另一條 datasource 或 renderer 路徑。
- **Acceptance：** 成功定義不是「圖能動」；必須同時滿足 signed pin、Grafana 13 render、no-eval binding、bounded data、PNG fallback、same-UID Preview/publish 與唯一 read/calculate/write。
- **Validation：** 需要 parser/sanitizer 的正反向 unit tests、Bridge contract tests、Grafana 13.1.2 compose integration test、瀏覽器 console/network test，以及人工判讀 Traditional Chinese caption 和 fallback。不能只靠 plugin catalog 顯示可安裝。

## 8. 開放問題／需產品或安全決策

1. Grafana Dashboard Editor 是否被明確授權可讓任意 viewer browser 執行 JavaScript？若否，所有含 `new Function` 的外部候選都不應 production 導入。
2. B 的可接受群組最小樣本數、敏感 feature 名稱、category 合併政策、SHAP 點級資料政策是什麼？沒有它們就不應讓 profile／SHAP 帶更多 tooltip data。
3. 正式 publish 時，PNG 與 figure artifact 的保留期、撤銷、權限變更後的呈現行為是什麼？現有短期 asset URL 與「freeze published asset」的設計目標需要收斂。
4. PoC 的選定 plugin 是否可在實際 Grafana 13.1.2 image 通過簽章、CSP、React/runtime、export 與 browser egress test？這不能由 `>=9.0.0`／`>=7.5.5` 推論。
5. 若 nLine 的 script surface 不可接受，是否接受後續建立／採購一個**無 script、只接受受限 static figure JSON**的專用 panel？這不屬本次 PoC scope。

---

## 來源索引（均為 primary source）

### Repository

- 【R1】[`CONTEXT.md`](../../CONTEXT.md)
- 【R2】[`sandbox-analysis-mcp/ml_presentation.py`](../../sandbox-analysis-mcp/ml_presentation.py)（`render_assets`、`render_shap_summary`、`validate_manifest`）
- 【R3】[`sandbox-analysis-mcp/capture.py`](../../sandbox-analysis-mcp/capture.py)（`emit`、`MAX_ITEM_BYTES`）
- 【R4】[`sandbox-analysis-mcp/server.py`](../../sandbox-analysis-mcp/server.py)（`read_captured_outputs`、`output_asset_summary`、`output_download_summary`、`compose_ml_template`）
- 【R5】[`artifact_assets.py`](../../artifact_assets.py)（`SUPPORTED_MIME`、signed output）
- 【R6】[`artifact-bridge-mcp/server.py`](../../artifact-bridge-mcp/server.py)（`resolve_asset_bindings`、`resolve_dashboard_refs`、`MAX_DASHBOARD_BYTES`）
- 【R7】[`ml_dashboard_contract.py`](../../ml_dashboard_contract.py)（ML allowed panel types、opaque binding／target gate）
- 【R8】[`compose.yaml`](../../compose.yaml)
- 【R9】[`docs/third-party-reuse-manifest.json`](../third-party-reuse-manifest.json)
- 【R10】[`docs/design/ml-grafana-presentation.md`](./ml-grafana-presentation.md)
- 【R11】[`docs/design/ml-p0-p1-calibration-catboost.md`](./ml-p0-p1-calibration-catboost.md)

### Grafana official docs／catalog／upstream

- 【G1】Grafana, [Plugin signatures](https://grafana.com/docs/grafana/latest/administration/plugin-management/plugin-sign/)
- 【G2】Grafana, [Work with transformations in panel plugins](https://grafana.com/developers/plugin-tools/how-to-guides/panel-plugins/work-with-transformations)
- 【G3】Grafana, [`plugin.json` reference](https://grafana.com/developers/plugin-tools/reference/plugin-json)
- 【G4】Grafana, [Removal of Angular in v12](https://grafana.com/docs/grafana/latest/whatsnew/whats-new-in-v12-0/#removal-of-angular)
- 【G5】Grafana catalog API, [`ae3e-plotly-panel`](https://grafana.com/api/plugins/ae3e-plotly-panel)
- 【G6】ae3e upstream: [`plugin.json`](https://raw.githubusercontent.com/ae3e/ae3e-plotly-panel/master/src/plugin.json), [`SimplePanel.tsx`](https://raw.githubusercontent.com/ae3e/ae3e-plotly-panel/master/src/SimplePanel.tsx), [v0.5.0 release](https://api.github.com/repos/ae3e/ae3e-plotly-panel/releases/latest), [`package.json`](https://raw.githubusercontent.com/ae3e/ae3e-plotly-panel/master/package.json)
- 【G7】Grafana catalog API, [`natel-plotly-panel`](https://grafana.com/api/plugins/natel-plotly-panel); Natel upstream: [`plugin.json`](https://raw.githubusercontent.com/NatelEnergy/grafana-plotly-panel/master/src/plugin.json), [`module.ts`](https://raw.githubusercontent.com/NatelEnergy/grafana-plotly-panel/master/src/module.ts), [`libLoader.ts`](https://raw.githubusercontent.com/NatelEnergy/grafana-plotly-panel/master/src/libLoader.ts), [latest release](https://api.github.com/repos/NatelEnergy/grafana-plotly-panel/releases/latest)
- 【G8】Grafana catalog API, [`nline-plotlyjs-panel`](https://grafana.com/api/plugins/nline-plotlyjs-panel); nLine upstream: [`plugin.json`](https://raw.githubusercontent.com/nline/nline-plotlyjs-panel/main/src/plugin.json), [`SimplePanel.tsx`](https://raw.githubusercontent.com/nline/nline-plotlyjs-panel/main/src/SimplePanel.tsx), [`useScriptEvaluation.ts`](https://raw.githubusercontent.com/nline/nline-plotlyjs-panel/main/src/useScriptEvaluation.ts), [`useChartConfig.ts`](https://raw.githubusercontent.com/nline/nline-plotlyjs-panel/main/src/useChartConfig.ts), [v1.8.1 release](https://api.github.com/repos/nline/nline-plotlyjs-panel/releases/latest)

### Plotly official docs

- 【P1】Plotly, [Creating and updating figures in Python](https://plotly.com/python/creating-and-updating-figures/)
- 【P2】Plotly, [Plotly.js function reference](https://plotly.com/javascript/plotlyjs-function-reference/)

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "僅建立本研究 Markdown artifact；未修改 repo runtime、compose、contract 或 plugin 設定。"
    },
    {
      "id": "criterion-2",
      "status": "satisfied",
      "evidence": "文件逐項引用指定 repo primary sources、Grafana 官方 docs/catalog、候選 upstream plugin.json/source/releases 與 Plotly 官方 docs，並提供現況證據、三方案、seam、逐圖資料界線與 PoC matrix。"
    }
  ],
  "changedFiles": [
    "docs/design/ml-grafana-plotly-plugin-research.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "未執行 shell／測試；本任務為 research-only，未實作 PoC",
      "result": "not-run",
      "summary": "已進行指定檔案閱讀、官方 primary-source fetch 與 load-bearing claim source checks；沒有可驗收的程式變更。"
    }
  ],
  "validationOutput": [
    "確認目前 Grafana 為 13.1.2，ML contract 為 row/text + PNG opaque binding。",
    "確認 capture 可保留 Plotly MIME，但 server/asset bridge/dashboard contract 尚不能把它呈現為 ML panel。",
    "確認 Natel 為 deprecated Angular plugin；ae3e 與 nLine upstream 都有 new Function script surface。"
  ],
  "residualRisks": [
    "未做 Grafana 13.1.2 live plugin render／signature／CSP／browser-egress PoC。",
    "nLine 與 ae3e 的 dashboard-editable JavaScript surface 需安全／RBAC 決策。",
    "正式 publish 的 PNG／figure artifact retention 尚需獨立驗證。"
  ],
  "noStagedFiles": true,
  "diffSummary": "只產出研究文件；無 runtime 或設定檔變更。",
  "reviewFindings": [
    "review required：不得將 B 視為已核准導入；必須先通過本文最小 PoC acceptance matrix。"
  ],
  "manualNotes": "未使用 git add；本執行環境未提供 shell，無法另行列印 git index，但本 agent 只寫入此 runtime artifact。"
}
```
