# ML Ontology Data Atlas — 先看資料，再看模型

狀態：implemented（TDD + Telco E2E 綠燈）

## 背景與問題

現行 ML contract（`sandbox-analysis-mcp/ml_presentation.py`）產出 14 張 PNG artifact，
其中約 57% 是模型能力圖（per_1000、baseline、trial_history、confusion、ROC/PR、
calibration、threshold_cost），僅 2 張涉及資料理解（`data_profile`、`correlation_analysis`）。
更具體的缺陷：

1. **裝飾性圖表**：`analysis_process.png`（六步驟勾勾流程）與 `trial_history.png`
   （前五名近乎相同的 CV 折線）不承載任何分析資訊。
2. **重複呈現**：`per_1000_outcomes.png` 的兩個數字已完整存在於 caption 與文字面板。
3. **任意取樣的相關熱圖**：`correlation_analysis.png` 以「變異數前 10」挑欄，
   沒有語義依據。
4. **Guard 只給結論不給原料**：PSI / stability / gap 卡片宣稱通過，但使用者從頭到尾
   看不到任何一條實際分布，無法檢驗。
5. **Plotly 鏡射不同步風險**：`build_plotly_figures` 仍為已決定刪除的圖保留 figure。

## 目標

以 ontology 欄位元資料（`semantic_kind` / `unit` / `analysis_role` / reason）為分組軸，
在模型結果之前呈現「資料的形狀、集中性、相關性」的通盤掌握，讓敘事從
「模型很強請相信我」改為「治理先行 → 資料自證 → 模型量化訊號 → 決策有據」。

非目標：

- 不做特徵×目標分箱正類率曲線（另案）。
- 不做誤差切片分析（另案）。
- 不更動 OOF calibration / threshold selector / multi-model 比較邏輯。
- 不改 dashboard write-gate 的面板型別限制（仍為 row/text/image）。

## 設計

### 1. 圖表增刪（artifact 總數守恆）

| 動作 | 圖 | 理由 |
| --- | --- | --- |
| 刪除 | `analysis_process.png` | 純裝飾 checklist，文字面板即可 |
| 刪除 | `trial_history.png` | 前五名 CV 折線零資訊量 |
| 合併 | `per_1000_outcomes.png` → `baseline_error_comparison.png` | 兩列堆疊 bar 同時呈現舊/新模型每千筆判對/判錯 |
| 取代 | `correlation_analysis.png` → `semantic_correlation.png` | 以 semantic_kind+unit 分組排序的塊狀 Spearman 熱圖 |
| 新增 | `ontology_field_map.png` | 一列一欄位，依 semantic_kind 上色，標註缺失率與集中度旗標；展示排除欄位與理由 |
| 新增 | `distribution_small_multiples.png` | 核可特徵分布小 multiples（數值直方圖／類別 top-8 長條），按語義群排列 |

單模型主路徑 artifact 數：刪 4 加 3 後為 12 張（含 SHAP），低於既有 bound 14，
`validate_manifest` 的 artifacts 上限不動。

### 2. `build_data_atlas(frame, *, target, fields_view, max_fields=24)`

輸入 `fields_view` 為正規化後的欄位元資料列表：
`{"name", "semantic_kind"?, "unit"?, "analysis_role"?}`；
上傳資料集沒有 ontology 時允許空列表（全部降級為 `unregistered`）。

每個欄位（目標除外）計算：

- **形狀**：`missing_rate`；數值欄加 `skew`、`q1/median/q3`、IQR 外離群占比。
- **集中性**：數值欄 `cv`（std/mean）；類別欄 `distinct` 與 top-3 占比。
- **旗標**：`high_missing`（>0.4）、`low_variance`（數值 cv<0.02 或類別 top 占比>0.99）。

輸出（寫入 `manifest["data_atlas"]`，validator 加入 known_sections 並設界：
fields ≤ 24、warnings ≤ 8、相關矩陣 ≤ 12 欄）：

```json
{
  "fields": [{"name", "semantic_kind", "unit", "role", "missing_rate",
               "numeric": {...}|null, "categorical": {...}|null, "flag"}],
  "correlation": {"columns": [...], "matrix": [[...]]},
  "warnings": ["high_missing:foo", "low_variance:bar"]
}
```

