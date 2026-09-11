# Ask O11y session 4EB：Preview 失敗根因

2026-09-11；主 agent 唯讀調查，未派子代理、重跑分析、重試發布、修改產品程式或部署。

## 結論

修過並驗證的 Go build 並未成為執行中的 Ask O11y backend。部署使用 `.scratch/ask-o11y-release-build/dist/gpx_consensys-asko11y-app_linux_amd64` 的舊產物，而最近 source 驗證輸出到 `.scratch/plotly-delivery-first/backend-check`。部署 receipt 只證明舊 dist 與 live process 相符，沒有證明 live process 與本次 verified build 相符。這是先前交付查核的缺口，不是使用者操作錯誤。

## 這次 session 的實際經過

- Session：`4EBjlma8ARLQEqax2h_PsJuUg8H0hERn4gxR0AYvSCc`
- Preview run：`laSHkkCLp7kOm5kIMv2hIUQY3VvvoIPePZiPeYfYvvo`，large / auto，完成計畫。
- 使用者回覆：`確認執行`。
- Failed run：`hJW5N4OKT_LEle_BjfPl5TICjc-TXtk6F1gsPNA75CM`，base / auto。
- 唯一工具呼叫：`grafana-query_execute_planned_query`；成功取得 139 列、13 個所選欄位。
- 沒有 Sandbox、report inspection/composition 或 Grafana writer 呼叫。
- 最後事件為 `grafana_preview_missing`，畫面卻宣稱 `Analysis completed`。

因此這次不是圖表渲染失敗，也不是 Dashboard writer 拒絕；流程在 query 後沒有進入分析／寫入。不能將此 session 說成「分析成功，只差 Preview」。

## 因果證據

### 一、執行產物與已驗證產物不同

| 對象 | SHA-256 |
| --- | --- |
| live `/proc/26/exe` 及部署 dist | `5f0ce973d3d61481226c47584ab9ea7eccc593149f906ffc248603100395e07b` |
| source 驗證產生的 `backend-check` | `7bc0593f8c51c4efe3e28da0aece5a8e3e6b28aa48470bf28f376097e0316e7b` |

live exe 路徑為正式插件目錄，不是 backup 目錄。舊錯誤字串存在 live-matching binary，verified binary 中不存在。`go tool nm` 顯示舊 binary 有 `agent.isExecutionResult`；verified binary 有新 `deliveryState` 方法。再次核對 source-rebuild receipt 的 216 個 source hashes，全部與當前 release-build source 一致，沒有 source drift。

`.scratch/plotly-delivery-first/rebuild_check.py` 明確 build 到 `backend-check`，沒有更新 release-build/dist。`.scratch/plotly-deploy-20260911/deployment-acceptance.json` 記錄的 binary hash 則是舊 dist hash。

### 二、舊狀態判斷把 query 當成 analysis

保留的舊 source（`.scratch/y5-goal/delivery-implementation/before/pkg/agent/loop.go:413–419`）以 `ok == true && strings.HasPrefix(step, "execute_")` 判定成功執行。部署 binary 的反組譯也顯示相同八字元 `execute_` 前綴比較，並具有相同 source line mapping。

`execute_planned_query` 因而被歸為成功執行；當模型沒有再提出工具呼叫且 Preview 尚未存在時，舊 loop 提示繼續，超過 bounded no-progress 次數後送出誤導性的 `Analysis completed ...` 錯誤。這與此次 query 之後僅有 LLM stream、沒有其他 tool events 的日誌吻合。沒有保存完整 LLM 回答，不能斷言模型每輪為何不呼叫工具，也不將此歸因於特定模型品質。

### 三、真 UI 路徑與先前 API 測試不一致

日誌明確顯示 Preview 為 large / auto，但短句 `確認執行` 被舊 model routing 改成 base / auto。目前 source 的 `pkg/plugin/plugin.go:1011–1023` 已加入沿用歷史 user task 的邏輯，但不是 live binary 的行為。

先前主 agent 的 API 測試明確指定 `model=large`，再送入包含完整分析要求的確認文字；不能當成使用者「UI auto → 短句確認」的驗收。先前 Preview 更只覆蓋一張相關圖，並未驗證完整煤源分析視覺成果。這些測試通過不等於本次需求端到端成功。

