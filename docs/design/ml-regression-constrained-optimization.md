# Regression Autoresearch + Constrained Parameter Search

狀態：既有 regression 路徑與歷史 E2E 已實作；2026-09-05 契約修訂待完成。

現行規範見 [平台架構修訂](natural-language-analysis-platform.md) NLAP-04/05/09/10/11；展示依 NLAP-07。歷史資料集案例不構成 production 欄位或流程規則。

## 背景與缺口

本設計初版時 `execute_ml_contract` 只支援二元分類；目前已有 trusted regression template。原先以 Ask O11y LLM 臨時撰寫 `execute_python_analysis` 處理連續目標的風險如下，仍需以執行契約而非 prompt 約定防止：

- 模型比較不公平（不同切分、不同 preprocessing fit scope）。
- 可能誤用 holdout 選模或反覆最佳化。
- target 組成／代理欄位未一致防堵。
- 連續目標缺 MAE/RMSE/R² 與 bootstrap interval 的結構化 manifest。
- 「最佳參數」容易外推未觀察支援範圍，或把觀察關聯誤稱為因果最佳。
- 同一流程無法複用於其他 regression／製程最佳化分析。

## 目標

新增兩個 capability-driven 通用能力，由 LLM 依資料動態選擇，不為特定 dataset hardcode。

### 1. Regression Autoresearch

Analysis contract 增加：

- `task_kind: "regression"`
- Metric 與 optional `optimization.direction` 分離；legacy `target_direction` 表示 predicted target 的業務方向，不是 MAE scorer 方向。純預測不要求或預設最佳化方向；schema 升版與相容轉換依 NLAP-09。
- `controllable_fields`: 可操作參數（後續最佳化用）
- `context_fields`: 只能調整、不可操作（負載、環境、日期）
- `forbidden_fields`: target proxy、post-outcome、未來資訊
- `split.kind`: chronological/grouped holdout（regression 不允許 stratified random 作為預設）
- 可用模型能力清單：Dummy、Ridge、Random Forest、Extra Trees、Histogram Gradient Boosting、CatBoost（可選）、XGBoost（可選）。
- LLM 依資料特性選擇模型子集，production 不固定每次全跑。

Deterministic 規則：

- preprocessing 僅 fit training。
- 訓練期 rolling/chronological CV 選模。
- MAE 為主選模指標，RMSE/R² 為輔助。
- 全部 model/feature-set 比較只用 train/CV；鎖定 winner 後才做 final holdout gate，不能以 holdout verdict 改挑模型。研究層級記錄所有 holdout 使用，不能只引用 selected helper 的 counter。
- 產出 baseline、CV mean/std、holdout MAE/RMSE/R² 與 bootstrap interval。
- 只有優於 dummy baseline 的模型可進入 constrained search。

### 2. Constrained Parameter Search

Regression 驗證通過後才可選擇啟用：

- `optimization.direction`: 使用者明確確認的 minimize/maximize target；與 `metric` 的 scorer direction 獨立。未請求最佳化時此契約不啟用。
- `controllable_parameters`: 僅 ontology 標記可操作的欄位。
- `fixed_context`: 搜尋時鎖定或依支持子集分層。
- `support`: minimum samples、maximum extrapolation distance、sparse group policy。
- `bounds`: 只取觀察 support 或使用者核准的工程界線。
- `uncertainty`: bootstrap / conformal 評估，不給單點答案。

安全規則：

- 預設不得外推未觀察組合；外推必須使用者明確核准且標為高不確定。
- 稀疏組合不得宣稱「最佳」；只能標為樣本不足。
- 輸出為 `candidate_settings`，不是因果最佳。
- 必須列出現場受控試驗建議與停止條件。

## 系統邊界

Ask O11y LLM：選擇能力、決定分析 preview、解釋結果、產生 dynamic report。
Deterministic contract/template：切分、preprocessing、模型訓練、CV、holdout、搜尋、manifest。
Ontology：target/role/forbidden/controllable/context 的唯一治理來源。
Grafana Query 仍是唯一 datasource execution boundary。

## U1 歷史 fixture 的必要 guard（不可移植為通用 hardcode）

- 目標 `熱耗率` 為連續、應 minimize。
- `原煤耗_g` 與熱耗率高度相關，可能為目標組成／代理，需排除。
- 總用煤量、發電量、平均熱值亦需 leakage 審查。
- A/B/C/D 燃燒風門開度為常數，不可最佳化。
- 煤源位置組合稀疏且不平衡；不支援的組合不得給最佳結論。
- 煤源／日期／煤質／負載可能有強混雜。

