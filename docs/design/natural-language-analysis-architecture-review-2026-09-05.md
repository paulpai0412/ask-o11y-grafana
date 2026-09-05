# 自然語言資料分析與 ML 平台架構審查

日期：2026-09-05。審查者：主 agent；未啟動新的 subagent。

## 結論與範圍

**目前是已有真實資料執行能力的受限分析原型，尚不能驗收為通用、無業務 hardcode、全圖片經 Plotly plugin 的自然語言分析平台。**

不建議重寫整套系統。保留 Grafana 唯一 datasource execution boundary、Planner 的 plan contract、Sandbox 隔離、Artifact Bridge 與唯一 dashboard writer；修正各邊界的權威與一致性。

本次核對目前工作樹、`.scratch/ask-o11y-build` 原始碼、build patch 順序，以及 Grafana API 中的 Power dashboard。執行了本地合成反例，沒有重新訓練模型、修改服務、修改 production code 或重開 TODO。沒有執行完整 browser E2E，也沒有證明部署 binary 與每份來源完全一致。專案不在現有 code graph 索引中，因此改用原始碼定位。

證據：

- 可重跑探針：`.scratch/reviews/main-agent-architecture-probes.py`
- 探針結果：`.scratch/reviews/main-agent-architecture-probes.json`
- Grafana API panel 清單：`.scratch/reviews/observed-power-panels.json`
- 命令：`.venv/bin/python .scratch/reviews/main-agent-architecture-probes.py`

探針使用合成資料驗證個別模組行为，不是實際 ML/E2E 成功證據。以下來源行號對應審查時工作樹。

## 已有且值得保留的架構

- Ask O11y 依 capability catalog 選工具、使用 advisory skills；不是單一中央固定 ML DAG。
- Query Planner 編譯並驗證分析／查詢契約，Grafana Query 接受 opaque `plan_ref`。
- `execute_ml_contract` 使用 host-owned 模板，優於讓 LLM 任意寫標準訓練程式。
- Sandbox network-denied 執行、輸入／輸出上限、org/user artifact 授權、provenance 已有基礎。
- Report synthesis 與數值計算分離；Bridge 解析 refs，Grafana writer 負責寫入。
- Plotly plugin 已有 `Plotly.react`、resize、sanitized figure 與 PNG 顯示能力。
- 回歸單一 feature set 的選模以 CV 為準，有 Dummy gate；profile 會遍歷收到的完整 frame。

這些是良好基礎，但「有 schema、hash 或測試通過」不等於資料關聯、語義和展示契約均已閉合。

## 缺口總覽

P0：正式多使用者／可信 ML 上線前阻擋。P1：核心產品需求未滿足。P2：擴展與交付能力。

| ID | 優先級 | 實際缺口 | 驗證程度 |
| --- | --- | --- | --- |
| A | P1 | PNG-only 圖表繞過 Plotly plugin | 原始碼 + live Grafana API |
| B | P1 | 欄名、大小、欄位順序成為 ML 語義權威 | 本地反例重現 |
| C | P0 | 分類契約可接受時間切分，executor 卻固定 random stratified split | validator 反例 + executor source |
| D | P1 | target direction 與 metric direction 在測試中混淆 | source |
| E | P0 | 分類多模型用 holdout 衍生 verdict 篩候選，再選模 | source，非實際重訓 |
| F | P1 | 多模型、預算、資源與能力宣告不一致 | source |
| G | P0 | session 授權未貫穿 artifact／Sandbox；MCP 共享 session 有競態風險 | scope 反例 + concurrency source |
| H | P0 | contract_ref 與 frame_ref 缺少直接同源綁定 | source，未做 live 混搭 |
| I | P1 | full-data 與 trusted-ML 保證未涵蓋完整執行鏈 | source |
| J | P1 | transport retry 沒有 effect-aware 去重契約 | source，未注入實際斷線 |
| K | P0 | recovery check 可被沒有真實執行證據的摘要判成成功 | 本地反例重現 |
| L | P2 | 支援範圍及交付驗收尚非通用平台 | source／文件比對 |