模型路由變更是已證實的路徑差異；本次不以未做的模型 A/B 測試宣稱它是唯一原因。

## 與 chunk 錯誤的區別

先前 chunk 480 是另一個部署／快取一致性問題：新舊 frontend 同為 0.3.5，module URL 相同、檔案時間倒退，可得到 304 並沿用引用已移除 chunk 的舊入口。乾淨 Chromium 可開 Ask O11y。這不能解釋本次 backend `grafana_preview_missing`，兩者不應混為「清快取就好」。插件掃描亦存在 backup 重複註冊警告，但本次 live backend 路徑明確是正式目錄；不把 duplicate 警告當成已證實執行錯目錄。

## 最小後續修復與完成線

1. 不再新增 planner/schema/retry 框架。將已驗證 source 的配套 frontend/backend 作為同一配送候選，修正入口版本／快取識別，備份移至插件掃描目錄外。
2. 部署時比對 verified artifact → volume → process 的同一 hash，而非只比對 volume 與舊 dist。不得用重新啟動舊產物冒充已修。
3. 修復後以真 Ask O11y UI 的 auto 選模及短句確認，驗證 query → analysis → report → writer → URL readback → 可見圖表／報告。另行核准執行後才做，遇錯停止，不重送舊 operation。
4. 目前 session 只有成功 frame，沒有可重用的成功 analysis ref。不得按照誤導訊息假造或要求 repair 不存在的分析。

尚未修復／部署，沒有宣稱新 build 已通過 live E2E。

## 本輪修復與真 UI 驗收（2026-09-11，後續更新）

使用者授權開始執行；全程主 agent，沒有 teams、commit/push 或正式 Dashboard 發布。

### 已實際部署

- 重建配套 frontend，安裝 verified `backend-check`。candidate 全檔 SHA256SUMS → staging → plugin volume 全部比對通過；重啟後 `/proc/.../exe` 為 `7bc0593f8c51c4efe3e28da0aece5a8e3e6b28aa48470bf28f376097e0316e7b`。
- plugin version 改為 `0.3.5+local.dd40135f24125753`，由 module/backend bytes 決定，避免固定版本沿用舊 module URL。新增最小 stamp helper 及 missing build／placeholder／變更／idempotency 回歸；installer 同步使用並核對 live backend hash。
- 舊 app 及 backup 目錄移至 `/var/lib/grafana/plugin-backups/20260911T080727Z`；不在 plugin 掃描樹中。不刪除原備份，不動其他服務。
- 第一次 move 以 container uid472 執行遇到 legacy uid1000 目錄權限失敗；未 swap/restart。確認狀態後，以部署用 uid0 移動同一 verified staging。另保留 `deploy-permission-failure.txt`。
- 自訂 webpack output path 未套用預設 dist 的版本 placeholder replacement，於部署前檢出並依原 webpack 規則補齊；helper 新增 placeholder fail-closed 回歸。沒有部署 placeholder manifest。

### 真 UI 已觀察到的行為

新 session：`Prhwasj7VpgALMQpcfpeGXiyJ60Bt4AeGZQLqahbHxw`。