## TDD slices

### A. Contract 與資料治理

RED：

- regression contract schema；拒絕 stratified random 作為連續時間資料預設。
- target proxy/forbidden/context/controllable role 驗證。
- target 為常數或全缺時 fail closed。
GREEN：`ontology_contract` + Planner schema + `ml_regression.py`。

### B. Multi-model regression autoresearch

RED：

- 小型 deterministic regression fixture：多模型在同一 chronological folds 下評估；Ridge/Forest/ExtraTrees/HGB/CatBoost(可選) 均使用 training-only preprocessing。
- holdout metrics 與 bootstrap interval 精確 literal。
- 不顯著優於 Dummy 時 verdict 不允許進入 constrained search。
GREEN：`ml_regression.py` + `ml_presentation` regression sections。

### C. Constrained parameter search

RED：

- 稀疏 group 被拒絕。
- 搜尋只在觀察 support 內；不產生 unseen position/source 或超出 bounds。
- 產出候選 set + uncertainty + support，不產生因果語句。
GREEN：同模組；manifest 寫 governance/candidates/uncertainty。

### D. execute_regression_contract 模板

RED：template string 檢查 fields_view、target proxy exclusion、CV/holdout 與 constrained-search gate；server 不接未核准 kind。
GREEN：Sandbox template 與 tool wiring。

### E. Ask O11y 動態 report 與 U1 E2E

- skill 更新為 capability-driven regression/optimization 導引，不指定固定步驟或 U1 欄位。
- U1 經上傳資料→preview→frame_ref→regression contract→完整 manifest/artifacts→LLM report synthesis→dynamic dashboard。
- 驗證排除 target proxy、常數風門；稀疏煤源組合不得宣稱最佳。
- 18/18+ ML checks、Bridge/servers、plugin、LSP/lens、Chromium E2E 全綠。

## 實作與驗證結果

- 通用 production path：Planner regression contract → Grafana Query → `execute_ml_contract` trusted template → regression manifest/PNG → whole-report LLM synthesis → Grafana Preview。
- Regression models：Dummy、Ridge、Random Forest、Extra Trees、Histogram Gradient Boosting，以及 optional CatBoost/XGBoost。
- Approved ontology snapshots 支援 generic nested feature-set sensitivity：approved context-only、明確 opt-in 的 treatment candidates、以及 treatment-only；三組共用 split、preprocessing、algorithm budget 與 baseline/holdout gate，treatment inclusion 不等於操作核准。
- `search_candidate_settings` 只評估已觀察組合，強制 context-group support、bounds、bootstrap uncertainty、非因果文字與現場試驗停止條件。
- Regression contract 支援通用 `population_filter`：以 authorized frame 的 exact scalar equality 固定分析母體；filter 欄位由 Planner 自動加入 query projection，但不會成為 model feature，未知欄位/值與零支持均 fail closed。
- Fresh natural-language Ask O11y E2E 由 session upload 動態選擇 regression capability；U1 模型未同時通過 CV/holdout Dummy gate，因此安全輸出 `blocked_by_baseline`，未產生候選設定。
- Fresh registered-U1 Ask O11y E2E 由同一 approved snapshot 動態建立並執行 Model A/B/C 三個獨立 plans：Model A approved features、Model B 加 treatment candidates、Model C 僅 treatment candidates；三組均產出 deterministic manifest 與 Result Preview，未使用 generated training code 或 Dashboard writer。
- Browser 驗證 UID `heat-rate-regression-preview-5a46b68f`：Preview tag、evidence-bound narratives、模型比較與候選阻擋圖均可見，無 console/page error。
- Fresh fixed-population U1 E2E：Ask O11y 自然語言指定 exact 澳洲/澳洲/印尼/印尼 filter；Planner/Query/Sandbox 驗證母體 76 rows，使用 25 個精確煤質、位置用煤量與 treatment/process features，filter fields 未進 model features，Result Preview 未寫 Dashboard。

## 非目標

- 不把觀察性關聯改成因果最佳化。
- 不支援 Bayesian optimization 在未觀察範圍的高風險外推。
- 不將 CatBoost/XGBoost 強制加入所有 regression。
- 不在 regression 中用 accuracy／calibration／threshold 分類語義。