### A. 所有圖片由 Plotly plugin 呈現：目前不符合

**Evidence**

- `ml_dashboard_compositor.py:184-207`：有 `plotly_index` 才選 `asko11y-plotly-panel`；否則回傳 `type="text"` + `<img>`。
- `ml_dashboard_contract.py:133,176-183`：接受 text evidence panel；plugin 分支反而要求必須有 Plotly bindings，未定義合法 PNG-only plugin mode。
- `sandbox-analysis-mcp/server.py:824-856`：回歸輸出 Matplotlib PNG。`sandbox-analysis-mcp/data_profile.py:396-506`：profile 同样輸出 PNG。
- `.scratch/ask-o11y-build/pkg/agent/loop.go:38` 的 nudge 仍指示使用 image/text panels。
- Live dashboard `power-reg-df84893` 的 model comparison、feature-set comparison、candidate-settings 三張圖，API 讀回均為 `text` 且含 `<img>`。

**Finding → Path**

這不是 plugin 未安裝，而是 compositor、validator、prompt 三者仍允許舊路徑。僅補 Plotly figure 產生器，不能保證所有圖片的呈現邊界。

最小方案：所有帶 artifact 圖片的 evidence panel 一律使用 `asko11y-plotly-panel`；plugin 支援明確 `plotly` 與 `image` 模式。PNG-only 在 plugin 內顯示原圖片，不偽稱互動圖；純敘述可以繼續用 Text panel。模式以 artifact MIME/capability 決定，不依 dataset/model 名稱分支。

同步修 compositor、dashboard contract、writer gate、prompt。拒絕 Text panel 的圖片／背景圖片等替代路徑；不把 PNG 包裝成偽造的 Plotly data。

`module.tsx:157-190` 已有 image fallback 可復用，但目前單 view 不顯示 panel narrative，且 fallback 不呈現 per-view narratives；image mode 要保留文字、限制與 evidence。`module.tsx:247-265` 的 `failed` state 未隨新 figure 重置，render error 後更新資料可能一直停在 fallback，也需要回歸測試。

驗收：profile、regression、classification、PNG-only、自訂圖片的 artifact panel 全為 plugin；瀏覽器實際驗證 PNG/Plotly、字型、resize、敘述、錯誤與更新恢復。API 200 不算 browser acceptance。

### B. Ontology hints 仍在 hardcode 分析語義

**Evidence**

- `upload_semantics.py:17,44-57` 僅讀前 1,000 rows 產生 hints。
- `:61-111` 以欄名關鍵字、數值唯一率和 magnitude 推斷角色；`用煤量/用量/consumption/flow` 特別豁免。
- `:111` 把最後一個低 cardinality 欄位標成 target candidate。
- `:336` 分類 target 必須已是 target candidate；推測因此成為拒絕使用者選擇的 gate。
- `sandbox-analysis-mcp/server.py:867` 通用 regression manifest 的說明固定寫入「用煤量」。

探針：交換 `outcome, segment` 欄位順序，target role 跟著改變。相同 40 個數值，`reading` 被當 identifier，`consumption`／`用煤量` 被當 feature。

**Finding → Path**

不能以更多例外欄名修正。拆開 observed facts、semantic hypotheses、approved analysis contract：dtype/值域/缺失率是 facts；identifier/leakage/敏感欄位名稱提示是待確認風險；target、時間意義、特徵可用時點由使用者意圖與 lineage 證據確認。硬性拒絕應來自已核准的規則，不是「最後一欄」或 keyword 猜測。

先讓明確指定的合法 target 不受 column-order 阻擋；刪除領域豁免與「用煤量」固定文案。敏感／leakage 治理不能直接取消，改為需要 evidence/approval 的 unresolved 狀態。

驗收：同資料欄位重排、等價重新命名、不同領域及前後段分布改變，不得無故改變已確認分析契約。完整資料要求下，前 1,000 rows hints 不可作全體常數／值域權威。

### C. 分類切分契約未被 executor 實踐

**Evidence**