1. UI 上傳原 CSV，Auto 模式，Preview run `t0sTGN-NzKNZ5fe_5bqnTANL1PdvH1e4arhuUeX5Kuk` 完成。
2. 真 textarea 輸入短句 `確認執行`，run `DC_1E3otA6thmXMMgCMuJkWziaZE-n5NZ3knsZTH56I` 保持 large/auto，完整 query 139 rows/50 fields。模型要求額外確認隔離式 Python 計算；不是 tool failure。
3. 同 UI 確認既定計算範圍，run `FqjpIpBB8X3OKES-egI_BRhMzf6WVpSKO4yvaf0P-MM` 只执行一次 Sandbox 及一次 inspection；computation succeeded、report accepted、6 個 artifacts、presentation_errors=[]。
4. 這時模型將 `REQUIREMENTS_NOT_RECORDED/not_assessed` 誤讀為工具失敗而自行停下。真 inspector 回傳 ok=true；generic Python provenance 沒有 supervised analysis_contract，故 coverage 不可自動評定。現有 compose 只對 incomplete 作條件拒絕，not_assessed 可保留 status notice 進入 Preview，不等於 certified complete。
5. 只補充 inspector instruction／compose description 的既有語意，不更動 coverage、身份、facts 或 approval gate。既有 native-report 回歸新增 succeeded generic→inspect→compose→resolve 並保持 not_assessed 與全部圖表；RED→GREEN，cursor/安全負例亦通過。重啟 bridge；Grafana tool catalog 仍 cache 舊描述，確認後重啟 Grafana 更新，live catalog 查核通過。
6. UI 依原授權繼續既有 refs，run `CSMwObW7F8UBaZ3czz5QvtvAPXVGxJ99vxW5ef1-F4E` **發生真正工具錯誤**：模型以完成的 inspection_ref 再呼叫 inspect，回覆 `inspection already complete; compose using the existing inspection_ref`。主 agent 按下 Stop generating；官方 GET 終態為 cancelled，只有該一次 inspect 呼叫，沒有 compose/writer。

第一次 UI harness 將 Combobox innerText 當成值而失敗；截圖實際為 Auto(default)，且尚未啟動任何 agent run。修正為讀 input value，沿用已上傳的空 session，沒有再上傳／重跑分析。保留 first log／screenshot。

### 尚未完成，不能稱為 E2E PASS

正確產物配送、UI 入口與短句 model continuity 已驗證；分析與六張圖已產生。但 report composition、Dashboard writer、Dashboard readback／真圖表渲染尚未通過。真正工具錯誤後已停止，不重試 cursor、不重算、不正式發布。

成功 execution：`artifact://run_b134b5c973b14db7b055e5b9be7b3fc5/sandbox-execution`。
成功 report：`artifact://run_b134b5c973b14db7b055e5b9be7b3fc5/report-manifest`。
完成 inspection：`artifact://run_14f7cab289f546439f4d1b54708f6241/report-inspection`。

後續應只處理已完成 inspection 的 evidence 再讀／交接，不能修成無證據直接放行或重跑 Python。本輪未實作該後續修復。

## 上下文根因修正與全新 session 驗收（2026-09-11，最新）

使用者進一步指出工具證據本應在 LLM 上下文中，並授權更新設計、修正與刪除補償程式、重開新 session。前節「先改完成游標重讀」的建議撤回；真正修正點是 session 歷史組裝。

### 根因與最小改動

`handleAgentRun` 原本只加入 session 的對話文字，再由 `compactPriorToolState()` 用固定欄位白名單／16 KiB 預算取代工具結果。舊 session 的 inspection 確實包含 6 artifacts、facts、remaining=0，但這些不在下一次請求中。保存過資料不等於資料還在模型上下文。

- 刪除 compact helper、白名單、第二套預算與 omission 狀態；還原標準 assistant tool_call／tool result 訊息。新 UI records 保留原 call ID，舊 records 只用位置 ID 配對。原始結果不再升格 system message。
- 原有 context window 保留；只修正 recent-message 切點不能把 tool result 與 call 拆開。沒有加 context manager／planner／新工具／新游標或 retry 機制。
- 移除上一輪 inspector instruction／compose description 針對 not_assessed 的補償段落與只比提示詞字串的 assertion；保留真正 generic inspect/compose/identity 回歸。不改 completed cursor 拒絕行為、coverage、身份、approval、immutable refs 或資料政策。
- 正式 `patches/ask-o11y-session-tool-history.patch` 納入 installer：Go 6 檔合計新增145／刪除212行。這是替換既有程式的配送 diff，不是疊加第二套執行架構。

### 機械與部署證據

`.scratch/u1-context-fix/`：`history-red.log` 經真 handler→agent→HTTP model request 重現報告內容遺失；`history-green.log` 證明超過舊16KiB的完整 facts／figure／remaining=0、原 call ID 與 arguments 保留。視窗配對另有 `window-red.log`／green。安全負例、unfinished/error records、report cursor RPC／native report 亦通過。

