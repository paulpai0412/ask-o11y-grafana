# 自然語言資料分析平台：架構修訂

日期：2026-09-05。狀態：設計已採納，runtime 修補與 release 驗收待完成。

依據：[主 agent 架構審查](natural-language-analysis-architecture-review-2026-09-05.md)。本文件定義新目標與驗收，不宣稱現有程式已符合。對本次修訂範圍內與舊設計衝突的規則，以本文件為準；舊 E2E 結果保留為歷史紀錄。

本地 Epic：`TODO-e519e98e`。本次新建待辦，不重開既有已關閉 TODO。

2026-09-11 後續決策：[不卡產出的 Plotly 設計](ask-o11y-novice-report-simplification.md#2026-09-11-修訂以順利產出為預設不再自訂-plotly-方言) 優先取代本頁涉及自訂圖表功能白名單、強制拆分 views、圖表失敗連帶封鎖分析證據的規則。其他身份／資料／授權／effect 政策不變；這是設計更新，尚非 runtime 完成證据。

## 目標與非目標

使用者能以自然語言選擇授權資料，完成描述、比較、視覺化及受支援的 ML 分析；依意圖確認後執行，結果可追溯並在 Grafana 檢視。

- 不以 dataset、欄名、欄位順序、業務 keyword 寫死 target、features、模型、最佳化方向或報告流程。
- 所有產出圖片均由 `asko11y-plotly-panel` 呈現；純敘述可以用 Text panel。
- 查詢、計算、報告、寫入的權限分離；LLM 不產生數值權威或自行擴權。
- 不抽樣、截斷或改資料母體來掩蓋資源限制；視覺聚合不得回流模型輸入。
- 不增加中央固定 DAG、Recovery Engine 或自訂 AutoML 框架。不在本批次擴建完整 MLOps。

允許並保留：版本化 schemas、實際支援演算法 allowlist、scorer 定義、安全驗證、資源上限與 deterministic 計算。這些不是業務 hardcode。

## 元件與權威

| 元件 | 責任 | 不得擁有的權威 |
| --- | --- | --- |
| Ask O11y | 理解意圖、選能力、提議契約、解釋證據、修 recoverable 語義錯誤 | 猜 ref、覆寫資料、決定驗收成功 |
| Session host | actor/session、附件、核准 revision、operation identity | 以自由文字推導附件授權 |
| Ontology/evidence | observed facts、語義假設與核准規則的來源 | 把 keyword 猜測當核准事實 |
| Query Planner | 驗證 scope/投影/filter/分析契約，產生版本化 plan | 執行 datasource |
| Grafana Query | 唯一 datasource execution boundary，產生綁定 plan 的 frame | 自行變更 scope 或模型輸入 |
| Sandbox trusted adapters | 實踐 split、訓練、評估、profile 與執行收據 | 未核准排除資料或擴大預算 |
| Artifact Bridge | 授權 refs、完整 evidence、synthesis/展示驗證 | 寫 Grafana、改模型數值 |
| Plotly plugin | sanitized figure 或受信任圖片與敘述的呈現 | 任意 JS、重新訓練或重選 threshold |
| Grafana writer | 核准 preview/publish、版本與 effect receipt | 绕過 artifact/展示契約 |

以上是可組合元件，不是每次都要完成的步驟清單。只問資料描述時不啟動 ML；修改報告時重用已成功 artifacts，不重跑 query/ML。既有 native datasource 圖表可保留；本修訂強制所有分析圖片走 plugin，不把一般 datasource dashboard 全改成靜態圖片。

## 契約與版本規則

下列為目標邏輯欄位，實作時優先擴充現有 plan/provenance/receipt，不另建平行權威儲存。

- Identity：org/user/session、attachment identity、artifact scope 與 explicit reuse grant。
- Intent：task、使用者 target、scope、必要假設與 confirmation revision。
- Data：source snapshot/hash、plan digest、projection/filter、frame identity、完整筆數收據。
- Analysis：supported task、features、split、metric、candidate set、global budget、seed。
- Optimization：僅在明確請求時存在；方向、controllable fields、support/bounds 與核准。
- Execution：host-issued executor identity/version、實際 split/trials/rows、winner lock、final verdict。
- Presentation：artifact ref、image/plotly capability、view narratives、inspection coverage、dashboard ref。
- Operation：request key、input digest、effect status、result receipt。

任何變更不得默默改寫已核准 plan 或其 hash。新契約升版；舊契約只可經明確、可追溯轉換，無歧義才相容，否則要求重新確認。最近一次 ref 僅可消除傳輸省略，不可取代同源驗證。

## NLAP-01：原始證據驗收

狀態：待實作。優先級 P0。TODO：`TODO-33dc17fe`。審查 K。

保存 immutable run/session/tool-call events 與原始成功／錯誤結果，summary 由事件推導。Recovery 要證明同 report context 下 error→修正參數→成功→writer receipt，且成功 query/ML 未重跑。不同 continuation 保存 parent/run IDs，不能合併成一次 fresh E2E。

驗收：無效 upload ID、空成功記錄、重複工具名、跨 run 拼接、換 ref、caller 自填 True 均不能驗收成功。既有審查的合成摘要反例必須被拒絕。

## NLAP-02：Session scope 與連線隔離

狀態：待實作。優先級 P0。TODO：`TODO-06a40ecc`。審查 G。

Actor/session context 端到端傳遞，不在 Sandbox 丟棄 session。Artifact 明確區分 session-private 與 explicit user-reusable；跨對話重用需要受信任 grant，不由 prompt 授權。現有 user-scope artifacts 不可默默當 session-private；遷移或重用時要求明確授權。

MCP 使用當次不可被另一 actor 替換的 connection/session，或以完整身分隔離；保守過渡可序列化整次 connect+call，但必須註明吞吐限制。

驗收：cross-user、同 user cross-session、stale/deleted upload 拒絕；explicit reuse 正常。A/B reconnect 交錯測試不得串 actor 或互相關閉 connection。

## NLAP-03：Plan/frame/approval 同源

狀態：待實作。優先級 P0。TODO：`TODO-5c9e7bb4`。審查 H。依賴 NLAP-02。

Frame 的 plan digest 必須等於 contract_ref 的 digest；source、projection/filter、split、seed 與核准 revision 都需相符。不能只分別驗證兩個 refs 屬於同 user。

驗收：同 schema、不同 source/filter/plan 的合法 refs 混搭，在 Sandbox 啟動前拒絕；執行參數變更不得沿用不相符核准。

## NLAP-04：真實切分契約

狀態：待實作。優先級 P0。TODO：`TODO-0f74abd2`。審查 C。

分類／回歸共用受信任 split primitives，outer split 與 CV 遵守時間／entity 規則。先拒絕不支援組合，不能接受 chronological contract 卻執行 random stratified split。Manifest 記錄實際 split digest、time ranges、group overlap。

驗收：未來資料不進 train、entity 不跨邊界，unsupported 組合在 compute 前拒絕。與 NLAP-03 同源驗證整合。

## NLAP-05：模型鎖定與 final holdout

狀態：待實作。優先級 P0。TODO：`TODO-622d2b6f`。審查 E。依賴 NLAP-04。

所有 model/feature-set 選擇僅用 training/CV 的指標及 guards。鎖定 winner 後，winner+baseline 才進一次 final holdout gate；失敗回報不通過，不以同 holdout 改選候補。

禁止用含 holdout metrics 的 accepted-first eligibility。一次 final gate 可計算多個預先指定 metrics，但研究層級要記錄所有 holdout 使用，不只回傳 selected helper 的 counter。已揭露 test 不可在下一輪選模中仍稱 untouched；重新研究需明示 test 已消費並安排新的獨立驗證。

驗收：改 holdout labels 不能改 winner identity，只能影響 release verdict。多 feature sets 在 final test 前完成比較。

## NLAP-06：Effect-aware recovery

狀態：待實作。優先級 P1。TODO：`TODO-81359729`。審查 J。依賴 NLAP-02、03；驗收依 NLAP-01。

LLM 繼續讀 validator 原文並只修相應 synthesis/參數。Transport read 可 retry；compute/write 需按 actor、operation key、input digest 查 receipt，回應丟失先查結果，不盲重送。相同 operation key 不可接受不同 inputs；新意圖可發新 key。

結果未知是 indeterminate，不是明確失敗。必要的效果狀態記錄不是中央分析 DAG，也不替 LLM 決定分析流程。

驗收：response 前斷線時 compute/writer 各只執行一次並取回相同 receipt；scope/inputs 變更不可復用舊授權結果。

## NLAP-07：統一 Plotly plugin 圖片邊界

狀態：待實作。優先級 P1。TODO：`TODO-39afaf4e`。審查 A。

所有分析 artifact 圖片一律 `asko11y-plotly-panel`，包含 profile、regression、classification 及探索圖片。支援明確 `image` 與 `plotly` 模式，由已驗證的 artifact MIME/capability 決定，不看 dataset/model 名稱。

- Image mode：受信任 opaque asset binding；原樣顯示圖片，不偽造互動 trace。
- Plotly mode（2026-09-11 修訂）：以原生 Plotly 相容的完整 figure 呈現，不維護 trace/layout 功能白名單；沿用資料呈現方式，不提供 eval/script/custom callbacks 執行入口。
- Text panel：僅敘述，禁止 img、CSS image 等圖片繞過路徑。
- 單／多 view 都保留 narratives、evidence、alt 與限制；image mode 不因 single-view 去重而遺失全部敘述。
- Invalid figure 不默默降級掩蓋問題；在該圖位置顯示錯誤，其他已驗來源的文字／表格／圖仍可讀，不連帶拒絕所有分析結果，也不將部分交付稱為完整。需要改 image mode 時仍須明確使用合法原圖片 capability，不改資料或假裝互動成功。使用者要求失敗停下時仍停止後續操作。
- 新 figure/operation 要重置舊失敗狀態，支援可驗證的更新恢復。

同步更新 compositor、dashboard validator、Bridge、writer gate、host prompt 與 plugin；不能只改前端。舊 dashboard 遷移由核准 writer 使用原 artifacts，不重新計算。歷史 PNG fallback 的「自動降級」規則由本節明確模式取代。

驗收：API 讀回所有 image evidence panel 均為 plugin；browser 驗證 light/dark、CJK、resize、單／多 view 敘述、PNG、Plotly 與錯誤後更新。API 200 不代替 browser 驗收。

## NLAP-08：語義事實、假設、核准分離

狀態：待實作。優先級 P1。TODO：`TODO-1299dd92`。審查 B。

Observed facts 記錄來源与 coverage；欄名、大小、順序推測只能是未核准 hypotheses。Target 與特徵可用性來自使用者意圖、lineage、核准規則。禁止最後低 cardinality 欄位自動成為唯一合法 target；移除領域 measurement 豁免與固定用煤量文案。

敏感／leakage 風險不取消；無證據時保留 unresolved 並要求確認，不能用更多 keyword 補白名單。前 1,000 rows 推測不得冒充全體常數或值域，完整 facts 與 NLAP-10 整合。

驗收：欄位重排、等價更名、未知領域不無故改已核准角色；審查 measurement-name 反例不再依特例授權。

## NLAP-09：Metric 與業務最佳化分離

狀態：待實作。優先級 P1。TODO：`TODO-218f14a8`。審查 D。

Metric 方向由 scorer 定義；optional optimization.direction 只在明確業務最佳化意圖及核准時存在。純預測不要求 direction，也不自動生成 candidate-setting 搜尋。

Legacy `target_direction` 表示 predicted target 的方向，不是 metric direction；經版本化轉換處理，不暗改舊 plan。測試不得因 MAE 越小越好而強制業務 target minimize。

驗收：MAE minimize 和 target maximize 可同時成立；未請求最佳化時不執行 constrained search。

## NLAP-10：全量資料與執行收據

狀態：待實作。優先級 P1。TODO：`TODO-5b565da3`。審查 I。依賴 NLAP-03、04。

收據核對 source_rows→queried_rows→eligible_rows→train/test_rows→explained_rows。每個減少／轉換需原因與核准；來源筆數未知時明示未驗證完整度，不假填 True。禁止未核准 dropna。使用者要求全量解釋時，不固定抽取 400 rows 作 SHAP。

圖表可產出完整資料的 bounded aggregates，只用於展示。資料超上限時明確失敗／提供完整分批方案，不靜默縮小母體。探索 Python 可保留，但 verified-ML 身分只由 host-owned executor receipt 給予，不從任意 manifest 自述推導。

驗收：null target、invalid time、少回 rows、解釋子集、視覺聚合都可追溯；範圍未核准變更不得取得成功驗收。

## NLAP-11：能力與全域預算

狀態：待實作。優先級 P1。TODO：`TODO-4b0fae72`。審查 F、L。依賴 NLAP-04、05、09。

由實際安裝的 adapters 宣告 task/split/metric/依賴 availability；LLM 在支援能力內提議候選，unsupported 不自動換成別的 task。Trusted classification 應接通核准多模型清單，不只執行單 kind。

Global search budget 定義為整個 study 的 trial 配額，涵蓋 feature sets；baseline、CV folds、final refits 另列成本收據及總資源上限。候選多於預算時要求縮清單或調整预算，不偷偷每種至少跑一次而超支。需要並行時使用 bounded workers，內層 threads 限制；未實作則明示 sequential。

驗收：候選與真實執行一致，trial 加總不超預算，缺依賴能力不可宣稱可用；並行模式有資源與結果一致性測試。

## NLAP-12：可重現 release

狀態：待實作。優先級 P2。TODO：`TODO-56013e2e`。審查 L。依賴 NLAP-01..11。

Clean checkout → patch apply → unit/contract/Go checks → image/plugin build → real-flow/browser smoke。記錄 repo、patch、image、plugin hashes；必要 source 納入版本控制，不能依賴未追蹤 scratch。清理使用者資料與 push 需另有授權。

Fresh-session E2E 使用自然語言，不在產品能力測試中教工具名、refs 或固定修復答案；具名 fixtures 可測回歸但必須與泛化驗收分開。涵蓋不同 domain、欄序／更名、未知意圖、描述性不升級 ML、PNG/Plotly、隔離及 recovery。

驗收依原始 receipts 與 browser 結果，不用歷史 Telco、合併 continuation 或 health 200 替代。功能成功與模型勝過 baseline 分開判定，blocked-by-baseline 可以是正確平台結果。

## 本地工作清單

| 規格 | TODO | 主要前置 |
| --- | --- | --- |
| Epic | TODO-e519e98e | 全部子項验收 |
| NLAP-01 | TODO-33dc17fe | 無 |
| NLAP-02 | TODO-06a40ecc | 無 |
| NLAP-03 | TODO-5c9e7bb4 | 02 |
| NLAP-04 | TODO-0f74abd2 | 無；整合依 03 |
| NLAP-05 | TODO-622d2b6f | 04 |
| NLAP-06 | TODO-81359729 | 02、03；證據依 01 |
| NLAP-07 | TODO-39afaf4e | 無；release 證據依 01 |
| NLAP-08 | TODO-1299dd92 | 無；完整 facts 依 10 |
| NLAP-09 | TODO-218f14a8 | 無 |
| NLAP-10 | TODO-5b565da3 | 03、04 |
| NLAP-11 | TODO-4b0fae72 | 04、05、09 |
| NLAP-12 | TODO-56013e2e | 01..11 |

以上依賴是實作安排，不是 production workflow DAG。各項完成需 code、檢查命令、實際結果與剩餘限制；文件或 TODO 狀態本身不是完成證據。

## 相關設計

- [Structured executor](structured-ml-executor.md)
- [Regression/optimization](ml-regression-constrained-optimization.md)
- [Multi-model stability](ml-multi-model-stability.md)
- [Report synthesis](ml-llm-report-synthesis.md)
- [Plotly presentation](ml-plotly-first-charts.md)
- [Upload context](upload-dataset-context-test-plans.md)
