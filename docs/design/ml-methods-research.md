# Ask O11y / Grafana ML 方法研究：先強化決策層，只把 CatBoost 當受控 challenger

狀態：research / 可供決策
範圍：Telco Churn 現有分類 slice；不實作、不新增 runtime 依賴、不改變 Grafana 邊界。

## 結論先行

1. **現在最值得做的不是再堆模型，而是補齊現有「校正 → 3:1 成本門檻 → Grafana 證據」的驗證。** 現行程式已以訓練集 OOF 機率做 isotonic 校正與成本門檻掃描；`TunedThresholdClassifierCV` 是可用的 sklearn 對照實作，而非必須取代既有的理由。[現況：R1；API：S4、S5]
2. **CatBoost 是唯一值得立即做、但只能作為 time-boxed challenger 的新模型。** 34 個混合型欄位正是其原生類別欄位介面可測的情境；它尚未在 sandbox image 中，因此不能宣稱會勝過現有 XGBoost/LightGBM。先以同一個固定 holdout 的一次預先登錄實驗證明，再決定是否替換一個 challenger。[R1、R5；S1–S3]
3. **維持簡單 challenger 是正確預設。** 現行流程已以 PR-AUC 選模、固定 holdout、CV–holdout gap／importance stability／PSI verdict gate 過濾候選；在 4,225 筆資料、4 CPU／2 GiB 與 deterministic Preview 的限制下，沒有「再加模型必然更好」的證據。[R1、R4]
4. **stacking/soft voting 與 conformal 都是「有條件的後續項」，不是現在的模型清單擴張。** 前者必須先證明兩個已通過候選的 OOF 錯誤互補；後者只在產品真的有人工覆核／延後決策動作時才有價值，且不會提高 PR-AUC。[S6、S7、S11、S12]
5. **uplift/CATE 與 survival/time-to-churn 不是目前資料契約可做的分類替代品。** 前者要有介入 treatment、結果與介入前混雜因子；後者要有事件時間與 right-censoring。現有函式只接收分類 `target`／`holdout_target`，未表達這些欄位；應先收集資料、建立新 task contract，不能把 churn label 直接誤當因果或存活標籤。[R1；S8–S10]

## 1. 已核對的 repo 現況與研究邊界

本 repo 的設計文件慣例是 `docs/design/` 下以「Status／Scope／背景／契約／驗收」組織；本研究沿用該慣例。未發現既有專用 research 目錄。本次交付的邏輯檔名為 `docs/design/ml-methods-research.md`。

| 已驗證現況 | 對方法選擇的含義 |
| --- | --- |
| `run_multi_model_comparison()` 只接受 `gradient_boosting`（LightGBM，缺套件時退回 HistGradientBoosting）、`xgboost`、`random_forest_shap`、`logistic_regression`；各模型按總 `n_iter` 平分預算，依 **CV PR-AUC** 選 accepted 候選中的最高分。 | 新模型不應無限制塞入清單；加第 5 家會把既有每家可用 trial 再縮小，反而使比較不公平。[R1] |
| 現行所有 autoresearch 模型先經 numeric impute/scale 與 categorical one-hot；另有 training-only `TargetEncoder` helper，但 autoresearch 沒有呼叫它。 | CatBoost 必須走獨立的「原始 DataFrame + `cat_features`」支線，不能把 one-hot pipeline 包在其前面。[R1、R2；S1、S2] |
| 最佳模型先在 train-only CV 搜尋；holdout 未傳入 search。其後以 OOF 預測 fit isotonic calibrator，掃描 49 個分位數門檻，依 FN:FP 成本選 operating threshold，並輸出 cost-optimal／balanced／recall-priority 三個每千筆情境。 | 已有正確的決策層骨架；應驗證與呈現它，而不是為 API 名稱重寫。[R1；S4、S5] |
| Guard 為 CV–holdout gap、原始欄位聚合後的 importance stability、numeric PSI；`accepted` 優先於全域最高 CV 分數。 | 任一新方法必須沿用同一 verdict gate，不可只以 PR-AUC 勝出就發佈。[R1、R4] |
| presentation 有通用 `render_shap_summary()`、grouped feature/permutation importance、ROC/PR、混淆矩陣與每千筆圖；但 `render_assets()` 目前**沒有**輸出 calibration curve 或 threshold-cost 圖。設計文件則將兩者列為 performance evidence。 | 這是本輪最小、最高價值的 Grafana 證據缺口；方法增加前先補可驗證的校正／成本圖，不需改 dashboard boundary。[R3；R6] |
| sandbox Dockerfile 已固定 sklearn 1.9、SHAP、XGBoost CPU、LightGBM，未安裝 CatBoost、EconML、scikit-survival、MAPIE。 | 新依賴必須單獨接受 image/offline/reproducibility 審核；不能假定它們已可在 sandbox 跑。[R5] |

