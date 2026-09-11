# Report analysis coverage — TDD 實作紀錄

## 交付範圍

使用者核准 report MCP 公開介面上的 RED→GREEN。Main-only；本批只改本地原始碼、離線測試與本文，未部署、重啟、呼叫真模型、寫入 Grafana、安裝依賴、commit/push 或操作 Goal。

- `artifact-bridge-mcp/server.py`：`prepare_ml_report` 回傳 `report_context.analysis_coverage`；`compose_ml_dashboard` 重新核對保留的 provenance，阻擋已知缺口並提供分型回饋。
- `scripts/check-analysis-coverage.py`：從真實 `tools/call` report MCP 路徑測試，使用隔離的 host artifact fixtures；不 mock validator 或合成器。
- 沿用現有 report context、provenance、fact catalog、inspection receipts、error response；沒有新增工作流、控制器、資料庫或重試權限。

## 實際驗證什麼

目前 `analysis_contract` 是既有 ML 契約，不等於完整的自然語言決策問題。本批只檢查：

1. 報告與 host provenance 配對，保留的 plan SHA、frame ref/hash 格式有效；不讀取原始 frame，避免把既有結果重用授權擴張為新資料/運算授權。
2. 若存在可辨識的 ML 契約，執行紀錄必須是 `execute_ml_contract` 且有真正 boolean trusted flag。Profiling、generic Python 與自行宣稱 completed 都不能代替。
3. 既有 trusted presentation schema 的比較數值必須存在且為有效數值：regression 的 selected MAE／baseline holdout MAE；classification 的 selected／baseline accuracy。支援 canonical catalog 與既有 legacy fact paths。這些是既有產物欄位，不是替 LLM 選分析方法。
4. 合成報告必須引用上述可用比較 facts，可放在 thesis、section block、panel 或 view narrative；不固定報告排版。
5. 合成時重新讀取 provenance；與準備時的 coverage 不一致、身分無效或未授權時停止，不能悄悄降級後繼續。

`evidence_available` **不是**「業務問題已回答」或「科學結論成立」。`business_question_status` 明確保留為 `not_assessed`。未知/未記錄的需求不猜測，標示 `not_assessed`；這不是 PASS，也不強迫描述性問題跑 ML。

## 給 LLM 的失敗回饋

| 類型 | 行為 |
| --- | --- |
| `missing_analysis_evidence` | 保留成功 query/profile，只能經既有確認與預算 gates 補缺少的分析；換範圍須確認；不能重送不變的合成請求。 |
| `report_evidence` | 已有 trusted execution，卻缺匯出的比較 facts；檢查保留結果並修 trusted export，不為修報告重跑分析。沒有授權的修復能力就停止並揭露限制。 |
| `report_synthesis` | facts 已存在但未引用，回傳 `missing_fact_refs`；只改 synthesis。其他既有格式/inspection 問題也保留 report-only 修正。 |
| `report_identity` / `authorization` | 不可恢復的身分/來源錯誤；停止並檢查 refs/provenance，不建議重算或繞過權限。 |

`recoverable` 表示有修復可能，不授予重試/運算權限。Coverage 固定 `actions_granted=[]`、`automatic_retry=false`；沒有新增自動重試迴圈。無法补齊時可以回覆有根據的 partial findings，而不是捏造完成或強迫模型達到顯著/優於 baseline。

## TDD 證據

原始 RED/GREEN 與最終檢查紀錄在 `.scratch/analysis-coverage/`：

- `red-prepare.log`：報告準備隱藏「planned ML vs profiling」缺口。
- `red-compose.log`：只有 profile 仍可合成宣稱完成的比較報告。
- `red-facts.log`：只有 trusted executor 身分就被當成比較證據。
- `red-repair-kind.log`：缺匯出 facts 錯誤要求補分析，而非修 evidence export。
- `red-authorization.log`：權限錯誤原本被回覆為可恢復的 synthesis 修正。
- `red-unknown-task.log`：未知契約被錯當成既有分類比較。
- `red-citations.log`：比較 facts 存在卻未在報告引用，仍可交付。
- `red-stale-coverage.log`：準備後 provenance 缺失可令 coverage 靜默降級；filesystem fault 僅發生在測試自己的暫存資料。正常 ArtifactStore 已禁止覆寫 immutable provenance，未放寬此規則。
- 同名 `green-*.log` 與最終 `check-analysis-coverage.log`：以上切片通過，並覆蓋 selected 比 baseline 差、描述性報告、generic/self-declared 偽成功、不同引用位置、跨 org/user/session 與錯配來源。