所有浮點數 round(4)；統計只用 pandas 內建（skew、quantile、value_counts、corr(method=spearman)），零新依賴。

### 3. `render_data_atlas_assets(manifest, output_dir, *, frame, target, fields_view, emit_figure)`

- 內部先呼叫 `build_data_atlas`，把結果寫進 `manifest["data_atlas"]`。
- 三張圖複用既有 `_plot_modules` / `_save_figure` / `register_artifact` 管線與配色。
- `semantic_correlation.png` 僅在 ≥2 個數值欄時產出。
- caption 使用白話（缺失率%、哪一群欄位一起變動、哪些欄位被語義規則排除）。

### 4. Plotly 同步（`build_plotly_figures`）

- 移除 `per_1000_outcomes`、`analysis_process`、`trial_history`、`correlation_analysis` figure。
- 新增同名於 PNG 的三張 figure：
  - `ontology_field_map`：各欄位缺失率 dot map，顏色=semantic_kind；forbidden 一律紅色，0% 缺失仍可見。
  - `distribution_small_multiples`：多 xaxis domain 小 multiples；Plotly contract 明確允許且只允許 12 組 axes，互動版與 PNG 均最多顯示 12 欄。
  - `semantic_correlation`：heatmap（同塊狀排序）。
- `baseline_error_comparison` 改為兩列（舊/新模型）堆疊判對/判錯 per 1000。
- 全部經 `ml_plotly_contract.sanitize_figure`。

### 5. server.py wiring

`ontology_contract.validate_analysis_contract` 只投影白名單欄位元資料
（physical_name/unit/semantic_kind/analysis_role/reason），Query Planner 將 bounded `field_views`
寫入 analysis contract；`compose_ml_template` 正規化並以 `FIELDS_VIEW` 常數嵌入 sandbox 程式碼。
模板中 `render_assets` 與 `build_plotly_figures` 均傳 `fields_view=FIELDS_VIEW`。無 ontology
（上傳資料集）時為空列表，atlas 降級為 `unregistered`，不猜測 unit/semantic kind。

### 6. 敘事排板（skill patch 更新）

Dashboard 面板順序改為五幕：

```text
Row 1 決策摘要（不變）
Row 2 資料地圖：field_map → 小multiples → 語義相關熱圖（先講資料限制）
Row 3 模型歸因：feature_importance → SHAP → spec 建議
Row 4 模型證據（折疊技術區）：baseline 併圖、generalization_health、
      confusion、ROC/PR、calibration、threshold_cost
Row 5 部署決策：門檻＋成本＋operating_scenarios
```

skill patch 中提及 `data_profile/correlation_analysis` 的段落同步改為新資產名稱，
並要求「資料地圖」列先講缺失率與被排除欄位。

## TDD slices

### Slice A — 刪除與合併

1. RED：更新 `check-ml-presentation-assets.py`——斷言四張舊圖不存在、
   `baseline_error_comparison.png` 承接 per-1000 語義（caption 含「每 1,000」）。
2. GREEN：`render_assets` 刪四段繪製程式、baseline 圖改雙列堆疊。
3. 回歸：`check-calibration-cost-evidence.py`、`check-cost-threshold.py`、
   `check-ml-presentation-manifest.py`、`check-multi-model-compare.py`。

### Slice B — Data Atlas（本 slice 的 RED 先行）

1. RED：新增 `scripts/check-ml-data-atlas.py`：
   - 已知小型 DataFrame + U1 形態 fields_view 的統計 literal 斷言
     （missing_rate/skew/top_share/flag/cv）。
   - 三張 PNG 存在、可讀、有 caption/alt_text。
   - `manifest["data_atlas"]` 通過 validator 且 warnings 正確。
   - 空.fields_view（上傳情境）降級不爆炸。
   - plotly 三張 figure 存在且 sanitize 通過。
   - `build_plotly_figures` 不再輸出已刪除的四張 figure。
