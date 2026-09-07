# 接續測試 Ask O11y 的 Thinking 修正

本修正不會自動重跑分析、重送失敗的 Dashboard 寫入或正式發佈 Dashboard。

## 原 session 接續測試

1. 重新整理 Ask O11y，開啟原 session（網址保留原 `sessionId`）；不需重新上傳 CSV。
2. 在同一對話送出：

   > 請沿用本 session 已成功完成的煤源分析與前 5 組相關性圖表，不要重新查詢資料或執行 Python。只使用既有分析結果建立 Grafana Preview Dashboard，並由 LLM 解釋內容。如果既有結果已過期或無法取得，請明確告知並停止，不要自行重跑。正式發佈前請再詢問我確認。

3. 若出現 Dashboard 寫入核准，確認內容後批准。核准綁定精確參數；模型修正參數後可能再次詢問。
4. 成功條件：回傳可開啟的 Grafana Preview URL、煤源比較圖與 5 組雙參數趨勢／散佈圖，以及解釋文字；沒有重新執行 query／Python。
5. 尚未確認正式發佈前，Dashboard 應保持 `ask-o11y-preview` 標記。

若仍失敗，保留該次 run ID、工具錯誤與時間，不要連續按重試。若寫入結果不明，必須先核對既有 Dashboard／持久化 receipt，不能刪除 receipt 後強制重送。

## 修正行為與限制

- 分析已成功但 Preview 不存在時，連續沒有工具呼叫的模型回覆最多得到 **2 次額外催促**；仍無進展則回傳 `grafana_preview_missing`，不再空轉到整輪 50 次上限。
- 真正的工具呼叫會重置上述連續計數，因此保留修復工具參數的能力。持續呼叫工具的情境仍受原本 `MaxIterations` 限制；這不是全域兩次重試限制，也不是固定秒數 timeout。
- 原始工具錯誤與成功分析 ref 保留。前端既有 error 事件處理負責結束 Thinking。
- 已過期的分析資產不能靠此修正恢復；需由使用者另外核准重跑。

## 部署設定

`compose.yaml` 掛載唯讀的 `config/grafana.ini`。其中：

```ini
[plugin.consensys-asko11y-app]
asko11y_effect_store = $__env{GF_PATHS_DATA}/ask-o11y-operations
```

Grafana 把此外掛專屬設定轉為 `GF_PLUGIN_ASKO11Y_EFFECT_STORE`，無需啟用 `plugins.forward_host_env_vars`。不要為了傳入單一路徑而開放所有主機環境變數。

目錄選擇優先順序為 `ASKO11Y_EFFECT_STORE` → `GF_PLUGIN_ASKO11Y_EFFECT_STORE` → `GF_PATHS_DATA/ask-o11y-operations`。全部缺少時仍拒絕寫入；沒有改用 `/tmp` 或弱化 receipt 的一次執行／結果不明保護。目錄必須位於持久化且外掛 OS 使用者可寫的 volume，根目錄權限會收斂至 `0700`。

新的 INI 保留目前 Grafana 13.1.2 image 原有的三項啟用設定；其他設定繼續由 defaults、既有 environment 與 Grafana DB 提供。

## 可重跑檢查

在 repository 根目錄執行（不呼叫模型、不寫 Dashboard）：

```bash
python3 scripts/check-ask-o11y-effect-store.py
```

此檢查讀取**實際外掛子程序**的三個相關目錄變數，不輸出其他環境內容；核對持久化 mount，以外掛有效 UID/GID 且相同群組集合將專用根目錄設為 `0700`，再建立並刪除一個臨時寫入探針。若 Docker 無法重現附加群組則拒絕宣告成功。

INI 明確把 `plugins.forward_host_env_vars` 設為空 plugin-ID 清單（不是布林值）。檢查器以 Compose 已設定的非機密 `GF_SECURITY_ALLOW_EMBEDDING` 為 canary：它必須存在於容器、且不能被繼承到外掛；若 canary 缺少或出現在外掛，檢查失敗。這是全環境繼承的回歸檢查，不是通用秘密掃描器。

不需 Docker 的檢查器正反例測試：

```bash
python3 scripts/test-ask-o11y-effect-store-check.py
```

已依 `scripts/build-install-ask-o11y.sh` 準備好來源與 Go 後，先核對補丁能從本地乾淨來源重建（不下載、不安裝）：

```bash
python3 scripts/check-ask-o11y-patch-stack.py
```

檢查會依安裝腳本順序套用全部補丁，任一步失敗或四個修改的 Go 檔案不一致就停止；不以最後一行成功文字掩蓋前面的失敗。再執行：

```bash
cd .scratch/ask-o11y-release-build
../go/bin/go test ./pkg/agent ./pkg/plugin ./pkg/mcp -count=1
../go/bin/go test ./pkg/agent -run 'TestDashboardReceiptStoreConfiguration|TestAgentLoop_GrafanaPreviewNoProgressIsBounded' -count=10
```

回歸測試使用真實 AgentLoop 搭配本機 mock LLM/MCP，包含：無進展結束、工具修復後成功、成功 ref 與錯誤保留、外掛專屬目錄設定／優先順序、缺設定禁止寫入、durable replay。

建置腳本依序套用 `ask-o11y-preview-recovery.patch` 與 `ask-o11y-effect-store.patch`，並在安裝後執行子程序目錄檢查。完整建置安裝會下載依賴並重建／重啟 Grafana，不是單純診斷指令。