- `upload_semantics.py:357-359` 只檢查 training-only scope，未限制 split kind。
- 探針：含 `chronological_holdout` 的分類 contract 通過此 validator。
- `sandbox-analysis-mcp/server.py:1044` 無條件使用 `train_test_split(... stratify=y)`；`ml_autoresearch.py:430` 使用 shuffled `StratifiedKFold`。

**Finding → Path**

如果使用者要求時間或 entity 隔離，契約可能被接受而實際 random split，造成時間／群組洩漏。這比「不支援」更嚴重。

立即讓不支援的 split fail closed。接著以同一受信任 split 實作產生 outer split 和 CV indices；分類／回歸共同使用，manifest 記錄實際 indices 的 digest、group overlap、time range，而不是只抄 `split.kind`。

驗收：未來時間不能進訓練、同 entity 不能跨邊界、executor 不支援就必須在執行前拒絕。不能默默換 random split。

### D. `target_direction` 不是 MAE 的方向

**Evidence**

- `ml_regression.py:148,242`：`target_direction` 決定 candidate `predicted_target` 由大到小或由小到大排序。
- CV scorer 已固定 `refit="mae"`，選模使用最小 `cv_mae_mean`，和 target direction 不同。
- `scripts/run-ask-o11y-vestas-e2e.py:175,185,202-203` 卻強制 `MAE + target_direction=minimize + budget=5`。

**Finding → Path**

先前把 minimize 描述成「安全的 regression objective」不精確。發電量若要最大化，可以同時是 MAE minimize、Power maximize；純預測根本不需要指定業務最佳化方向。

使用 `metric`（含 scorer 定義的方向）與 optional `optimization.direction` 分離；最佳化方向只能由使用者確認的業務目標決定。沒有最佳化意圖，不能因回歸 task 自動產生 candidate-setting 目標。

驗收：預測誤差最小、產出最大可同時成立；沒有最佳化意圖不要求方向、不偷偷啟用 constrained search。本次未修改既有測試或結果。

### E. 分類多模型的 holdout 已參與 model selection

**Evidence**

- `ml_autoresearch.py:57-74,467`：verdict 使用 holdout score、gap/drift 等。
- `:518-542`：每個 candidate 都完整評估 holdout，再把 `verdict == accepted` 的模型列為 eligible，最後才以 CV 選 best。

**Finding → Path**

不是單純「CV 選模後只看一次 holdout」：即使最後 sort key 是 CV，eligible set 已受 holdout 影響。`holdout_used_during_search=False` 只描述單個 SearchCV，不足以涵蓋整個 comparison。

拆為所有候選在 train/CV 上比較 → 鎖定 winner → winner+baseline 做一次 holdout release gate。未過 gate就回報不通過，不以同一 holdout 改選候補模型。

回歸單 feature set 選模相對正確；但 `server.py:795-808` 對每個 feature set 呼叫完整評估，之後才選 feature set，且 manifest 只回報 selected result 的 `holdout_evaluations=1`。不應把這稱為整次研究只消費一次 holdout。

驗收：改動 holdout labels 不得改變 winner identity；只能改變 release verdict。feature-set/model 搜尋全完成後才揭露 final test，記錄研究層級的評估次數。

### F. 多模型能力與資源治理未統一

**Evidence**

- `ml_autoresearch.py:513-518` 明確 sequential；`ml_regression.py:315-331` 明確 serial、`n_jobs=1`。
- trusted classification template `server.py:1048` 只呼叫 single-kind helper，沒有接通 multi-model comparison。
- `ml_autoresearch.py:516`、`ml_regression.py:312` 每種 budget 至少 1；當 budget 小於種類數，總 trial 可能超出宣告 budget。feature sets 另各使用完整 budget。
- 分類內部 estimator/search 有 `n_jobs=-1`；不能直接在外面加 parallel 而忽略 nested workers。

**Finding → Path**

前面「平行調參已完成」應撤回；有比較函式不等於 runtime 已統一接通。

