# Y5：重核根因與待調整範圍

## 範圍與證據

Session `Y5DGerZFD4sD4uq_hnoDC0ywqFd7xSBUfZoU7QDLwx8`；核對截至 2026-09-09T03:35:24Z 的已保存事件與 Dashboard API readback。本次不執行分析、不部署、不寫 Grafana。原始事件副本與雜湊：`.scratch/y5-root-cause-recheck/receipt-index.json`。尚無瀏覽器渲染驗收。

## 已證實與更正

1. **表示格式不相容，不是空資料。** `FbJRr0...` 回報 `expected non-empty array in trace.y`。`run_a8c590441ce746bead965716a6587a17/sandbox-execution` 第一張圖 y 實為 `{dtype:f8,bdata:...}`；base64 解碼為 1104 bytes，即 138 個 float64。`ml_plotly_contract._check_array` 對非 list 與空 list 使用同一錯誤。此前「Python 產生空序列」歸因撤回；數值是否科學有效是另一個問題。
2. **Plotly Express 預設屬性與允許格式衝突。** `tmSPd9...` 拒絕 `hovertemplate`；保留輸出同時仍有 typed-array 編碼。僅刪除 hovertemplate 不足以完成修復。不得直接放寬所有安全限制。
3. **運算執行與報告產物驗證混在一起。** 上述兩筆 sandbox execution 都 `error=null` 並保留 8／6 個輸出；工具卻因 manifest 驗證回傳 `ok=false,recoverable=false`。LLM 之後要求再次核准整段 Python，而非先判別可否修復既有呈現／證據匯出。execution 成功不證明方法或數值正確，但也不等於必須重算。
4. **最後不是原目標分析，而是改用舊 profile。** `rj2sq...` compose 主動指定 `delivery_status=partial`，來源回到 `run_5a225...` profile。API readback `coal-profile-preview-7d1e` 為 9 panels、partial。缺少承諾的熱耗率關鍵因子與煤源差異交付。
5. **問題綁定中斷，不是問題完全沒有保存。** profile provenance 保存原始 business_question，但 `analysis_contract=null`；`_analysis_coverage` 提前回傳 not_assessed，未檢查 profile manifest 的泛用 purpose 與原問題差異。compositor 又把 manifest purpose 作為 Dashboard Question。不能把現有 ML-only coverage 當作任意決策問題驗收。
6. **有 LLM 敘事，但未形成問題導向解釋。** compose 原始參數已有 observation／interpretation 等文字；並非完全沒有呼叫 LLM。可引用 facts 只有 rows、columns、profiled_rows；敘事多是通用說明。一般 profile 的相關圖只含前十二個數值欄，時間圖只含前四個，均未涵蓋熱耗率。圖表內容與引用數量合規不等於回答了問題。
7. **完成狀態語義不足。** Python 報告失敗回合仍以聊天 done 記為 run completed；partial Dashboard 寫入成功後亦引導發布。partial notice 有保留，不能說完全隱瞞；但聊天完成、運算完成、分析交付與預覽持久化沒有充分區分。
8. **工具選擇膨脹已知，但不是全部根因。** 同 server 全工具重新加入的程式缺陷與歷史 selectedTools=85 已觀察。selection-closure 只有 source/offline 測試證據，不能宣稱 runtime 已修復；後續需核對安裝 binary/source fingerprints。85 並不證明某一個模型原本選了哪些工具，也不能單獨解釋敘事品質。

## 調整原則與反例

- 保留動態分析策略；需求是已確認的問題／比較／資料範圍及證據義務，不是固定 DAG、熱耗率關鍵字分支或必跑 ML。
- 使用者只要資料概況時，profile 可是正確交付；使用者要煤源比較時，同一 profile 不可替代。
- partial Preview 是合法的有限交付；必須保留原問題與逐項缺口，不得因 partial 而改題或假稱完成。是否限制 partial 正式發布需使用者決策，並非本次已決政策。
- 138 個有效熱耗率值不代表可靜默刪列、填補或放寬先前核准。呈現修復不得改分析母體、值或分组。
- 煤源槽位 A/B/C/D 不等於國家；同日多煤源共享同一熱耗率，不可當獨立國家樣本。第一份程式展開槽位、第二份改比槽位，方法是否回答國家差異需另驗證，不可因格式修好就通過。
- 報告匯出修復沿用已保存合法結果；若必須重算，先確認原方法、授權與結果狀態。模糊／indeterminate 寫入先 reconcile，不盲目 retry。

## 驗收要求

- 使用真 Plotly producer 輸出跨 emit→capture→manifest→bridge→panel 驗證，含 typed arrays、Express 預設屬性、真正空陣列及惡意／超限負例。
- 合法 normalization 保持數值、次序、維度、群組與授權範圍不變；錯誤含 artifact/trace/path 與修復層級，不逐次猜 key。
- 已確認需求版本→執行／結果→報告→Dashboard／chat 狀態可追溯；not_assessed 不是 pass。
- 真實敘事指出本題觀察、關係、限制與下一步，不能只引用列數或泛用免責文字。計算正確性、語義充分性與瀏覽器可讀性分開驗收。
- REQUIRED：機械回歸、最終 source 獨立審查；涉及輸出解析／授權的 security review；獲准後 clean build、runtime fingerprint、fresh-session E2E、saved JSON 與 browser 驗收。沒有相應證據不標完整修復。
