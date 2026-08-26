# P0 決策證據 + P1 CatBoost Challenger 設計

日期：2026-08-25
狀態：implemented（TDD + Telco E2E 綠燈）
依據：[ML 方法研究](./ml-methods-research.md)、[多模型穩定度設計](./ml-multi-model-stability.md)

## 目標

在不改變 Ask O11y → Sandbox → bounded manifest/PNG → Grafana Preview 邊界下：

1. **P0**：補齊現有 OOF calibration 與 3:1 threshold selector 的可驗證證據。
2. **P1**：加入 CatBoost 原生類別處理的 optional challenger；不放入預設清單、不承諾勝過 incumbent。

## 已確認 TDD seams

### P0 public seam

`ml_presentation.build_manifest()` + `render_assets()`：

- 輸入固定 manifest、holdout labels、**calibrated holdout probabilities**。
- 輸出新增：
  - `calibration_curve.png`
  - `threshold_cost_curve.png`
  - `manifest.evaluation_evidence`
- 每張 PNG 有 caption/alt；facts 為有限數值，不含 raw rows/path/URL。

### P1 public seam

`ml_autoresearch.run_multi_model_comparison()`：

- `kinds=["catboost"]` 或顯式包含 CatBoost 時可執行。
- 回傳形狀與既有模型相同：CV score、holdout metrics、threshold、guards、verdict、top features、estimator。
- CatBoost 不加入任何預設 kinds；只有 caller 明確要求才執行。

## P0 契約

### Probability 與資料隔離

- hyperparameters：train-only CV。
- calibrator：train OOF predictions fit；目前保留 isotonic，不重寫為 `TunedThresholdClassifierCV`。
- operating threshold：train OOF calibrated probabilities + 已核准成本矩陣選擇。
- final holdout：只評估一次；不得用 holdout curve 重新選 calibrator 或 threshold。
- `compose_ml_template()` 必須把 `result["calibrated_probabilities"]`（不是 raw probabilities）傳給 renderer。

### Manifest facts

`evaluation_evidence`：

```json
{
  "calibration": {
    "evaluated_on": "holdout",
    "selected_on": "train_oof",
    "method": "isotonic",
    "brier_score": 0.0,
    "bins": 10
  },
  "threshold_cost": {
    "evaluated_on": "holdout",
    "selected_on": "train_oof",
    "threshold": 0.0,
    "false_negative_cost": 3.0,
    "false_positive_cost": 1.0,
    "fn_per_1000": 0.0,
    "fp_per_1000": 0.0,
    "weighted_cost_per_1000": 0.0
  }
}
```

### 圖表

- Calibration：quantile bins、理想對角線、Brier score、明示 holdout evaluation。
- Threshold cost：holdout 上的 cost/1,000、recall、precision 對 threshold 曲線；垂直線只標示 train-OOF 已選 threshold，不取 holdout 最低點作新決策。
- bounded artifact 上限由 12 調整為 14，僅容納新增 2 張 evidence PNG。

## P1 契約

### Runtime dependency

- sandbox image 固定 `catboost==1.2.10`（PyPI 提供 CPython 3.14 manylinux wheel；1.2.8 無 cp314 wheel，禁止 source-build fallback）。
- `CatBoostClassifier` 設 `allow_writing_files=False`、`verbose=False`、`random_seed=seed`。
- 只留一層平行：outer `RandomizedSearchCV(n_jobs=1)`；CatBoost `thread_count=4`。

### Raw-category adapter

- 不走 `ColumnTransformer` one-hot。
- 保留原始 DataFrame 欄名與順序。
- numeric 欄保留數值/NaN；categorical 欄將 missing 轉固定 sentinel 後轉字串。
- `cat_features` 使用欄名，unseen category 必須可 predict。
- 每個 CV fold 由 sklearn pipeline clone/fitted；不得在全資料預先建 target/category statistic。

### Search 與 guards