本研究採用題設的當前資料輪廓：Telco Churn 4,225 筆、34 個混合型特徵、正類約 26.5%、FN:FP = 3:1、固定 holdout、PR-AUC 選模、4 CPU／2 GiB。這些是**目前分類 slice** 的條件，不是對未來有 treatment 或時間欄位資料的否定。

## 2. 方法逐項評估

### 2.1 CatBoost 原生類別處理 — **P1：現在可做的單一新 challenger**

| 面向 | 判斷 |
| --- | --- |
| 適用條件 | 特徵含類別欄位，且可在每個訓練 fold 將原始欄名／類別清楚傳給 `cat_features`。CatBoost 官方 API 接受欄位 index 或 name；官方文件也明確警告不要在前處理手動 one-hot，而應由 CatBoost 的內建機制處理。[S1、S2] |
| 對本案的可能收益 | 現行 autoresearch 將每個類別欄 one-hot；CatBoost 可保留原始欄位及其類別處理，因而是與現有樹模型**有實質差異**、又對混合型小中型表格資料合理的比較對象。這是可檢驗的假設，**不是已證實會提升 PR-AUC 的事實**。[R1；S1、S3] |
| 資料／運算／依賴成本 | 不需新資料；需新增尚未安裝的 `catboost` 與一個不經 `ColumnTransformer` one-hot 的 estimator path。[R1、R5] 官方 `thread_count` 可限制訓練執行緒，且 CPU 上該參數不影響結果；在 4 CPU 下應只保留一層平行（例如 outer search `n_jobs=1`、CatBoost `thread_count=4`），實測記錄峰值記憶體，而不是假定 2 GiB 一定足夠。[S3] |
| SHAP／Grafana 相容性 | 相容。CatBoost 可輸出 `ShapValues`，其每列包含各特徵 contribution 與 expected value，且 contribution 加總為該筆 prediction；既有 renderer 已接收值矩陣與特徵名稱。因原始欄名未被 one-hot 拆散，dashboard 的 ontology 聚合反而較直接。[R3；S2、S3] |
| 洩漏／過擬合風險 | **高風險點是整合方式，不是名稱。** 不可先在全 train 建 category/target statistic 再 CV；每 fold 必須只以其訓練部分 fit。保留既有 post-outcome 欄位排除、固定 holdout、seed 與 guard；若要 early stopping，validation 也只能由當前 train fold 再切出，不能借 fixed holdout。CatBoost 本身也提供 validation/overfitting detector，不是豁免此規則。[R1、R6；S3] |
| 是否現在做 | **做一次受控實驗，不直接加入預設模型清單。** 若未在預先宣告的 PR-AUC、成本與 guard gate 同時勝出，刪除／不加入，維持現行 challenger。 |

**最小實作方向（供後續規劃，不在本次實作）：** 新增一個 raw-category estimator adapter，輸入已通過 ontology／leakage 審核的原始 DataFrame、`cat_features` names、固定 `random_seed`/thread budget；輸出和現有 estimator path 相同的 probability、permutation importance、CatBoost SHAP adapter。不要把 CatBoost 放進 `_preprocessor()`，也不要新增通用模型 factory。

### 2.2 Cost-sensitive threshold tuning + probability calibration — **P0：現在先做**

