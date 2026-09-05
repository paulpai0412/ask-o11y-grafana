# 多模型選模穩定度修正設計（grouped stability + verdict gate）

日期：2026-08-25；修訂：2026-09-05。狀態：grouped importance 為既有實作；accepted-first 選模規則已被新設計取代，runtime 修補待完成。
來源：Telco Churn 多模型比較驗收（run_248cba58）發現兩個契約缺陷。

現行規範：[平台架構修訂](natural-language-analysis-platform.md) NLAP-05、11。只可用 train/CV 及其 guards 選模，鎖定 winner 後才揭露 final holdout；含 holdout 的 verdict 不可決定 eligibility。下方 accepted-first code、契約表與綠燈為歷史紀錄，不能作新驗收依據。

## 背景

Ask O11y 以 `run_multi_model_comparison`（sandbox-analysis-mcp/ml_autoresearch.py）比較四種模型後，
XGBoost 以 CV PR-AUC 0.790 勝出，但 `importance_stability=0.577` 判定 `unstable`；
gradient_boosting / random_forest_shap verdict 為 `accepted` 卻因 CV 分數較低落選。
選模規則與守門結論互相矛盾。

## 缺陷 1：importance_stability 在 one-hot 展開名上計算

`_importance_stability()` 對每個 CV fold 取 `preprocess.get_feature_names_out()`
（`num__Age`、`cat__Contract_Month-to-Month`…）的 top-10 集合，計算兩兩 Jaccard。

問題：一個原始欄位被拆成多個 dummy（Contract→3、Payment Method→4），
同一業務訊號的 importance 被分攤，個別 dummy 排名抖動，
top-10 集合不穩定 → 穩定度被系統性低估。

### 修正

新增共用 helper：

```python
def aggregate_importance_to_original(names, values, columns) -> dict[str, float]
```

- 剝除 `num__` / `cat__` 前綴。
- 名稱等於原始欄位 → 直接映射。
- 名稱以 `欄位 + "_"` 開頭 → 映射回該欄位（最長欄位優先，避免前綴碰撞）。
- 無法映射 → 保留原名。
- 同欄多條 importance **加總**。

`_importance_stability` 改為：先聚合回原始欄位，再取 top-k 算 Jaccard。
`columns` 來自 pipeline 的 `feature_names_in_`（訓練欄位），不可用時退化為原名。

## 歷史缺陷 2：選模不考慮 verdict（舊修正已被取代）

`run_multi_model_comparison()` 取全域 `max(cv_score)`，unstable/overfit 模型可能勝出，
與守門機制矛盾。

### 修正

```python
eligible = [r for r in comparison if r["verdict"] == "accepted"]
candidates = eligible or comparison          # 全軍覆沒時退化為全域最高分
best = max(candidates, key=lambda r: r["cv_score"])
```

回傳新增：

- `selected_from_accepted: bool` — True 僅當候選池為 accepted 子集。
- `eligible_kinds: list[str]` — 進入候選池的模型。

## 2026-09-05 修訂驗收（待實作）

- 保留原始欄位 grouped importance；selection guards 只能取自 training/CV，不得含 final holdout。
- 所有 model/feature-set 比較完成後鎖定 winner，再對 winner+baseline 作 final holdout gate。
- Gate 失敗回報 blocked，不在同 holdout 改挑 accepted runner-up；不得以 fallback 選模掩蓋不通過。
- 改 holdout labels 不得改變 winner identity，只能影響 release verdict。
- Manifest 回報 study-level holdout 使用與實際 global trials；舊 accepted-first tests 需要改寫，不能沿用綠燈。
- 多模型目前是 sequential。是否加入 bounded parallel 由 NLAP-11 決定，不因有 comparison helper 就宣稱已平行。

## 歷史契約變更

| 項目 | 舊 | 新 |
| --- | --- | --- |
| `best_kind` | 全域 CV 最高 | accepted 子集中 CV 最高（無 accepted 時全域最高） |
| 回傳欄位 | — | 新增 `selected_from_accepted`、`eligible_kinds` |
| `importance_stability` 語義 | one-hot 欄位 top-k Jaccard | 原始欄位 top-k Jaccard（數值通常上升） |

下游 `check-multi-model-compare.py` 的「best == 全域 max」斷言改為：
`selected_from_accepted is True` 時 best 是 accepted 集合的 max；
`False` 時 best 是全域 max。

## 歷史 TDD 驗收（非新 release gate）

1. `scripts/check-importance-stability-grouped.py`（紅→綠）
   - helper 單元測試：dummy 映射回原欄、加總、未知名保留、最長前綴優先。
   - 整合：含類別欄位的合成資料跑 `run_classification_autoresearch`，
     確認 stability 為 [0,1] 且聚合路徑被使用（grouped ≥ ungrouped 於手工案例）。
2. `scripts/check-multi-model-verdict-gate.py`（紅→綠）
   - 合成資料跑 `run_multi_model_comparison`：
     - 有 accepted 時 → best 的 verdict == accepted，且為 accepted 集合 max；
     - `selected_from_accepted` / `eligible_kinds` 與 comparison 一致。
3. 回歸：`check-multi-model-compare.py`（依新契約更新）、
   `check-cost-threshold.py`、`check-execute-ml-contract.py` 全綠。

## 實作驗證結果

- RED：`aggregate_importance_to_original` 缺失；`selected_from_accepted` 缺失。
- GREEN：兩支新 check 與三支相關回歸均在正式 `ask-o11y-sandbox-analysis:local` image 通過。
- 正式驗證命令限制 `--cpus=4 --memory=2g`，並設定 `LOKY_MAX_CPU_COUNT=4`、`OMP_NUM_THREADS=1`，避免主機 CPU 數造成 joblib 過度展開。
- 已重建 `ask-o11y-sandbox-analysis:local`；image smoke 確認包含 grouped stability、accepted-first verdict gate，且 seaborn theme 後仍保留 CJK 字型。
- 真實 Telco 4,225 筆 E2E（同 34 features、20 trials、5-fold、PR-AUC、3:1 成本）：XGBoost stability **0.577 → 0.689**、verdict **unstable → accepted**；四模型均 accepted，accepted-first 規則仍以 CV PR-AUC 0.787 選出 XGBoost。

## 非目標（後續 todo）

- sandbox code audit 強制 helper 呼叫。
- manifest 衛生檢查（拒 NaN、拒 estimator 序列化）。
- CJK 字型回歸測試與 sandbox image 重建驗證。
- 特徵工程 / monotonic constraints / repeated CV。