先使 executor 按核准候選清單執行，明確 global search budget、baseline 成本、feature-set 成本與實際完成 trial。候選不夠分配時要求縮小清單／調整預算，不偷偷超支。若需要並行，使用受 sandbox CPU/RAM 約束的 bounded workers，內層 thread 限制為 1；不用新增中央工作流引擎。

驗收：candidate 清單與執行 receipt 一致；所有 trial 可加總；低 budget 不超支；記憶體受控。平行不是平台可用性的前提，正確與可控優先。

### G. Session 授權是局部的，共享 MCP connection 有額外風險

**Evidence**

- `loop.go:676-684`：upload ID 綁定只處理 inspect_dataset；`:757-759` 只對 grafana-query 工具注入 session。
- `sandbox-analysis-mcp/server.py:166-194` 的 context 只保留 org/user；`:335` 傳給 inspect_upload 的 session 因此可為 None。
- `artifact_store.py:62-78,180-188` 保存與驗證 org/user，不保存 session。探針確認同 user 的 session B 可以讀 session A 的 artifact。
- MCP client `client.go:258-321` 重新設定共享 `c.session` 後釋放鎖；`:595-610` 隨後才重新讀取 `c.session`。Proxy `proxy.go:153-180` 共用 client，未包住整次 connect+call。

**Finding → Path**

Org/user 隔離不是嚴格 session 隔離。跨對話 revision 工具有意設計成 user scope，不能一概當 bug；但它與「foreign session 一律 fail closed」要求不一致，需要明確權限模型。

MCP 另有 source-level interleaving 風險：A reconnect 完成，B 替換 session，A 讀到 B connection；也可能導致彼此關閉連線。尚未做 live 並行重現，不能聲稱已發生跨租戶洩漏，但應列為正式多使用者 release blocker。

最小方案：端到端傳 immutable actor/session context；artifact 明確標記 session scope 或 explicit reusable user scope。reuse 必須經受信任操作授予，不從自由文字拿 ref 自動擴權。MCP 呼叫使用當次建立的 local session，或按完整 actor/session 分隔；短期可序列化整個 connect+call 作保守修補。

驗收：同 org 不同 user、同 user 不同 session、stale/deleted upload、明確授權 reuse，以及強制 A/B 交錯 reconnect 的並行測試。

### H. 分別驗證 plan 和 frame，不等於驗證兩者是同一次計畫

**Evidence**

- `server.py:1157-1171` 從 `contract_ref` 讀計畫 A、產生 template，再只把 frame_ref 與 template 傳给 execute_python_analysis。
- `:1198-1201` execute_python_analysis 從 frame 的 run 重新讀計畫 B，用 B 的 validity/semantic contract；未直接比較 A/B ref 或 plan hash。
- `capture.py:62-105` 讀 frame/validity_rules，沒有用 semantic_contract 檢驗 generated code 是否等於計畫 A。

**Finding → Path**

同使用者持有兩個合法但不同計畫，若欄位相容，有混搭 template 與資料／provenance 的風險。此項為 source 結論，尚未 live 混搭測試。

直接驗證 frame provenance 的 plan digest 等於 contract_ref digest，連同 source hash、selection/filter、split、seed／execution settings 綁定。不能用「最近一次 plan/frame」補值取代 lineage proof。

驗收：兩個同 schema、不同資料或 filters 的合法 refs 互換，必須在 sandbox 啟動前拒絕。

### I. Full-data 與 trusted ML 保證尚非 end-to-end invariant

**Evidence**

- Planner `server.py:147-152,278` 主要使用 caller 提供的 min/max rows；完整來源筆數並非通用 exact-row-count gate。
- regression template `sandbox-analysis-mcp/server.py:777` 另行 dropna；classification `:1028-1039` 有 target coercion/drop/mapping。這些不等於 capture 的前置 validity audit。
- `profile_columns` 的 full_data 是「收到的 frame 全部」；不證明 datasource 沒漏資料。
- `execute_python_analysis` 仍是可用工具；`capture.py` 會執行 Python，input audit 只核對前置資料，不證明任意程式未抽樣／未替換模型。
- `server.py:1085-1086` SHAP 使用最多 400 rows。這不是 training sample，但必須與「全量解釋」宣稱區分。

