# Ask O11y 瘦身 S1：source-only 驗收

2026-09-10。母 TODO-01ba5263；本片 TODO-826d5f52。
產品契約見 [非專業使用者報告與系統瘦身](../design/ask-o11y-novice-report-simplification.md)。

## 已實作

- 刪除 `conversationSupportsDatasourceFamily`、初選與重選的英文詞表 gate，以及多餘的 `datasource_families` selector 回覆欄位。重選不再需要整份 `LoopRequest`。舊 selector 回覆帶多餘 family 欄位仍可被 JSON reader 忽略，不影響舊 fixture。
- 只從已經 RBAC／tool selection／exclude 過濾的目錄選能力；未知能力不能加入。`executeTool` 的 RBAC、工具開關、actor/session、approval、Preview/publication、effect receipt 未改。
- 泛用 `execute_python_analysis`／`revise_python_analysis` 的純 JSON 數值、文字、CSV 輸出可成功，保留 computation／provenance／inline results／下載；沒有圖時 `report_status=not_requested`，不建立假的 report manifest。
- `presentation_mode` 是有圖時的格式偏好，不是要求每次計算都必須製圖。這不判定使用者的完整報告已完成；LLM 仍要依本輪要求提供白話答案、事實、限制與適當呈現。
- 先驗證 reserved report-source，再接受純資料輸出。有 Plotly／PNG 或偽造 report-source 時，不能走純數值的捷徑。無法建立綁定的未命名／不支援視覺輸出明確拒絕；不靜默忽略。
- 既有 structured profile／ML producer 仍保留報告要求；無效圖、manifest persistence failure、既有 unknown／terminal operation 分流不變。既有 receipts 不因新版 source 被改寫或自動重試。
- 真 `AgentLoop` system request（含 custom prompt）加入簡短產品責任：使用者不需懂分析／ML；LLM 選法並解釋術語、證據與限制；要求完整報告時，裸數值、工具 JSON 或收據不算完成。沒有新增 workflow／成功關鍵字判定器。
- 設定生成腳本移除「使用者必須說出 datasource family」矛盾與一段重複 Plotly 說明；仍禁止猜 UID 或繞過 registered-data contract。工具描述與成功回覆同步說明純計算不需圖。

本片四個正式程式／設定檔淨減 52 行、1,894 bytes；tests、文件、配送 patch 不在此數字內。這不是模型成本或性能實測。保留歷史 patch 順序，新增相容增量 `patches/ask-o11y-novice-report-slimming.patch`，不以 squash 冒充架構瘦身。

## 證據與驗證

證據目錄：`.scratch/novice-report-slimming/`。

| 檢查 | 結果／證據 |
| --- | --- |
| 修前 RED | `python-red.log`：scalar 計算成功仍要求 Plotly；`go-red.log`：中文初選被詞表濾掉，實際 LLM request 缺新手產品責任 |
| 真 Python host RPC＋ArtifactStore，fake remote executor | `recovery.log`：scalar／CSV／text 的 execute＋revise 成功、同 frame／parent provenance、無 manifest、原 receipt 不變；原能力／錯誤／repair 回歸仍 PASS |
| 非安全捷徑負例 | 同一 regression：偽造來源的檔名／format、HTML／SVG、無效／未命名 Plotly 均拒絕；trusted profile／ML 無圖仍拒絕 |
| 真 Go selector／reselection，mock LLM | `go-green.log`：四種工具 family 可由中文問題選用，未在 authorized catalog 的工具仍拒絕；existing scope/approval 邊界保留 |
| 真 AgentLoop request | 同一 log：五種 turn 情境含 custom prompt 均收到新手／本輪責任，沒有額外強制工具呼叫；完整 Plotly capability 與原 rejection 仍進入 request |
| 既有 recovery／lineage | `generic-repair.log`：generic public repair、immutable outputs、no redispatch、fresh bridge lineage PASS |
| Plotly／Bridge／presentation | `plotly-contract.log`、`bridge.log`、`presentation.log` PASS |
| Sandbox／settings | `sandbox.log`（fake executor／隔離 store）、`settings.log`（local-defaults、絕對 scratch output，沒有 --apply）PASS |
| 正式重建 | `package.log`、`patch-stack.log`、`source-rebuild.json`：26 patches，213 個 Go／TS／TSX／Markdown sources byte-identical |
| 重建來源測試／build | `rebuilt-tests.log`：`go test ./... -skip Redis -count=1`；`rebuilt-build.log`：`CGO_ENABLED=0 go build ./pkg`，皆 PASS；Go proxy 關閉、使用既有本地 toolchain |
| 固定流程／shell | `no-fixed-flow.log`、`bash -n scripts/build-install-ask-o11y.sh` PASS；固定流程掃描只涵蓋腳本列出的舊 U1 markers，不是自然語言理解證明 |
| LSP | 9 個相關 Python／Go 檔 primary LSP 零 errors；不是全專案或全部 auxiliary scanner 的清潔證明 |