2. GREEN：`build_data_atlas` + `render_data_atlas_assets` + plotly 三圖。
3. 回歉：`check-ml-plotly-presentation.py` 更新為新 figure 集合。

### Slice C — wiring 與 skill

1. RED：`check-ml-data-atlas-template.py` 斷言 server.py 產生的模板含 `FIELDS_VIEW`
   且 renderer/Plotly 呼叫帶 `fields_view=`、SHAP Plotly 使用 transformed matrix。
2. GREEN：ontology 白名單投影 + Query Planner field_views + sandbox 模板修改。
3. skill patch 面板順序段更新。

### Slice D — Telco E2E

以 `ask-o11y-sandbox-analysis:local` image（4 CPU / 2 GiB）執行真實 Telco
CSV（4,225 筆、33 features）：走完 autoresearch → render_assets（含 atlas 三圖）→ SHAP →
plotly figures，斷言 14 張 PNG + 14 張對應 plotly json 產出、manifest 通過 validator，
記錄 wall time。

### Phase 2 — 完整資料故事與 Grafana 全鏈

- 新增 `feature_target_relationships.png`：重要欄位的分箱／類別實際正類率，明示描述性、非因果且未用來重選模型或門檻。
- 新增 `error_slice_analysis.png`：鎖定門檻後 holdout 各切片的 FN/FP 堆疊率；只指出調查方向，不宣稱公平性。
- `render_assets` / Plotly template 新增 `evaluation_frame=X_hold` 與 aligned `target_values`；原始 frame 不冒充 holdout。
- 無 approved ontology 時，以 dtype + bounded 欄名規則產生 `metadata_source=inferred`；caption 必須明示 inferred，不能冒充核可 ontology。
- Dashboard contract 改為五幕：決策摘要 → 資料地圖 → 模型歸因 → collapsed 模型證據 → 部署決策。
- 實作並安裝 `grafana-panels/asko11y-plotly-panel`：bundle 內含 Plotly、Grafana/React external、無 eval/new Function；figure 無效或 render 失敗時顯示同一份 PNG fallback。
- Plotly sanitizer 改成 idempotent：只接受 host-owned fixed config，支援 Sandbox sanitize → Bridge 再 sanitize。

## 實作與驗證結果

- Slice A RED：renderer 仍產出四張舊圖；GREEN 後引用只剩 negative assertions。
- Slice B RED：`build_data_atlas` public seam 不存在；GREEN 後 shape/concentration/Spearman literals、PNG、Plotly、空 ontology 降級均通過。
- Plotly/Telco 首輪 RED：33 features 產生 `xaxis5`，被舊 Plotly contract 拒絕；Phase 2 將 whitelist 明確擴為 x/y axis 1..12，`xaxis13` 仍 fail closed。
- 人工讀圖修正：field map 由缺失率横條改 dot map；低 cardinality 數值改離散 bar；forbidden role 一律紅色。
- `compose_ml_template` 修正既有 Plotly SHAP wiring：`sample_values` 傳 transformed matrix，不再傳原始 `X_sample`。
- Planner/Sandbox server `--self-check`、ML/Plotly/Bridge/dashboard/plugin/calibration/CatBoost/multi-model checks 全綠。
- 正式 image Telco E2E：4,225 rows、33 features、14 PNG、14 Plotly figures、PR-AUC 0.7921、threshold 0.2759、verdict accepted、wall 6.8s（4 CPU / 2 GiB）。
- Grafana API E2E：實際建立 UID `telco-ontology-ml-e2e`，讀回五幕順序，14 signed assets、8 Plotly panels，全數無 unresolved placeholders。
- Chromium acceptance：Plotly 真實渲染、collapsed row 展開、PNG fallback dashboard 均通過；無 page/console error。證據在 `.scratch/telco-data-atlas-e2e/`（未納入版本控制）。

## 驗收標準

- 所有 `scripts/check-*ml*.py` 與受影響的 calibration/CatBoost/multi-model checks 綠燈。
- Telco E2E 在正式 image 內產出完整新資產集合，atlas 統計與 pandas 直接計算一致。
- 已刪除圖表的任何引用（renderer、plotly、check scripts、skill patch）清零。