Fixtures 驗證的是公開 report 工具行為與 metadata/evidence contract，**不是**實際的人類確認或一次真的 ML 訓練。

## 既有 self-check 修復

廣泛 Bridge self-check 重現已知舊 fixture 失敗（`all analysis images must use asko11y-plotly-panel`）。只把原本 text/HTML 圖片 fixture 改成既有 Plotly plugin 的明示 `renderMode=image` + `fallbackUrl`，保留 resolver 的所有安全拒絕條件。RED：`bridge-self-check.log`；GREEN：`bridge-self-check-green.log`。

## 最終離線檢查

- 新 coverage check、Bridge report synthesis、Bridge Plotly、computed report source、canonical report manifest、ML presentation manifest、no-hardcoded-report、security-negative checks。
- Bridge self-check；17 patches 依 installer 順序重建並比對。沒有執行 installer。
- Python LSP、session lens、`git diff --check`、no-staged。
- 最終結果與來源 SHA256 保存於同一 scratch 目錄；此清單不是部署或 browser 驗收。

## 第二批：缺失來源 fail-closed 與 partial report

使用者要求繼續實作，沿用同一組 report MCP 公開介面。原始 RED/GREEN 與本批 final source fingerprints 在 `.scratch/analysis-partial/`；上一批 SHA/log 保留為歷史證據，不改寫。

- `red-missing-receipt.log` → `green-missing-receipt.log`：刪失 provenance 後重新 prepare，原本會降級成 `not_assessed`。現在 canonical report 缺必要來源紀錄就停止；舊 trusted execution 必須先經 host re-export 產生 fresh refs，generic/legacy index 不可由 Bridge 直接回填，也不能以重做 prepare 或 partial 模式繞過。缺少契約與缺少來源紀錄是兩件不同的事。
- `red-partial-report.log` → `green-partial-report.log`：新增 compose 的明示 `delivery_status="partial"`，預設仍為 `standard`（不是 complete）。保留 coverage 的 incomplete/not_assessed 狀態與所有缺口。
- `ml_dashboard_compositor.py` 沿用既有 narrative intro，加入 host 產生的 `Partial report — analysis is not complete` 提示、已知缺口及尚未評估業務完成的聲明；不另建固定分析流程或圖表樣板。notice 的文字經 HTML escaping，使用既有允許的 `div role="note"`；沒有擴大 HTML allowlist。
- Partial 只解除已知 evidence-gap 的完整報告阻擋；不解除來源配對、scope、inspection、數值/fact validation 或可用比較 facts 的引用要求。權限/來源損毀仍停止。沒有 LLM 可覆寫的 notice 參數。
- Dashboard 仍帶 `ask-o11y-preview`，新增 `askO11yDeliveryStatus=partial` **僅為顯示資訊，不是權限或可信完成收據**；報告 API 不自行呼叫 Grafana writer 或發布。限制提示置於既有 intro 開頭並增加所需高度，沒有修改原始資料或刪減圖表。
- 原 Plotly canonical positive fixture 補上與成功 Sandbox 輸出一致的 paired provenance（profile、非 trusted ML），而不是放寬缺來源的拒絕條件。沒有將歷史無來源報告推定為 approved；若真實歷史報告缺來源，需調查，不能假造 provenance。

本批 source/offline：11 個相關 Python checks、Bridge self-check、17-patch fidelity、四檔 LSP、session lens、diff/no-staged通過。新增防回歸包含 partial 下的權限拒絕、缺 inspection、偽造 facts、漏引用、無效 delivery 值、覆寫 notice 企圖、缺來源，以及原 Preview 狀態和配置不重疊。

**未部署，未跑真 LLM/瀏覽器。** HTML/JSON 的 source checks 不代表實際 Grafana 畫面已驗收；metadata/提示也不代表其他 writer 路徑能強制保留它們。缺來源 now-fail-closed 是有意的行為修正，不會自動刪除或回填歷史資料。

## 第三批：objective 對齊與基準比較證據

使用者要求繼續實作。沿用 report prepare/compose seam 做 RED→GREEN，紀錄於 `.scratch/analysis-objective/`：

