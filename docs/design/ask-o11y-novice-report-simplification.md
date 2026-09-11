# Ask O11y：為非專業使用者瘦身，不縮水分析能力

2026-09-10 使用者核准。追蹤：TODO-01ba5263。這是新的產品與瘦身方向；舊文件的驗證快照不改寫為新版本已通過。

S1 source-only 已實作與驗證，見 [S1 驗收](../verification/ask-o11y-novice-report-slimming-s1.md)／TODO-826d5f52；S2a 最小敘事與舊版相容已 source-only 完成，見 [S2a 驗收](../verification/ask-o11y-report-interface-s2a.md)／TODO-34db0647。S2b 單一 inspection ref 也已 source-only 完成，見 [S2b 驗收](../verification/ask-o11y-report-evidence-cursor-s2b.md)／TODO-9094da8a。S3–S5 尚未完成，整體產品目標仍待真 LLM／QA／browser 證據。

## 2026-09-11 後續修訂：未評估不等於缺項或失敗

使用者核准修正手動 session 的 Host delivery status 語意，要求不新增 hardcode 或限制。沿用現有狀態欄位，不新增 validator、狀態機、必經流程或資料集／方法特判。

- `not_assessed` 是自動評估尚未進行或沒有適用評估結果；本身不是一項 `gap`，不構成 partial／失敗的證據，也不等於評估已通過。
- Producer 不再於缺少適用評估契約時憑空產生 `REQUIREMENTS_NOT_RECORDED`／`REQUIREMENT_NOT_ASSESSABLE` 缺項。Host 不再於正常 plan、query 或成功 producer 後，僅因尚無報告評估而添加 PLAN/REPORT_NOT_ASSESSED 缺項。
- 真正的執行錯誤、輸出／圖表缺失、身份／來源／數據綁定錯誤仍保留原有 evidence、檢查與拒絕。沒有把 not_assessed 強制改為 evidence_available 或宣稱業務問題已完全回答。
- 沿用 LLM 的報告組織與 scope 判斷；既有 `delivery_status=partial` 表達已知交付缺項，而不是自動評估能力不足。工具原有欄位說明釐清兩者，不新增參數、LLM 回答文字判斷、方法白名單或強制流程。
- Host 輸出分清「自動分析評估」與真正 writer 結果；沒有 writer 證據時不在每次 Preview／query 後顯示像失敗的 not_verified 警告。移除籠統的 `Assistant response (not host-confirmed)` 前綴，不因此提升任何來源或授權等級。
- Dashboard 通知分開描述部分交付與未評估狀態，不再將 partial 一律翻成「分析不完整」。保留人可讀的未知／具體限制，不隱藏真錯誤。
- 改動作用於新產生的回應、prepared contexts 與 Dashboard；舊聊天內容、已保存 Dashboard HTML、execution／inspection／context receipts 不回寫或追認。舊 prepared context 若與新 producer 評估結果不同，原 freshness/identity 檢查仍拒絕混用；要新組版時用既有 manifest 正常準備與讀取新 context，不重算、不新增遷移／retry 協定。

驗證沿用 public report RPC、真 Go loop producer replay 與 renderer 通知，涵蓋無適用評估、正常 default composition、真缺圖、真錯誤、身份／來源負例；不以修提示詞字串或單次模型順從當作狀態正確性的唯一證據。本輪不重跑使用者資料或改写既有 Preview。

## 2026-09-11 後續修訂：保留正常工具訊息，不以 refs 摘要取代上下文

使用者核准更新設計、修正實作、移除為此問題增加的補償程式，並重開新 session 驗證。以下取代 S2b 中「每回合只保留 refs」的決策；不是新架構或游標恢復協定。