Patch SHA256：`7fdaa476aed2402c4e8064879f22a8d1e886005b19cc6baacb23ffe46cdb1bce`。
來源差異／hash：`before/`、`before-hashes.json`、`source-delta.diff`、`final-hashes.json`。

### 測試中發現與處理

- `python-fixture-mismatch.log`：首次 GREEN 嘗試的文字 fixture 只放 `mime.text/plain`，與真 capture 使用 `result.text`／空 mime 不同。依 `server.py` capture 實作修 fixture，未修改 inline reader 迎合假資料。
- `unnamed-renderable-red.log`：新增負例暴露未命名 Plotly 可漏入 `not_requested`。修正共同 preflight：非 plain、又無可綁定來源的 generic output 拒絕，保留 runnable regression。
- Auxiliary `no-boolean-in-except` 對合法 `REPORT_MANIFEST_ERRORS=(ValueError, TypeError, KeyError, OSError)` 誤報；`no-identity-operator-on-literals` 對安全契約的 `is not False` 誤報。讀實際來源後記 false-positive，沒有放寬精確 False 判定或例外處理。最後 scoped lens full 的 LSP sweep 曾 inconclusive；隨後單檔 primary LSP 再次 confirmed clean。該次 lens 顯示 gitleaks／opengrep timeout，不能稱這兩個 scanner 通過；full/cached lens 的零 error 不等於完整掃描證明。

## 複查與未完成範圍

主代理自行檢查最終 patch 與 Python diff：新增 plain 分支在 reserved-source validation 之後；capture 大小／有效性 audit 仍先於 preflight；有視覺產物不被悄悄刪除。RBAC 與 enabled/excluded tools 在初選／重選目錄建立前仍執行，execution 再次檢查。未變更資料／approval 政策，未添加依賴。

- 主代理 only，沒有獨立 reviewer；本輪不是獨審證據。
- Redis 名稱測試全部跳過，避免現有測試連 localhost DB15／FlushDB；沒有 Redis 整合驗收。CGO 關閉，沒有 race。
- 無前端修改，未做 frontend build／瀏覽器驗收。
- **未部署、未修改 live settings、未跑真 LLM／OpenSandbox、未重跑使用者 CSV、未處理舊 operation、未改歷史 session/artifact／Goal、未 commit/push。**
- S2 報表 interface、S3 安全科學文字與樣式、S4 薄核心收斂尚未完成。原有嚴格 report schema、部分重複指引與 delivery warning 仍在，不把 S1 稱為整體瘦身完成。
- **新手是否真的看得懂、方法是否合理、圖文是否一致與可讀，是母 TODO 的真 LLM／QA／browser 必需驗收。** Mock 只證明 host 接線與機械契約；不聲稱自然語言品質、科學正確性、成功率或 token／時間改善。

下一步是 S2 的最小報表 interface 與相容策略，先保留同一份證據與故事內容，再刪除機械填表負擔；不直接刪 renderer／sanitizer 或讓使用者負責補技術參數。