| 面向 | 判斷 |
| --- | --- |
| 適用條件 | 已有正／負類 label 與已核准的 FN:FP 成本。題設 3:1 正好滿足；成本仍必須由業務確認其是否代表真實 retention action。[R1、R6] |
| 對本案的收益 | 將「排序能力（PR-AUC）」與「採取行動（門檻、FN/FP、每千筆成本）」分開。sklearn 說明 threshold tuning 只改 class decision；`predict_proba`、ROC 與 PR curve 不變。因此它不能讓 PR-AUC 變好，卻可能讓 3:1 下的營運成本更低。[S4] |
| 現況與最小變更 | 現行 `select_operating_threshold()` 已在 OOF calibrated train probabilities 上做成本掃描，且保留三個情境；因此**不建議為 `TunedThresholdClassifierCV` 進行 wholesale rewrite**。[R1] sklearn 1.9 已含該 API，可用同一個 custom business-cost scorer 做對照／敏感度測試；其文件明示內部 CV 選門檻，也警告不可用同一資料同時 train classifier 與 tune threshold。[S4、S5] |
| 校正 | 現行只有 isotonic。`CalibratedClassifierCV` 的官方文件支持 sigmoid／isotonic 並以 CV 取得未見預測來校正；它也警告 calibration 樣本遠小於 1,000 時 isotonic 容易 overfit。故應在 train-only OOF 上比較或預先固定 sigmoid/isotonic，記錄 Brier/calibration curve；絕不以 fixed holdout 選方法。[R1；S5] |
| 運算／依賴 | sklearn 已在 image，無新依賴；與現有 OOF CV 同級。若加入完整 calibrator wrapper，避免與外層 `RandomizedSearchCV(n_jobs=-1)` 做巢狀平行。[R1、R5；S5] |
| SHAP／Grafana 相容性 | 不改 base model attribution；需新增兩個 bounded PNG：reliability/calibration（附 Brier 與 split）及 threshold–cost/recall/precision（附 3:1 假設、每千筆與 chosen threshold）。這恰好補上設計契約與 renderer 的落差。[R3、R6] |
| 洩漏／過擬合風險 | calibration、threshold、hyperparameter 選擇都不能看 final holdout。當候選數／調參多時，OOF 門檻也可能對 train 選擇流程過度樂觀；final holdout 僅作一次預先登錄的報告，並保留現有 guard。[R1；S4、S5] |
| 是否現在做 | **是，P0。** 先補量測與圖，不改選模主指標 PR-AUC，不以 0.5 或 class weight 取代顯式成本決策。 |

### 2.3 Stacking／ensemble — **P2：資料已可做，但現在不做**

| 面向 | 判斷 |
| --- | --- |
| 適用條件 | 至少兩個已通過 guard 的候選在 OOF／holdout 上有可量化的錯誤互補，而非只因分數接近就混合。此互補性尚未由現況證明。[R1、R4] |
| 可能收益 | sklearn 的 soft voting 是校正良好分類器的加權機率平均；stacking 則以 base estimators 的 OOF 預測訓練 meta-model。兩者有可能降低錯誤，但官方文件沒有保證會勝過單一模型。[S6、S7] |
| 運算／依賴 | sklearn 已提供 API，無新套件；但 stacking 會為每個 base estimator 產生 cross-validated predictions 再 fit meta-estimator，對目前 4 CPU/2 GiB 的小資料流程增加模型訓練與巢狀 CV 成本。[S6；R1、R5] |
| SHAP／Grafana 相容性 | **不是零成本相容。** 現有 SHAP panel 假設一個原始特徵矩陣到一個模型；stack meta-model 的輸入是 base predictions。需另顯示 base-model attribution 與 ensemble weight/OOF evidence，不能把 meta SHAP 說成客戶欄位的因果或直接歸因。[R3；S6] |
| 洩漏／過擬合風險 | sklearn 明示 final estimator 應由 cross-validated base predictions 訓練，也警告拿同一資料上 prefit base models 來 train stack 有很高 overfit 風險。[S6] 這與目前固定 holdout 的保護方向一致。 |
| 是否現在做 | **否。** 先完成 CatBoost 試驗；只有兩個 accepted 模型的 OOF residual correlation／不同 FN case 顯示互補，且單一模型未達業務成本目標時，才用兩模型、固定權重的 soft voting 做一次小實驗。不要直接上 full stacking。 |

### 2.4 Uplift／CATE（留存介入效果）— **P3：需要新增資料與新產品契約**