- **根因**：完整工具結果已存在 session，但下一次 `/agent/run` 組裝歷史時，`compactPriorToolState()` 不論是否超過模型預算，一律把結果縮為 refs 等白名單欄位，捨棄 facts、figure 及 `remaining_artifact_count`。這是應用程式丟上下文，不是 LLM 自然遺忘，也不是分析失敗。
- **修正位置**：現有 session→agent messages 組裝。還原標準 assistant/tool-call 與 tool-result 訊息，保留參數、原始文字結果、成功／錯誤及 call/result 配對，之後再放入原有 context window。工具資料不再升格為 system message；歷史訊息不授予任何執行／重試權限。
- **大小限制**：刪除第二套 16 KiB refs-only 摘要與 omission 狀態。沿用已有 recent-message 與 token-budget 機制；訊息數切點不能拆開 tool call/result。真正超過預算才由既有機制裁剪，不能預先宣告 refs 等於證據內容。
- **相容**：新保存的 UI tool records 保留原 call ID；舊 records 沒有 ID 時，使用穩定的訊息位置 ID 只作模型訊息配對，不建立 operation、不重寫舊 session 或 receipts。沒有結果／損壞的紀錄明示 unavailable，不猜測成功。
- **刪除補償**：移除 `compactPriorToolState`、專門維護摘要白名單／第二套預算的程式及過時測試；撤除本次為 generic `not_assessed` 增加的 inspector／compose 誘導說明。不改 coverage 狀態與檢查、不改已完成 inspection 的錯誤分支，不新增重讀／自動重試機制。其他真實問題的修正（部署 hash、cache identity、原生 Plotly、來源／權限防護）保留。
- **驗收**：真 handler→agent→HTTP model request 回歸須收到完整、超過舊 16 KiB 的報告結果，包括末端 figure、facts 與完成狀態；包含成功、失敗、未完成 calls 和視窗切點配對。正式 patch 可重建、測試／build 與 live hash 一致後，以全新 UI session 上傳原 CSV、Preview、確認、分析及 Dashboard 呈現驗收。不得以舊 refs 重試或單純機械 PASS 宣稱完成；真工具失敗即停。

## 2026-09-11 修訂：以順利產出為預設，不再自訂 Plotly 方言

狀態：使用者要求「以不卡的方向去設計」。以下是新的設計決策，**尚未實作或部署**；優先取代本文件 S3 及舊報表文件中相衝突的圖表白名單／整份 manifest 連帶拒絕規則。歷史測試與 receipts 不變。原因見 [U1 拒絕診斷](../verification/u1-report-rejection-20260911.md)。

### 交付路徑

**成功計算 → 保留結果與來源 → LLM 解釋結果 → 原生 Plotly 呈現所需圖表。** 這是責任關係，不是新增固定工具順序。圖表是分析交付的一部分，不再是取得所有分析證據的前置門票。

- 既有 execute／inspect／compose interface 吸收實作，不增加 planner、LLM、repair engine、圖表 DSL 或另一套 validator framework。
- LLM 使用一般 Plotly API，不必學自訂 trace/layout/nested-key 清單，也不必為普通圖表反覆取得使用者技術決策。
- 完整報告仍須回答業務問題、說明證據與限制；不能靠成功 receipt 或一張能畫的圖宣稱分析完整。

### 刪除與保留

| 項目 | 新設計 |
| --- | --- |
| 手工維護的 trace／layout／nested 欄位白名單及樣式限制 | 移除。正常 error bars、subplot annotations、hover／legend／色彩／軸設定不因未手工列舉而拒絕 |
| 複製給 LLM 的龐大能力清單 | 移除；只說明實際 Plotly 版本、既有執行環境及必要的輸出方式 |
| 格式相容 | 交給版本相容的原生 Plotly producer／renderer；不再另抄 schema。不使用 skip_invalid 或靜默刪欄位偽造成功 |
| 缺值 | 圖表座標按 Plotly 語意保留缺口；可在表示層將缺測座標轉為 null／斷開片段，位置與 x/y 對齊不變。不能全域把非有限統計值當缺值吞掉、填零或改原始資料 |
| 自製 subplot 拆分／重排 | 預設完整 figure 原樣呈現；保留原 domain、anchor、annotation、共享軸，不為配合自訂 view 結構改圖。Grafana theme／resize 不覆寫分析語意 |
| 自訂圖表數、trace 數、點數等重疊 gates | 逐項移除沒有獨立實測理由的限制；沿用已有 execution／傳輸大小及 Sandbox 資源限制，不新增猜測性上限 |
| 身份、來源、原始資料與 operation | 沿用既有權限、owner/session、refs/hash、full-data、missing-policy、exact approval、隔離及 idempotency；不新增第二套 |
| 安全 | 不新增腳本執行入口，沿用安全文字呈現與現有資源載入控制；JSON 不等於可執行 JS。移除某防護前確認其風險已由真實 consumer 處理，不用一般 Plotly 欄位白名單代替此檢查 |

「原生 Plotly」指實際配送版本支援的功能，不承諾所有未安裝版本、需外部服務或自訂 JavaScript 的擴充。不新增外連／執行權限來換取成功。已有輸出限制超過時明示範圍，不抽樣、截斷或隱藏資料。