`rebuild.py` 離線由固定 upstream＋31 patches 重建，216 sources byte-match；`rebuilt-tests.log` 全 Go packages PASS（Redis integration 明確排除，未更動儲存協定），`rebuilt-build.log` build PASS。6 Go files primary LSP clean；不是全 repo diagnostics 宣告。

新 backend SHA256：`579e98cf7445a116c76d9cbae93a9e10f450b39c8f7452a5a14d6aca36555338`。
新 plugin version：`0.3.5+local.a42e54fce13029ec`。
Candidate／staging／installed 全檔 checksums 及 `/proc/.../exe` 一致。沿用上一輪已驗證且未修改的 frontend bytes；未重新 build 無變更前端。Backup：`/var/lib/grafana/plugin-backups/20260911-history-context`。只重啟修改的 bridge 與 Grafana；不動其他服務。

### 全新 UI session 結果

- Session：`Oi-9jJz6Wb05d_UHNf5xT0-lFNuGFKeyQ8Y5SD25XM4`，UI 新建＋重新上傳原 CSV，dataset `upload_6a6920c4e2063dab8e3d7b1ce9f1f094`。
- Preview run：`UHCz-Gfa0t_BmtcLirlQYFG2MiTNsJkFPBWLsvlEhcU`。
- 真短句「確認執行」run：`YkwfGPkagSl4bQi_Fn9SZlwSx6OR5Jd9HkmQSrb5_j0`，Auto→large，completed。
- 真工具順序為 query → Python → inspect → compose → writer → summary/property readback，無 isError、無 retry、沒有重新 query／Python。6 artifacts 全部保留，presentation_errors=[]。沒有強制 large API、手工組版或直接 MCP 代替產品流程。
- Execution：`artifact://run_ee8ef1a24f354a02b7523f7196613202/sandbox-execution`；inspection：`artifact://run_7aef92038d5443be9620c2b34cdbb751/report-inspection`；dashboard artifact：`artifact://run_92cef5302c5d49e78b228b01fd24fad6/dashboard`。
- Dashboard：[熱耗率與煤源關係分析](http://localhost:3000/d/heat-coal-prev-6a6920/f7725bb)，UID `heat-coal-prev-6a6920`、version1、14 panels（6張完整Plotly圖＋文字／row），保留 Preview tag，未正式發布／覆寫舊 dashboard。
- 真 Grafana Chromium153、1440與768 viewport，6張 figures 都有 Plotly fullLayout／SVG，無 page errors；截圖人工查看頁首、煤源圖與參數矩陣。`browser-final.json`、`browser-final.log`、`dashboard-*-*.png`。
- Browser harness 初版錯誤要求 UI 顯示原始 enum `not_assessed`；實際畫面已顯示英文完整性限制。只修 harness 以實際使用者可見通知驗證，再讀同一 Dashboard；未改產品／重算／寫入。`browser-harness-first.*` 與原截圖保留。密集矩陣與長欄位標籤仍不適合在窄畫面逐項閱讀；本輪未擴成圖表版面重設。

**本次上下文修正與全新入口到 Dashboard 呈現的驗證通過。** 這不代表因果／正式分析完整性認證：原有 `partial / not_assessed` 與人可讀限制仍可見，沒有放寬或假報已驗證。也不把這次單次成功外推為所有 LLM 流程可靠性已證明；跨停頓後原始 inspection context 的缺陷由真 HTTP seam 回歸覆蓋，新 live run 則在一次確認內完成組版，沒有再次觸發舊的停頓場景。

原 CSV 與上一個成功 execution／provenance／manifest hashes 逐一核對不變。無 children、Goal 操作、commit/push、正式發布；browser contexts 均已關閉。完整母任務／科學與報告品質責任不因這次技術修正自動結案。

## 手動 session 的狀態語意修正（2026-09-11，最新）

Session `zlSRDi7P0kudkcG3i-_yW_f0co03oHWOGrCFUPAfyDI`：query、Python、inspect（7 artifacts／remaining=0／presentation_errors=[]）、compose、writer 與 readback 都成功。Provenance 是 succeeded／accepted，沒有結構化 ML analysis_contract。原 `_analysis_coverage` 因沒有適用評估而產生 REQUIREMENTS_NOT_RECORDED gap；模型再明確傳 `delivery_status=partial`，Host 照該值呈現。不是 Host 發現少圖或計算失敗。

使用者核准「依建議修正，不要 hardcode 或寫限制」。已完成：

- 刪除無適用評估時憑空建立的缺項，以及 Go 對正常 plan/query/成功 producer 添加的 PLAN/REPORT_NOT_ASSESSED 缺項。
- `not_assessed` 保持未評估；不提升為 evidence_available，不替 LLM 認證問題已完全回答。既有 partial 仍供已知交付缺項使用；只釐清現有欄位說明，不新增拒絕條件、資料／方法特判、流程或工具。
- Host 以 Automated analysis assessment 描述 not_assessed，分列有真 receipt 的 writer 結果；移除籠統的 Assistant response (not host-confirmed) 前綴。Dashboard 不再把 partial 一律翻成「analysis is not complete」，並獨立說明未評估。
- 真圖表錯誤、執行失敗、缺少已要求 ML 證據、owner/session、facts／provenance 負例仍拒絕或保留 partial；未改安全／來源／核准／publication gates。

驗證證據在 `.scratch/manual-zlSR-diagnosis/`：status-red／host-status-red；status-green-final；coverage-final（全部既有來源／完整性負例）；cursor；host-status-green-final 使用新 Python public RPC 輸出重播真 Go loop，涵蓋正常、budget、LLM error、truncated 終態。中間 regression 曾期待 Host 原樣複製 raw error，實際既有 sanitizer 只顯示 Tool failed，修正測試而未改 sanitizer；舊通知文字 assertion 同步更新，原失败 logs 保留。

32 patches／216 sources exact rebuild；全 Go packages tests PASS，Redis integration 明確排除；backend build PASS。8指定 primary LSP clean；lens all 最後僅5 cached files 無問題，非全repo clean。三個 identity-operator 診斷是測試字串引起的 scanner 誤報：Python AST 明確為 In/NotIn，無 Is/IsNot；已記錄 false-positive，未改正確程式迎合檢查器。

已配送版本 `0.3.5+local.ecb3290141e904d2`，backend SHA `84ad79870475df120e80879cf0021c77eebcfc18d7876ead292a46ab8a021356`，candidate/staging/volume全檔checksum與live process核對一致。只重啟bridge與Grafana，backup `/var/lib/grafana/plugin-backups/20260911-assessment-status`，未重建無變更frontend／Sandbox。live Ask O11y tool catalog 已讀到新欄位語意。部署前使用者可見sessions無activeRun；最初誤用無list功能的 `/agent/runs` 得到307，於任何部署效應前停止，核對handler後改讀既有sessions metadata，不新增endpoint或盲follow redirect。

Chromium153 已渲染實際 compositor 產生的未評估通知，沒有 partial／失敗的誤導標題。這是 fixture HTML 的瀏覽器驗證，**不是新的真模型分析或 live Dashboard E2E**。本輪沒有呼叫模型、重跑原資料、重新組版使用者報告或寫入／發布 Dashboard。

原 CSV、此手動 session 的 execution/provenance/manifest/context/inspection/dashboard artifact hashes 不變；Grafana `heat-coal-preview-0d269e` 的保存內容前後相等。舊聊天／Dashboard HTML 仍保留原文字，不回寫歷史。後續新組版應正常從 manifest 準備新 context；舊 prepared snapshot 與新版 assessment 不同時，原 freshness gate 仍有效，不新增兼容跳過或修復協定。先前 `%PLUGIN_ID%` 導覽占位符為另一已確認問題，本輪狀態修正沒有順帶變更導覽。

## 證據

`.scratch/u1-session-4EB-diagnosis/`：

- `session.json`、`run.json`：正式 plugin GET 回應。
- `run-log.txt`、`failed-run-log.txt`：對應時間窗的 Grafana 日誌。
- `deployed-isExecutionResult.asm`、`deployed-loop.asm`：與 live hash 相同 binary 的反組譯。
- `findings.json`：事件、hash、source 一致性核對結果；不是產品回歸通過聲明。