- `red-objective.log` → `green-objective.log`：classification 契約要求 ROC AUC 時，accuracy facts 不再被視為完成。
- `red-baseline.log` → `green-baseline.log`：實際執行同一個 constant-negative baseline 的 accuracy、ROC AUC、PR AUC 計算；沒有沿用只報 accuracy 的舊輸出。
- `red-planned-objective.log` → `green-planned-objective.log`：Planner/host side 的 effective objective 透過 validation result 和 Sandbox provenance 保留到 report；不是由 report 內容猜測。
- `red-invalid-objective.log` → `green-invalid-objective.log`：錯誤/布林 objective 不會靜默 fallback。

具體變更：

- `sandbox-analysis-mcp/ml_autoresearch.py` 的 `run_multi_model_comparison` 對同一 holdout 回傳 no-fit constant-negative baseline 的 `accuracy`、`roc_auc`、`pr_auc`；空資料等 invalid input fail closed。
- `sandbox-analysis-mcp/server.py` 將既有 `baseline_metrics` 輸出改用 trusted comparison 的完整基準；保存 `planned_objective` provenance。沒有新增方法、改 baseline、重跑 holdout 或改選模門檻。
- `artifact-bridge-mcp/server.py` 只接受 objective 對應 facts；effective objective 優先使用 host-retained planned value，與 analysis contract 衝突或缺失時停止/回饋，未知 objective 不猜。
- `scripts/check-multi-model-verdict-gate.py` 的控制流 fixture 改用有效二元標籤，因 baseline 計算現在正確拒絕空 holdout；沒有降低該測試的 selection/holdout gate。

現有 accuracy/ROC AUC/PR AUC facts 存在且被引用，只代表該比較證據可交付；不代表模型較好，也不代表業務問題、因果或營運安全已完成。selected 輸給 baseline 仍可被報告；本批沒有引入「必須改善」閾值。

本批最終：14 相關 Python checks、Sandbox/Bridge self-check、17-patch fidelity、py_compile、session lens、diff/no-stage 通過；Bridge/compositor/Sandbox server/check files 的 LSP 無 findings，`ml_autoresearch.py` 的 LSP 逾時，未把逾時當成 clean，已由 py_compile、真實 sklearn fixture 與完整回歸檢查補足機械證據。未部署、未建 image、未啟服務、未做真 LLM/browser 或獨立 review。

## 第四批：classification holdout 不確定性證據

使用者要求繼續實作；本批仍是 report/可信執行切片，RED/GREEN 與 source fingerprints 在 `.scratch/analysis-uncertainty/`：

- `red.log`：trusted classification comparison 沒有 uncertainty metadata 或 metric intervals。
- `green-source.log`、`final-coverage.log`：同一鎖定 holdout 的 selected 與 constant-negative baseline 都輸出 accuracy、ROC AUC、PR AUC 的 95% nonparametric bootstrap interval；使用獨立 seed，未用 interval 選模型或改 threshold。
- bootstrap 僅重抽固定 holdout 的已產生 predictions/probabilities；不重新 fit、重新校準或重新挑模型。metadata 明確標記 model-selection、calibration、holdout 限制，不宣稱因果或未來表現。
- `build_report_source` 對 trusted uncertainty summary 保留 samples/confidence numeric facts，並在 canonical conclusion 明示 fixed-holdout bootstrap 限制；錯誤 uncertainty metadata、少於100或超過10000 resamples fail closed。interval facts 經 source→manifest→catalog 驗證。
- `scripts/check-ml-regression.py` 的舊 route fixture 補合法最小 Plotly output，讓它測 template/route 而非被既有 Plotly-only 契約的空 output 擋住；沒有放寬 production output gate。

不確定性是估計精度資訊，不是分析完成、顯著性、因果或部署批准。selected 輸給 baseline、interval 很寬或 bootstrap 不足都不能被改寫成成功/失敗結論。

本批 source/offline 最終檢查、限制與 LSP 逾時以 `.scratch/analysis-uncertainty/checks.txt` 和 `final-coverage.log` 為準；未部署、未建 image、未啟服務、未做真 LLM/browser 或獨立 review。