### 單張圖失敗，不封鎖整份已成功分析

- 在既有 execution／report artifacts 中分開記錄：計算結果是否成功、來源是否可驗、各圖是否可呈現。沿用既有狀態結構擴充，不另設平行 ledger。
- 結果／fact catalog 以成功 execution 和同源資料為依據建立，不必先讓所有 figures 通過。仍然驗證引用的數字來源，不把任意 LLM 敘事當事實。
- 某張圖無法解析或渲染：保留原 figure 與錯誤，在其原位置顯示明確錯誤；其餘可用文字、表格與圖仍可供使用者查看。不得默默刪圖、用 PNG 假裝互動成功，或把部分交付標成全部完成。
- inspection 遇到無法抽取的特殊 trace/view，只明示該項資訊不可得；不因自製摘要器不認識就否定原生可渲染 figure，也不捏造 inspection／vision 證據。預設以完整 figure 作檢視單位。
- writer 可在既有核准範圍內保存明示缺項的 Preview；publication／完整交付狀態不自動升級。若使用者要求「失敗停下」，立刻停止後續工具操作，只回報已保留結果與錯誤，不以部分交付機制繞過停下要求。
- 修圖不重查資料；若必須重執行 Python，明確列為新 exact call，不宣稱純報表修復。不自動盲重試，也不改既有 rejected／indeterminate receipts。

### 最小實作範圍與驗收

1. **先鎖定真案例**：保留 U1 四圖作回歸，另用小型含缺值資料經真 Plotly producer 到 renderer，涵蓋誤差棒與自動 subplot 標題。不是再加四張手寫合法 fixtures。
2. **替換而非疊加**：修改共用圖表處理、report manifest／inspection 與 plugin consumer；移除舊白名單及重複 prompt。Bridge、writer／delivery 若假設「所有圖通過才有數字證據」，一併改為上述獨立狀態，避免錯誤移到下一道 gate。
3. **相容而非追認**：舊合法 manifest/dashboard 保持可讀；新表示與錯誤狀態使用明確可辨識的契約修訂，優先擴充既有格式，不硬塞給舊 consumer。歷史 receipts／來源 hash 不重寫。
4. **成套配送檢查**：使用實際 Grafana 工具目錄、MCP／Sandbox 與 panel 版本／內容驗證，不以 port open、工具名稱或 source build 代替 runtime 一致。這是部署檢查，不是每次分析新增模型關卡。
5. **以產出驗收**：U1 四圖在真瀏覽器呈現，缺值維持缺口、誤差區間可見、subplot 標題與共享配置保留；至少一張不同於 U1 的原生 Plotly 圖能通過，不依資料集／圖名特判。另驗單圖故障時已有結果可讀、错误清楚且不假報完整。權限／來源負例及實際腳本／外連／輸出限制維持有效；相同資料不得因恢復報告而重新 query。

機械回歸、安全 consumer 檢查、真 browser、部署一致性、真 Ask O11y 報告與文件為必要證據；只有前述檢查均完成才能宣稱交付成功。主代理自行檢查，非獨立審查；本輪不啟動 children。**這次僅修訂設計；部署、原資料重跑及舊 operation 修復不在本輪執行範圍。** 不順帶增加架構、重設 UI 或擴大到無關分析政策。

## 產品目標與完成定義

藉由 LLM 的理解、分析策略和解釋能力，讓**不懂資料分析／machine learning 的使用者，也能從業務問題得到看得懂、可追溯的報告**。減少 patch、工具呼叫或 schema 欄位只是手段。

- 使用者提供問題、必要的業務含義與授權，不必指定演算法、target/features、驗證程序或報表 JSON。
- LLM 從授權 metadata／資料證據釐清技術事實，自行選擇適當的描述、比較、統計或預測方法；不為展示能力而強做 ML。
- 報告先回答「發現什麼、對問題代表什麼」，再給必要數字／圖表、來源、限制與可行下一步。術語在使用時解釋，不要求新手選擇技術參數。
- 依問題選擇長度、章節與圖表。簡短數值或表格也可以回答簡單問題；**使用者要求完整報告時，裸數值、原始工具 JSON、下載連結或單一 Dashboard 寫入收據不能算完成**。
- 敘事與 artifact 使用同一份事實；相關不等於因果，探索不等於確認性證據，未支援／資料不足需說明，不偽造確定性。
- LLM 判斷工作是否回答本輪問題；host 只證明可機械驗證的計算、來源、儲存、授權與渲染狀態。不增加自然語言成功判定器、固定報告模板或中央 workflow。