- 與現有 family 同一 `n_iter`/CV/objective。
- search space 只含 iterations、depth、learning_rate、l2_leaf_reg、random_strength、scale_pos_weight。
- 沿用 OOF calibration、3:1 threshold、grouped importance stability、CV–holdout gap、PSI、accepted-first selection。
- SHAP 使用 CatBoost tree attribution，欄名保持原始 ontology 欄位。

### Challenger policy

- `catboost` 加入 allowlist，但不加入 caller 的預設比較清單。
- 本輪只證明「能公平執行與輸出同契約證據」；是否成為預設候選須另以固定 holdout、資源與業務成本 gate 決策。

## TDD slices

### Slice P0

1. RED：`scripts/check-calibration-cost-evidence.py`
   - 已知 labels/probabilities 的 Brier、FN/FP/cost literals。
   - 兩 PNG 存在、可讀、有 caption。
   - manifest facts 明示 train_oof selection / holdout evaluation。
2. GREEN：只補 renderer/facts、template probability/cost wiring。
3. 回歸：presentation manifest/assets、cost threshold、structured executor。

### Slice P1

1. RED：`scripts/check-catboost-challenger.py`
   - mixed numeric/category + unseen category。
   - `run_multi_model_comparison(kinds=["catboost"])` 回傳完整 metrics/guards。
   - preprocessor 輸出仍為原始欄位 DataFrame；thread budget 固定。
2. GREEN：optional import、raw-category adapter、CatBoost estimator/search、allowlists/template SHAP、Docker dependency。
3. 回歸：contract kinds、multi-model compare、cost threshold、structured executor。
4. Image smoke：重建 image，在 4 CPU/2 GiB 執行 CatBoost 小資料與真實 Telco time-boxed comparison，記錄 wall time/peak memory。

## 實作與驗證結果

### TDD

- P0 RED：renderer 未產生 calibration/cost assets；manifest 未拒絕 holdout-selected evidence。
- P0 GREEN：新增 2 PNG、`evaluation_evidence` facts/validation；template 改傳 calibrated probabilities 與 cost matrix。
- P1 RED：`catboost` 不在 kinds allowlist；CatBoost 原生 estimator 因 constructor 改寫 `cat_features` 無法 sklearn clone。
- P1 GREEN：新增 cloneable `CatBoostAdapter` + raw DataFrame preprocessor、optional kind/contract/schema、CatBoost tree SHAP。
- `catboost==1.2.8` 無 CPython 3.14 wheel，image source build 失敗；依 PyPI 官方 metadata 改 pin `1.2.10` cp314 manylinux wheel；NOTICE、third-party manifest、SBOM 與 image digest 已同步。

### 正式 image Telco E2E

條件：4 CPU／2 GiB、固定 34 features、seed 42、train/holdout 80/20、每家 5 trials × 5 folds、PR-AUC、FN:FP=3:1。

| 模型 | CV PR-AUC | Holdout PR-AUC | ROC-AUC | Stability | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| XGBoost | 0.7867 | 0.7981 | 0.9151 | 0.6888 | accepted |
| CatBoost | **0.7924** | **0.8029** | **0.9204** | **1.0000** | accepted |

- accepted-first 本次選出 CatBoost，但它仍只屬受控 challenger，未加入預設清單。
- P0：Brier 0.0980；train-OOF threshold 0.2663；holdout weighted cost/1,000 = 258.0；recall 0.866、precision 0.602。
- `calibration_curve.png`、`threshold_cost_curve.png` 均產出；總 assets 12。
- Ask O11y ML skill 已重建安裝：runtime advisory 要求使用 calibrated probabilities、兩張 P0 evidence 圖，並將 CatBoost 限定為受控 challenger。
- wall time 33.1 秒；main-process max RSS 631 MiB（不等同 cgroup 全行程峰值），未觸發 2 GiB OOM。

## 非目標

- 不做 stacking、soft voting、conformal、uplift/CATE、survival。
- 不重寫現有 OOF calibration/threshold selector。
- 不把 CatBoost 自動加入所有 multi-model runs。
- 不以 final holdout 反覆選模型、校正器或 threshold。