**Finding → Path**

應有 source_rows → queried_rows → eligible_rows → train/test rows → explained_rows 的執行 receipt，所有差異有核准理由。使用者要求不排除資料時，無效 target 應先報告／要求處理決策，不静默 dropna。

標準 ML 結果只可由 trusted executor 取得 verified-ML 狀態。任意 Python 仍可做探索，但輸出不得自動繼承同等治理保證；不要靠掃描函式名稱就宣称算術正確。

驗收：來源完整度、null target、invalid timestamp、圖表 aggregate、解釋子集都能追溯；不得把視覺聚合回灌 model input。大資料超上限要明確報告或提供完整分批能力，不自行縮小 query。

### J. 原生 recovery 需要 effect-aware transport，不需要另一個 Recovery Engine

**Evidence**

- `client.go:536-574` 對 transport error 自動 retry；`:626-666` 又有 reconnect retry。未在這兩層依 read/write/compute idempotency 決定。
- `sandbox-analysis-mcp/server.py:1207-1227` 執行後建立新的 output run；此入口未見重複請求收據去重。

**Finding → Path**

如果服務已完成計算／寫入，但回應丟失，盲目重送可能重訓、重算或重寫。這不是「LLM 原生自我修正」能獨自解決。

保留 LLM 讀 recoverable validator 原文、只修 synthesis 的行為。transport 層根據 tool effects：read 可 retry，compute/write 以 request key 查收據後恢復；無法判定結果時回報 indeterminate，不當作明確失敗重送。

驗收：在 response 回傳前斷線，計算／writer 執行次數仍為 1，重連取得同一 receipt/ref。不得重跑已成功 query/ML。

### K. Recovery 綠燈不足以證明真實端到端閉環

**Evidence**

- `check-native-recovery-e2e.py:18` 只檢查 upload_ prefix 與 rows=10000；`:28-45` 信任摘要中空的 unrecovered list，跨所有 phases 數同名工具兩次。
- 本地反例：`upload_not_a_valid_id`、沒有 run/session、沒有成功 receipt，只有兩個工具名與 recoverable=true，checker 仍輸出 native_llm_recovery=true。
- `run-ask-o11y-vestas-e2e.py:246-255` 部分 validation flags 為直接寫入 True，不是從 raw receipts 推導。
- 此對話前段曾把獨立 strict-ML continuation 與舊 profile/recovery 摘要合併到同一 evidence 檔；可保留為分段測試記錄，不能稱為一次 fresh-session 全流程通過。

**Finding → Path**

不是說真實 recovery 沒發生，而是 checker 無法證明其完整性。工具成功、Grafana API 200、真實模型有效是三種不同驗收。

保存 immutable 原始 run/event log；summary 只由它推導。驗證 session/run/tool-call ID、時間序、錯誤→修正參數→成功結果、相同 report context、無重跑 query/ML、dashboard UID provenance。不同 continuation 清楚標示父子關係，不覆寫成同一次執行。

驗收：上面的合成摘要必須被拒絕；舊 run 拼接、缺成功事件、換 dataset/ref、僅重複同名工具均不可通過。

### L. 通用能力及 release 尚需明確邊界

- structured executor 主要是 binary classification 與 chronological/grouped regression；分類多模型與一致 split 尚未接通。Forecasting、clustering、multiclass 等不能因為任意 Python 可執行就宣稱是已治理能力。
- `scripts/build-install-ask-o11y.sh:8-25` 有 pin 上游 commit，這是優點；但靠多份 patch 及 scratch build 組裝，`:26-49` build/typecheck/health 並不包含完整 contract、Go、browser gate。
- 本次 `git status --short` 有 77 entries；有未追蹤核心模組。可運作工作樹不是可重現 release。
- `docs/design/ml-plotly-first-charts.md` 有歷史 Telco Chromium 結果；這不涵蓋目前 profile/regression 的 PNG-only 路徑，也不能直接說完全沒有 browser 證據。