## 責任分界：薄核心、深報表 module

| 責任 | 所在位置 | 不應暴露給一般使用者的細節 |
| --- | --- | --- |
| 理解意圖、選法、解釋與組織報告 | 同一分析 LLM | 不把算法選擇和工具錯誤修復推給新手 |
| 工具發現與執行、approval、context | Ask O11y 核心 | 不以自然語言詞表充當 RBAC，不編排必經分析順序 |
| 計算／artifact／來源驗證 | 現有分析工具 | 資料與產物的機械綁定由 implementation 負責 |
| 報表事實讀取與呈現組裝 | 現有 report module | 收斂 prepare／inspection／compose 的重複 interface；不是新增另一個 LLM 或報告引擎 |
| 圖表格式與呈現 | 原生 Plotly／現有 plugin | 不維護縮水版 Plotly schema；安全、來源與資源處理由已有對應 consumer 負責 |
| 真實 effect 與交付證據 | 現有 writer／receipt | 計算成功、報告生成、儲存成功、視覺已驗證各自如實陳述 |

不以檔案數或 implementation 行數比率判斷 module 深度；看 caller 要學多少協定與能完成多少有用工作。不用新增 wrapper 把同樣長的 schema 再包一層。

## 先做的縮減

### S1：解除非安全限制，保留回答責任

1. 刪除 datasource family 英文詞表與相關 selector／reselection gate；能力來自已授權工具目錄，由 LLM 按問題與工具證據選用。保留工具存在性、開關、RBAC 與執行時授權。
2. 一般 Python 計算若只產生數值／表格／文字，不因缺少 Plotly MIME 宣告 report rejection；沒有圖就不製造圖的驗證錯誤。有視覺產物時仍走原 sanitizer、manifest 與 recovery，不能把壞圖當作「不需要報告」略過。
3. 工具與實際 LLM request 清楚區分「有用的分析回答」和「可選報表 artifact」，維持上述新手報告品質責任；不硬編碼判讀使用者要圖／要報告的字眼。
4. 正式 patch stack 須可重建。刪除的是最終有效行為；新增一個相容增量 patch 的檔案數不代表程式變胖，也不以 squash 假裝完成重構。

### S2：縮小報表 interface

保留證據 ID、lineage 與可驗證事實；縮減每 panel/view 必填敘事欄位。先定最小可用 interface、現有有效產物的讀取相容與 immutable receipt 策略，再改 schema。機械性的 ref／inspection 整理由既有 module 吸收；LLM 保有分析內容、圖表與敘事的決定權。不把逐圖固定模板改成另一個固定全篇模板。

### S2a 契約細化（2026-09-10，TODO-34db0647）

沿用 v1 的相容擴充：舊完整 payload 必須保持同樣內容與可讀性；只對新 composition 的缺省值做 normalization，不改歷史 receipt。

- Panel 必填縮為 `artifact_id`、`view_ids`、`headline`、`observation`、`interpretation`、`limitation`、`evidence`：仍須說明看到什麼、代表什麼、不能推論什麼。
- `cross_chart_context`、`next_step`、`view_narratives` 可省略。逐 view 敘事是有需要才提供的附加解讀，可涵蓋 selected views 的子集；不複製 panel 敘事到每個 view 假装獨立解釋。
- 提供 view 敘事時必填 `view_id`、`headline`、`data_observation`、`interpretation`、`limitation`、`evidence`；`next_step` 可省略，`visual_observation` 缺省為 null。收到 vision 不強迫寫視覺觀察，但只有 spec 時仍禁止宣稱像素觀察。
- 機械預設：section 的 `collapsed=false`、`narrative_blocks=[]`；panel 的 `view_narratives=[]`、`priority=supporting`、`preferred_width=full`。缺省文字不填罐頭答案，顯式空字串／null 不能繞過文字驗證。
- 完整 inspection、比較與 uncertainty facts、身份與來源驗證不減少；敘事少不代表可以少看證據或不交代限制。
- 現有 UI 僅做相容修正（Extension）：沿用 Grafana 字型、色彩、間距、圖表／折疊互動；省略 optional 欄位時不畫空標題，沒有逐 view 解讀時必須保留 panel 白話說明。沒有新視覺系統或 layout 重設。
- S2b 的 prepare/inspection/compose 收斂見下節；不能以省掉真 inspection 收據或變更 artifact/session 政策冒充深 module。