| 面向 | 判斷 |
| --- | --- |
| 適用條件 | 目標問題必須是「對此客戶採取 retention treatment 是否改變結果」，而非「此客戶是否 churn」。EconML DR 類方法需要 observed outcome `Y`、treatment `T`、heterogeneity features `X`、controls/confounders `W`；文件也將「所有會同時影響 treatment choice 和 outcome 的 confounders 已觀測」列為前提，並說明小 overlap 會增大變異。[S8] |
| 對本案的收益 | 若有隨機 A/B 或可審核的觀察性資料，才可把排序從 churn risk 改為 **incremental save / treatment effect**，較貼近「該聯絡誰、給何種 offer」的決策。[S8] |
| 目前阻塞 | `run_classification_autoresearch()` 只有 train/target/holdout/holdout_target 的二元分類介面；本次已讀的資料／runtime contract 未表達 treatment、assignment time、eligibility、pre-treatment confounder 或 control group。這證明**現有 runtime contract 不支援** CATE；不等於永遠沒有上游資料，需資料 owner 確認。[R1] |
| 需要新增資料 | customer/unit ID、treatment/control 與 offer/channel、assignment/eligibility/send timestamp、outcome observation window、churn/result、介入前特徵與可能 confounders、policy changes；優先隨機化以降低未觀測混雜風險。須另外檢查 treatment overlap、propensity 與時間順序。[S8] |
| 運算／依賴／呈現 | EconML 不在 image；DR learner 有 nuisance outcome/propensity model、cross-fitting 與效果模型，不能假定符合 2 GiB。[R5、S8] EconML 有 CATE `shap_values`，但它解釋的是「效果異質性」而不是 churn risk；Grafana 必須新增 uplift/overlap/policy value/CI 與因果假設面板，不能沿用目前「模型關聯」文案。[S9；R3] |
| 是否現在做 | **否，資料與治理先行。** 沒有介入／對照資料時，任何 uplift/CATE 數字都應 fail closed。 |

### 2.5 Survival／time-to-churn — **P3：需要新增時間與 censoring 資料**

| 面向 | 判斷 |
| --- | --- |
| 適用條件 | 問題必須改為「何時 churn」，每筆需要 event indicator、event time 或 last-observed time（right-censoring）。scikit-survival 的 `Surv` target 正是由 event 與 observed time 組成；Random Survival Forest 也要求這個 structured target。[S10] |
| 對本案的收益 | 若 retention 作業要排程，可提供特定 horizon 的風險／survival curve，而不是只有 snapshot churn probability。評估也必須改成 censoring-aware C-index、time-dependent AUC、Brier score，而非直接沿用單一 PR-AUC。[S10] |
| 目前阻塞 | 目前分類 contract 是單一 label；沒有 event-time/censoring 欄位、observation cutoff 或 temporal split contract。[R1] 因此不能把「尚未 churn」一律標成非 churn，否則會錯處理尚未觀測到事件的客戶。 |
| 需要新增資料 | cohort entry/snapshot time、churn/cancel time、last-active/last-observation time、event flag、資料擷取 cutoff、可用於 temporal train/holdout 的時間序列；所有 feature 必須在預測時點之前取得。 |
| 運算／依賴／呈現 | scikit-survival 不在 image，且會引入不同 estimator/metric 及 survival-function assets。[R5、S10] 現有 ROC/PR、成本 threshold 和單一 SHAP panel不是充分的 survival evidence；要有 horizon 指定、censoring rate、survival/calibration-by-time 與 temporal drift 說明。[R3、S10] |
| 是否現在做 | **否。** 先完成資料 contract 與時間切分驗收，再選 Cox/RSF 等最小 baseline；不要把 survival 作為本次分類模型的第 N 個 challenger。 |

### 2.6 Conformal uncertainty — **P2：資料可做，但僅在有明確 abstain/review 動作時做**