方案：從實際安裝的 executor adapters 匯出 capabilities、supported split/metrics、依賴 availability 與 limits。未知 task 明確說不支援／需要確認，不悄悄換 task。建立 clean-checkout patch apply → unit/contract → image/plugin build → 小型 real-flow/browser smoke 的 release gate，記錄 commit、patch、image、plugin hashes。

批次預測、模型下載／保存、監測是若要把訓練成果重用才需要的下一階段；本輪不要直接引入完整 MLOps 平台。

## 建議目標架構：自由組合，但強制驗證邊界

不是固定流程 DAG，而是下列元件間每次操作都遵守契約：

1. **會話與意圖**：trusted attachment、使用者問題、scope、confirmation revision。
2. **Capabilities + evidence**：實際 datasource、schema、profile、ontology facts；不靠欄名猜測當權威。
3. **分析契約**：LLM 提議 task/target/features/split/metric/視覺需求；host 驗證可行性、授權與歧義；需要時請使用者確認。
4. **資料執行**：Planner → Grafana Query；輸出綁定 source/plan hash 的 frame。
5. **計算**：trusted adapters 動態執行核准 task；列出真實 rows、trials、split、metrics、release verdict。
6. **報告**：LLM 依完整 bounded facts/views 決定論點、選圖與順序；不是固定十四張圖，也不把所有 task 都升級成 ML。
7. **展示**：所有圖片／圖表 evidence 由 Plotly plugin 呈現；image/plotly 模式來自 artifact capability；敘述可用 Text。
8. **寫入與復原**：唯一 writer + versioned approval + idempotency receipt；LLM 修語義錯誤，不重跑成功 effects。

同一聊天可只做描述性圖表、先問資料問題、比較群組、要求預測、修改報告或重用已核准結果；不要求每次走完全部元件。

### 「不可 hardcode」的可執行定義

禁止 dataset/欄名例外、keyword 選 target、固定業務方向、固定模型與圖表流程，以及為單案例補值。

允許且必須保留版本化 tool schemas、已實作演算法 allowlist、型別驗證、安全拒絕規則、資源上限、scorer 定義與 deterministic 計算。把這些全部交給 LLM 任意決定，反而失去安全與可重現性。

## 建議落地順序與验收

| 階段 | 改動 | 必須留下的驗收 |
| --- | --- | --- |
| 1：校正真實狀態 | 修 evidence checker；區分歷史/continuation/fresh E2E | 合成假摘要及跨 run 拼接拒絕 |
| 2：守住權威 | split 語義、plan/frame lineage、session scope、MCP identity isolation、effect retry | 時間/group 零洩漏、混搭 refs 拒絕、並行隔離、斷線不重算 |
| 3：達到展示要求 | 統一 image/plotly plugin contract，清除 Text-image 路徑 | 所有任務圖片 browser 驗證；無需重新計算 |
| 4：去業務 hardcode | hints 降級、意圖 target、metric/optimization 分離、全量 row receipt | 欄位重排/重新命名/未知領域、不省略資料 |
| 5：完整可用 ML | 候選 CV 鎖定→final holdout、全域 budget、能力宣告 | holdout labels 不影響 winner、budget 真實、unsupported fail closed |
| 6：可交付版本 | clean-build、artifact/version manifest、release smoke | 新環境可重現；不是依賴 scratch 現況 |

不新增中央 DAG、不新增自訂 AutoML 框架、不以更多 keyword 分支修補。先補上述小而關鍵的契約缺口，再擴充 ML task 種類。

## 對先前進度說明的更正

- TODO 已依使用者要求全部關閉，仍維持關閉；不代表功能完成。
- 多模型比較存在，但「多演算法平行調參完成」不成立。
- session attachment 已有實作，但「任意 cross-session runtime path 全隔離」未成立。
- MAE minimize 不代表業務 target 必須 minimize；之前對此的強制測試不能當通用正確性證據。
- 最新 Power dashboard 可看，但目前其圖片未經 Plotly plugin。
- 真實個別流程／recovery 有觀察證據；不能把合併摘要當成新版本一次性完整 E2E acceptance。