### S2b：單一 inspection ref（2026-09-10，TODO-9094da8a）

共同入口沿用 `inspect_report_artifacts(report_manifest_ref)`，內部完成 metadata preparation，傳回完整 bounded facts/catalog 與本批實際 artifact evidence。以最新 `inspection_ref` 續讀，不必收集 context/ref 陣列或手動切批；`remaining_artifact_count=0` 後才把同一 ref 與 LLM 自己的 synthesis 交给 `compose_ml_dashboard`。

- 不新增 tool、ref type、planner 或分析順序；每批最多八 artifacts、每份證據最多八 receipts，沿用原有 bounds。可選 `artifact_ids`／vision，預設 spec 不等於看過圖片；這是 artifact-count bound，不是總 token／byte 保證。
- 收據留本批 coverage/mode 與扁平 prior refs；compose 重新驗證每份 receipt 的 owner/session、context、view modes 與完整 coverage，不偷偷 inspection、不掃描 storage 挑「最近成功」替代缺失 ref。空／重複／超限／跨報告 references 拒絕。
- Prepare 每次產生 fresh context，不覆寫舊 snapshot。新 receipt 綁定 context digest，變更後停止而不是要求重寫敘事；不更改底層 ArtifactStore 的舊格式或原始 execution/provenance。
- 舊 prepare/context/artifact_ids/inspection_refs 介面保留；缺 digest 的歷史 receipts 仍需舊顯式介面，不冒稱具備新 cursor 的 digest 保證。新舊參數混用拒絕，沒有隱式 fallback。
- Read receipts 是「供應過證據」的機械紀錄，不證明 LLM 理解、科学有效或實際视觉驗收。工具結果以正常上下文保留；不再每回合強制縮為 opaque refs。僅在既有視窗／token 預算確實裁剪內容後，才有必要重新讀取 retained manifest，不能假裝仍記得 facts，也不重新查詢／計算使用者資料。
- Go text-only host 在既有 event／approval 前綁定點，僅對省略的 mode 補 `spec`；原 vision guard 不變，顯式 vision／null／空值／非法值仍拒絕，核准後不改參數。有效 receipt 缺 inspection 時要求續讀；損壞／未授權 identity 停止，不能用敘事修復取代。
- Go delivery transport 的身份／授權綁定保留；session context 改為標準工具訊息，不用 compact state 取代證據內容。保留上下文不升級 `not_assessed`，不授予 writer 或 publication 權限。Bridge、Go 與指引需一致配送，source 檢查不能推定舊 runtime 已更新。

### S3：視覺能力與安全分離

本節原「先補樣式／保留完整 sanitizer」方向由上方 **2026-09-11 修訂**取代：移除自訂 Plotly 功能白名單，沿用原生呈現與真正有用途的既有防護；單圖錯誤不再拒絕所有分析證據。下列 S3a/S3b 只是歷史 source 完成紀錄，不是新設計已實作。

S3a 已完成圖表標籤的 source-only 修正（TODO-23fa655d）：以 stdlib HTML parser 區分 markup 與比較運算子，原字串不轉碼，沿用 Plotly native text-node sink。見 [S3a 驗證](../verification/ask-o11y-plotly-labels-s3a.md)。其他樣式與真 browser 品質仍待完成；不宣稱 S3 整體完成。

S3b 已完成報告文字的 source-only 修正（TODO-d76569de）：report source、synthesis、dashboard 共用 S3a markup 判別，保留各自長度、ID、URL／script 與數字證據規則；沿用 compositor `html.escape` 和 React 文字節點，儲存值不 escape/unescape。`p < 0.05` 可作為已保留 fact 的 display，不能直接塞入 synthesis 敘事繞過數字引用規則。Bridge 與兩份 Plotly/shared contracts 必須成套配送；見 [S3b 驗證](../verification/ask-o11y-report-narrative-s3b.md)。

### S4：收斂核心與指引

將報表專屬機械邏輯集中到真正的 report seam；去掉相互重複、矛盾的 prompt／skills 協定。保留一份簡短的跨 custom/default prompt 產品責任。Plotly 能力以實際配送的原生版本為準，不再產生或手抄自訂功能清單。根據實際依賴逐片重構，不整包刪除混合安全與功能的歷史 patch。

### S4 本輪必要清理（source-only）

依使用者「不要 overdesign」要求，不預做額外樣式或抽象重構。本輪只修已找到的指引矛盾：