| 面向 | 判斷 |
| --- | --- |
| 適用條件 | 需要 base classifier、未用於 fit 的 conformalization data，及產品對「不確定」的明確處置（人工覆核、延後接觸或收集更多資料）。MAPIE upstream 文件描述的典型流程亦是 fit base estimator、在未參與 fit 的資料上量 conformity score、再輸出 set/interval。[S11] |
| 對本案的收益 | 可把二元分類顯示為 `{留存}`、`{流失}`、`{兩者皆可能}`（或依方法的空集合）並把不確定 case 導向人工處理；**不會使不準的 base model 更準，也不會提升 PR-AUC**。[S11、S12] |
| 保證與風險 | 原始文獻在 IID 情境給出長期 coverage；MAPIE 文件將它表述為 exchangeability 下的 **marginal** coverage，而非每個客戶／敏感 subgroup 的條件保證。漂移、時間順序或小校正樣本會使承諾失效或集合變寬。[S11、S12] |
| 運算／依賴 | MAPIE 不在 image；split conformal 要犧牲一部分 train，cross conformal 要更多重訓。4,225 筆與少數類約 26.5% 下，先量測校正分割後的有效正類數與集合大小，再決定是否值得加入。[R5；S11] |
| SHAP／Grafana 相容性 | base-model SHAP 可保留，但只能解釋其 score，不能把 SHAP 當 coverage 保證。新增 bounded panel：nominal/empirical marginal coverage、singleton/ambiguous counts per 1,000、review capacity、split type 與 exchangeability limitation；不輸出個別客戶資料。[R3；S11、S12] |
| 是否現在做 | **否，除非產品 owner 明確確認「ambiguous → 誰處理、容量多少、成本多少」。** 沒有該動作時，先把 P0 calibration 做好即可。 |

## 3. 分階段優先序

| 優先序 | 做什麼 | 現有資料可做？ | 為何現在／為何不做 | 結束 gate |
| --- | --- | --- | --- | --- |
| P0 | 保留現有 OOF isotonic + 3:1 threshold selector；加入 Brier/reliability、threshold–cost 圖與 manifest facts；以 `TunedThresholdClassifierCV` + custom cost scorer 做 parity/sensitivity check。 | 是 | 不增加模型或依賴，直接補決策證據與設計落差。 | 所有圖均標示 train-CV/holdout、成本假設與 threshold；holdout 未參與選擇；既有 verdict 不退化。 |
| P1 | CatBoost raw-category pilot，只和現任 champion/gradient booster/LR sanity baseline 比較。 | 是（但需新套件） | 唯一對混合類別特徵有不同歸納偏好的受控 challenger。 | PR-AUC、3:1 cost、calibration、gap/stability/PSI 全部達預先登錄 gate；否則不加入。 |
| P2a | 兩模型 soft vote 或 stacking。 | 技術上是 | 只在 P1 後有 OOF error complementarity 與未達成本目標時。 | OOF-only meta fitting；勝過單一 accepted 模型；有雙層解釋與同樣 holdout gate。 |
| P2b | Conformal prediction set。 | 技術上是 | 只在有 review/abstain action，不能作為「看起來更科學」的裝飾。 | 明確 capacity policy；coverage/ambiguity/review metrics；IID/drift limitation 顯示。 |
| P3a | Uplift/CATE。 | 否，需介入資料 | 預測 churn 與估計 offer 的因果增量是不同任務。 | treatment/control、time order、confounder、overlap、因果驗證 contract 皆存在。 |
| P3b | Survival/time-to-churn。 | 否，需 event time/censoring | snapshot label 無法回答 time-to-event。 | event/censoring/cohort cutoff/temporal split 與 survival metrics/assets 完備。 |

## 4. 最小實驗矩陣（避免把固定 holdout 變成調參資料）

### 4.1 預先固定的共同條件

- Freeze 同一份 ontology／leakage exclusion、34 欄 feature list、正類定義、3:1 cost matrix、seed、image/library versions、train/CV/holdout indices；holdout **只在最後一次**讀取並報告。[R1、R4、R6]
- 所有 preprocessing（包括 CatBoost 類別處理、calibration、threshold）只能由當前 CV training portion fit；不得把 target/category statistic、threshold 或 calibration 先算在整個資料後再切 fold。[R1、R2；S4、S5]
- 只選一層平行，將實際 wall time、peak memory、thread setting 寫進 manifest/provenance；若超出 sandbox 限制，該方法失敗而非降低 guard。[R1、R5；S3]

### 4.2 兩段式矩陣