補充：迴歸比較已沿用同一 fixed-holdout bootstrap contract，selected 與 Dummy baseline 都保留 MAE interval、confidence/samples 與 model-selection/calibration/holdout/multiple-comparison limitations；metric delta 是 descriptive difference，不是 standardized effect size。classification/迴歸 trusted manifests 也保留 selection/holdout separation、training-only preprocessing、independence/causal/multiplicity 未驗證的 bounded boolean guards，不能把 false/unknown guard 解讀成安全或因果結論。

## 第五批：可用不確定性必須進入 synthesis

本批以第四批已產生的 classification uncertainty facts 做最小 gate，先 RED 再 GREEN：

- `red-citation-gate.log`：有 interval 與 confidence/samples facts 時，原 compose 只要求 objective point facts，可能漏掉區間。
- `_analysis_coverage` 現在把 canonical classification interval refs 與 uncertainty metadata 識別為 `uncertainty.status="available"`；samples/confidence 或 interval 不完整時不宣稱可用。
- `compose_ml_dashboard` 在 synthesis 缺少可用 interval refs 時回傳 `report_synthesis` repair，要求只修改引用並披露 fixed-holdout 限制；不重跑 query/ML。補齊 point 與 interval refs 後才可交付。
- 舊 report/沒有 uncertainty metadata 的 fixture 不被猜測成有區間；generic Python、profiling、executor 身分也不能建立此 gate。

`green-citation-gate.log` 與完整回歸驗證顯示 gate 只強制已存在且已被 trusted source 保留的區間，沒有新增「模型必須勝過 baseline」或固定分析流程。未部署、未建 image、未啟服務、未做真 LLM/browser 或獨立 review。

## 第六批：business question 與 result lineage 綁定

本批先以 `.scratch/analysis-business-binding-red.log` 驗證舊行為：trusted comparison 即使有指標，也可能只有 `business_question_status="not_assessed"`。現在：

- `data-query-planner.plan_query` 接受 bounded `business_question`，將精確問題寫入 immutable query plan、plan provenance 與 plan hash；WFERP legacy plan 也保留原始 bounded prompt 作為問題 lineage。
- Sandbox `read_plan_contract`／trusted executor 只從 retained plan 讀取問題，將它寫入 server-owned `sandbox-provenance`；trusted ML template 使用該值產生 report purpose。LLM 後續不能用 metric、chart title 或 synthesis prose 覆寫。
- Artifact Bridge 只在 report purpose 與 retained question 精確相等，且 plan hash、frame ref/hash、execution、report manifest、provenance 仍配對時，標示 `business_question_status="assessed"`，並回傳 bounded lineage metadata。缺問題變成 `BUSINESS_QUESTION_NOT_RECORDED`；不一致直接 fail closed。
- 新增 planner、executor、report prepare/compose 正反例：問題可追溯、缺失不猜、report purpose mismatch 停止、unsafe/過長問題拒絕。18-patch installer fidelity 保留 Go prompt 的 business_question 指引；沒有執行 installer。

`.scratch/analysis-business-binding-*` 的 planner、executor、coverage、Go prompt 與完整相關回歸通過；這只證明 host lineage 與 source contract，不證明使用者真的確認了自然語言問題，也不代表 business question 已被科學或營運證據回答。未部署、未建 image、未啟服務、未做真 LLM/browser 或獨立 review。

## 明確未完成

`TODO-d85ea8c4` 仍為 open，不能把本切片當成完整需求 validator：

- 尚未將任意自然語言業務需求結構化並綁定「目前這次」已確認版本；現在只能核對報告保留的原始 ML 契約。
- 不驗證所有候選模型、primary objective 選擇、混雜調整、統計假設、因果或操作安全性；classification accuracy 存在不等於其他 objective 已被充分比較。
- 不理解任意 narrative 的真假；正確引用也可能伴隨錯誤解讀。
- 本批 gate 在 report prepare/compose 路徑，**不是全域 Dashboard writer / 所有手工 binding 路徑的完成保證**。替代路徑與最終交付狀態需後續核對，不能以此宣稱已堵住所有旁路。
- Partial Dashboard 已有 source/offline 支援；本批新增 bounded `reexport_trusted_report`，只接受 host provenance 綁定的既有 trusted profile/ML execution，產生 fresh execution/provenance/report-manifest refs，不重跑 Python。缺可信來源時仍停止，不假裝已有修復能力。
- 未實測 LLM 收到回饋後能否正確修復/停止、最新 runtime、Grafana save/readback/browser、新手理解程度；独立 review/security gate 仍未具備，沒有因 main-only 而取消。