- Default prompt 刪除「平行失敗就改逐一重試」與通用「出錯就修參數重試」；沿用原有依錯誤／receipt 判斷的 recovery 指引。
- 壓縮後讀 retained artifacts／authorized metadata，不為恢復上下文重跑成功的查詢或分析。
- 設定用 prompt 取消指定中英文章節標題；方法適用理由、資料檢查、評估與確認責任全部保留。
- Viewer／Editor／Admin 的真 prompt registry、custom override、真 Loop＋mock MCP/LLM 回歸通過；設定 self-check 與 no-fixed-flow 通過。修正既有 configured-prompt checker 的過期字句，RED 另存，不冒稱是本輪產品回歸。
- `ask-o11y-prompt-cleanup.patch` 納入正式配送；29 patches／215 sources byte-match，重建後 targeted Go tests 與 offline backend build 通過。未跑全 Go／Redis／race／前端 build／真模型或 browser。

證據：`.scratch/prompt-cleanup-s4/acceptance.json`、RED／green logs、`rebuild.json`、片前與最後 hashes。本輪未修改 skills、工具／權限／資料 gates 或 runtime 編排。S3 其他樣式與 S4 其餘去重不盲目擴充，後續以 S5 實測問題決定；不宣稱 S3–S5 或整體產品已完成。未部署、未套設定、未重跑資料。

## 本次不更改的政策

RBAC、session/owner、opaque refs、exact-call approval、Sandbox 隔離、原始資料／receipt 不可變、full-data／derived dataset 政策、missing-policy、effect idempotency、未知結果先 reconcile、Preview／publication 分離均保留。任何放寬需另行提出明確範圍給使用者決定。

原生比較以鎖定的 `8395ae10c3e38beae56329e4174a14a9a6d4c680` 為準，不聲稱最新 upstream 沒有問題；原生不具相同報表管線，因此不能拿不同能力的成功率比較。

## 驗收與阻擋條件

| Gate | 要求 | 本輪範圍 |
| --- | --- | --- |
| 機械回歸 | 中文工具選擇、純計算、壞圖／unknown operation；S2a 加 minimal／legacy 合成與讀取、證據與身份负例 | 各片必需；真 RPC／mock LLM／SSR 不冒稱真模型或 browser 品質 |
| 正式來源 | 依實際 source 變更選相關回歸、LSP／cached lens；改 Go／patch 時重建 byte-match/tests/build，改前端 production 時隔離 typecheck/build | 各片必需；S3a/S3b 為 Python-only production，驗相關契約與配送副本，不重跑未修改的 Go／webpack；缺失、逾時、跳過需明列 |
| 安全 | 保留原權限／隔離／receipt 負例；S3 須額外檢查 renderer 文字注入與外連 | 本輪主代理複查；不冒稱獨審 |
| 獨立 review | 原 main-only 限制有效，不啟動 child | 本輪不適用；不是已有獨審證據 |
| 真 LLM／QA／browser | 下列產品情境與真畫面、實際數值來源驗證 | **整體完成必需**，需另行部署與資料執行授權；source 綠燈不能替代 |
| 文件 | 設計、TODO、證據分清已實作與待做 | 必需 |

產品驗收使用固定模型、相同授權合成資料與需求，比較縮減前後；保持相同工具能力與安全 adapters。記錄成功回答率、事實錯誤、非安全型拒絕、使用者被要求的技術決策、工具輪次、實際 token／時間；不先宣稱會更省。

- 新手只問業務問題：不提供算法，LLM 能提出合理分析，提供白話報告與可追溯數字。
- 簡單問題：只需數值／表格就回答，不額外製圖或建立 Dashboard。
- 完整報告：有問題導向的結論、適當圖文、來源與限制，不用工具成功取代報告。
- 改變說明深度：相同證據改寫給不同受眾，不擅自重算、改數字或強求職稱。
- 資料不足／歧義：只問必要業務問題；不虛構效果、不強做 ML。
- 中文與不直接命名 datasource 的探索：可選已授權工具，未知／禁用工具仍拒絕。
- 無效圖、持久化失敗、未知 effect：原錯誤和適當 recovery 保留，沒有藉瘦身繞過授權。

母 TODO 在真產品驗收前維持 open/in_progress；單片 source-only 完成另記子 TODO。不修改既有 Goal、不部署、不重跑使用者 CSV、不重試舊 operation、不改歷史 receipts、不 commit/push。