| 段 | 候選／設定 | 在何處選擇 | 必收集的證據 | 不可做的事 |
| --- | --- | --- | --- | --- |
| A：family screen | A0 現任 accepted XGBoost（或 image 實際的 gradient booster champion）；A1 現有 gradient booster；A2 LogisticRegression sanity baseline；A3 **CatBoost raw-category**。相同 CV folds、相同每家 search budget、PR-AUC objective。 | Train-only stratified CV；沿用 accepted-first guard。 | CV PR-AUC、fold variance、fit time/memory、top features stability、預先固定的 params/trials。 | 不因 A3 加入就偷偷提高總 search budget；不看 holdout 選 A0–A3。 |
| B：operating layer（只給 A 的前兩名） | B0 現行 OOF isotonic + custom 3:1 selector；B1 sigmoid + same selector；B2 `TunedThresholdClassifierCV` + same business-cost scorer（只作 threshold sensitivity/parity）。 | Train OOF；calibration method 以預先規則/Brier 與可靠度曲線決定。 | Brier/reliability、threshold 分布、recall/precision/FN/FP/cost per 1,000、三種情境。 | 不讓 fixed holdout 決定 calibrator 或 threshold；不宣稱 B2 改善 PR-AUC。 |
| C：一次 final report | 只帶入在 A/B 已鎖定的單一 champion。 | 固定 holdout，一次。 | PR-AUC、ROC-AUC、Brier/reliability、3:1 cost、混淆矩陣、現有 gap/stability/PSI verdict、所有 Grafana assets。 | 看到 holdout 後回頭重跑／換模型／改成本；若要再做，必須取得新的 holdout。 |

**CatBoost 加入 gate：** 不預設 numeric uplift 閾值（避免在研究文件捏造業務門檻）。產品 owner 必須在 C 前明定「至少不劣於 incumbent 的 PR-AUC」與「3:1 每千筆成本至少改善多少才值得新增依賴」。若任一既有 `accepted` guard、資源限制或解釋契約失敗，保留現有模型。

## 5. Scope、acceptance、validation 的直接影響

### Scope

- **本次不應**新增 neural network、SVM、AutoML 平台、資料庫、dashboard generator 或 generic ensemble abstraction。
- CatBoost 若進入實作，只新增一個 raw-category adapter 與其最小驗證；不能重構現有四族模型或改變 Ask O11y → sandbox → opaque asset → Grafana Preview 邊界。[R1、R3、R6]
- CATE/survival 是新 task kind 與新資料 contract，不是 `kind` 字串多一個分支；在 schema 未滿足時應拒絕執行。[R1；S8、S10]

### Acceptance

1. 固定 holdout 未參與 hyperparameter、calibrator、threshold、ensemble weight 或 conformal score 的選擇；結果明確標示 split。 [R1；S4、S5]
2. CatBoost 只使用原始 categorical names，沒有全資料 one-hot/target statistic；CV fold training-only 證據可稽核。 [R1、R2；S1、S2]
3. 任何 champion 仍須現有 `accepted` verdict，且 final report 同時呈現 PR-AUC、校正、3:1 FN/FP/cost per 1,000、gap/stability/PSI。 [R1、R4]
4. Grafana Preview 只收到 bounded manifest/PNG；新增 calibration/cost 視覺需含分割、成本假設、CJK 可讀 caption，不能含 raw rows、physical paths 或 credential。 [R3、R6]
5. 無 treatment/control 或無 event/time/censoring 時，CATE/survival 必須拒絕，而非退化成一般分類再貼因果／時間標籤。 [S8、S10]

### Validation

- **P0 checks：** calibration Brier/reliability curve 與 3:1 threshold-cost curve 都可由同一 deterministic input 重現；其中 threshold chart 的最佳點須等於 manifest operating threshold；三個 scenario 的 FN/FP/cost 與 table 一致。[R1、R3]
- **P1 checks：** raw categorical path 在 unseen category 下可 predict；每一 fold 的 cat preprocessing fit scope 被測試；seed/thread/model version 被記錄；CatBoost SHAP 欄名數與輸入欄名一致。[S2、S3]
- **P2 checks：** stacking 的 meta training input 是 OOF predictions；conformal 的 conformalization indices 與 base-fit indices disjoint，並呈現 empirical marginal coverage，不誇稱個人保證。[S6、S11]
- **P3 checks：** CATE schema 要求 `T,Y,X/W` 與 assignment/outcome time ordering；survival schema 要求 event/time/censoring/cutoff，缺任一項 fail closed。[S8、S10]

## 6. 尚未能驗證、必須由 owner 決定的問題

1. 3:1 是否是已核准的真實 monetary/operational cost，還是暫定權重？若是暫定，P0 只能產出 scenario，不可宣稱 operating ready。
2. Grafana 使用者是否真的有「uncertain → 人工 review／延後決策」流程與容量？沒有就不做 conformal。
3. 是否存在 retention offer/control、assignment/eligibility timestamp、介入前 confounders，以及足夠 treatment overlap？若有，優先設計 CATE data contract；若沒有，CATE 不在 roadmap。
4. 是否存在 cohort、churn/cancel timestamp、last observed timestamp 與 censoring 定義？若有，survival 是獨立 discovery，不與本次 classifier 混跑。
5. CatBoost 加入 image 的離線供應鏈／授權／image-size 政策是否接受？以及 P1 的預先登錄「material cost improvement」門檻是什麼？
6. 4 CPU/2 GiB 下 CatBoost 實際 wall-time/peak-memory 尚未量測；本文件不以官方 API 或小資料量推斷它一定能通過。

## 7. 來源（primary sources 優先）

### Repo primary sources

- **R1** — [`sandbox-analysis-mcp/ml_autoresearch.py`](../../sandbox-analysis-mcp/ml_autoresearch.py)：模型清單、PR-AUC search、OOF isotonic、threshold cost scenarios、holdout isolation、guards、accepted-first selection。
- **R2** — [`sandbox-analysis-mcp/ml_preprocessing.py`](../../sandbox-analysis-mcp/ml_preprocessing.py)：training-only impute/one-hot/TargetEncoder helper。
- **R3** — [`sandbox-analysis-mcp/ml_presentation.py`](../../sandbox-analysis-mcp/ml_presentation.py)：bounded manifest、SHAP renderer、既有 PNG assets；未產生 calibration/cost assets。
- **R4** — [`ml-multi-model-stability.md`](./ml-multi-model-stability.md)：Telco 34 features／PR-AUC／grouped stability 與 accepted-first gate 的已實作設計。
- **R5** — [`sandbox-analysis-mcp/Dockerfile`](../../sandbox-analysis-mcp/Dockerfile)：已固定的 image packages。
- **R6** — [`ml-grafana-presentation.md`](./ml-grafana-presentation.md)：Preview/publish boundary 與 calibration/threshold-cost evidence direction。

### Upstream／原始研究 sources

- **S1** — CatBoost maintainer docs, [Categorical features](https://catboost.ai/docs/en/features/categorical-features)：內建 category handling 與不要手動 one-hot 的警告。
- **S2** — CatBoost maintainer docs, [CatBoostClassifier API](https://catboost.ai/docs/en/concepts/python-reference_catboostclassifier) 與 [feature importance API](https://catboost.ai/docs/en/concepts/python-reference_catboost_get_feature_importance)：`cat_features`、`ShapValues` API。
- **S3** — CatBoost maintainer docs, [Parameter tuning](https://catboost.ai/docs/en/concepts/parameter-tuning)、[Performance settings](https://catboost.ai/docs/en/references/training-parameters/performance)、[ShapValues](https://catboost.ai/docs/en/concepts/shap-values)；另見原始論文 [CatBoost: unbiased boosting with categorical features](https://proceedings.neurips.cc/paper/2018/hash/14491b756b3a51daac41c24863285549-Abstract.html)。
- **S4** — scikit-learn maintainer docs, [Tuning the decision threshold](https://scikit-learn.org/stable/modules/classification_threshold.html)：probability vs decision、internal CV、cost scorer、threshold 不改 ROC/PR、不可同資料 train/tune。
- **S5** — scikit-learn maintainer docs, [`TunedThresholdClassifierCV`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TunedThresholdClassifierCV.html) 與 [`CalibratedClassifierCV`](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html)：CV threshold、prefit warning、sigmoid/isotonic 及小 calibration sample 的 isotonic overfit warning。
- **S6** — scikit-learn maintainer docs, [`StackingClassifier`](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.StackingClassifier.html)：OOF meta training 與 prefit overfit warning。
- **S7** — scikit-learn maintainer docs, [`VotingClassifier`](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.VotingClassifier.html)：well-calibrated classifiers 的 soft voting / weighted probability average。
- **S8** — EconML maintainer docs, [Doubly Robust Learning](https://www.pywhy.org/EconML/spec/estimation/dr.html)：`Y,T,X,W`、confounding、overlap、cross-fitting 與 CATE semantics。
- **S9** — EconML maintainer docs, [Interpretability](https://www.pywhy.org/EconML/spec/interpretability.html)：CATE `shap_values` 的效果異質性語義。
- **S10** — scikit-survival maintainer docs, [`Surv`](https://scikit-survival.readthedocs.io/en/stable/api/generated/sksurv.util.Surv.html)、[Evaluating survival models](https://scikit-survival.readthedocs.io/en/stable/user_guide/evaluating-survival-models.html)、[`RandomSurvivalForest`](https://scikit-survival.readthedocs.io/en/stable/api/generated/sksurv.ensemble.RandomSurvivalForest.html)：event/time/right-censoring、survival outputs 與 censored metrics。
- **S11** — Shafer & Vovk, 原始論文 [A Tutorial on Conformal Prediction](https://jmlr.org/papers/v9/shafer08a.html)：IID 下的 coverage 原理。
- **S12** — MAPIE upstream source docs, [Conformal prediction theory](https://github.com/scikit-learn-contrib/MAPIE/blob/master/doc/content/conformal-prediction/theory.md) 與 [classification theory](https://github.com/scikit-learn-contrib/MAPIE/blob/master/doc/content/conformal-prediction/classification.md)：exchangeability、未參與 fit 的 conformalization data、marginal 而非 conditional coverage。

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "僅產出一份繁體中文研究 Markdown；未修改任何 runtime、模型、依賴或 Grafana 檔案，且明確將實作範圍限制為後續 P0/P1 最小實驗。"
    },
    {
      "id": "criterion-2",
      "status": "satisfied",
      "evidence": "文件逐項列出現況 source-code 證據、官方／原始來源連結、資料與依賴前提、洩漏風險、分階段 gate、最小實驗矩陣與未驗證事項，可供獨立 reviewer 追溯。"
    }
  ],
  "changedFiles": [
    "docs/design/ml-methods-research.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "functions.read sandbox-analysis-mcp/ml_autoresearch.py sandbox-analysis-mcp/ml_preprocessing.py sandbox-analysis-mcp/ml_presentation.py docs/design/ml-multi-model-stability.md docs/design/ml-grafana-presentation.md sandbox-analysis-mcp/Dockerfile",
      "result": "passed",
      "summary": "已核對目前分類、校正、threshold、guards、presentation 與 image dependency 現況。"
    },
    {
      "command": "functions.web_search / functions.fetch_content / functions.source_check（CatBoost、scikit-learn、EconML、scikit-survival、conformal primary sources）",
      "result": "passed",
      "summary": "每個載重方法主張均以 maintainer docs、upstream source 或原始論文交叉核對；未將實證增益當作既成事實。"
    },
    {
      "command": "functions.read /home/timmypai/.pi/agent/sessions/--home-timmypai-apps-grafana--/subagent-artifacts/outputs/7b53e0ec-1047-4f24-838c-96034441165a/docs/design/ml-methods-research.md",
      "result": "passed",
      "summary": "artifact reread 成功；已確認繁體中文研究、來源表、實驗矩陣與 acceptance report 均存在。"
    }
  ],
  "validationOutput": [
    "研究結論：P0 先補校正／成本證據，P1 僅試 CatBoost challenger；stacking/conformal 延後；CATE/survival 需新資料契約。"
  ],
  "residualRisks": [
    "CatBoost 的實際 PR-AUC、wall-time 與 peak-memory 未實測，不應從文件推斷。",
    "3:1 成本是否已被業務核准，以及 treatment/time/censoring 資料是否存在，仍待 owner 確認。",
    "本子代理工具沒有 git shell；未執行 staging 動作，reviewer 仍應以 git diff --cached 做 workspace 級確認。"
  ],
  "noStagedFiles": true,
  "diffSummary": "新增一份方法研究與可驗證的分階段決策／實驗矩陣；無程式碼變更。",
  "reviewFindings": [
    "no blockers in research scope; 實作前需先處理 calibration/cost asset 與 owner 的成本資料決策。"
  ],
  "manualNotes": "此 artifact 由 runtime 指定輸出路徑保存；邏輯 repo 路徑為 docs/design/ml-methods-research.md。"
}
```
