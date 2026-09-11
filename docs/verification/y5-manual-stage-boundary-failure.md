# 手動核准品質稽核卻因未建立 Dashboard 失敗

## 實際事件（2026-09-10）

Session `Hyg-6uXVS_lSFypanS0MEz03-nbC9jPd4vrlf4ny9Gc`；失敗 run `CgiCYocB8dxJ9g0cDLmWBQ6l12TIDB2Q9rnSKxMmFQc`。

上一輪明確提出「只做全資料品質稽核、不排除不填補，之後提出第二版計畫供核准」。使用者回覆「確認核准」。

- 06:49:26Z：run開始，model=large、modelSource=auto、role=Admin。
- 06:50:10Z：selector設 `prepareGrafanaPreviewAfterExecution=true`。
- `execute_planned_query`成功。
- `profile_dataset`成功：139→139、排除0，computation succeeded/report accepted；保留run_8fbf5bc939fc4f4f8c6463777ad29521。
- 隨後三個LLM請求分別約92、92、68秒，均status=ok，無後續工具事件。
- 06:54:51Z：主控發出`grafana_preview_missing`，run failed。沒有Dashboard writer呼叫，也不是writer拒絕。

## 因果定位

`pkg/agent/loop.go:544` selector提示將「本輪核准執行」加「先前或當前要Dashboard」映射成強制Preview，沒有區分階段稽核與最終交付。

`loop.go:326–335`只要曾有execution輸出，就拒絕未建立Dashboard的無工具回覆；兩次nudge後第三次發error。`emitFailure`呼叫`emitFinal("")`，不保留當次LLM文字。因此合理的階段性停頓也會被判成failed。真實三段原始LLM文字沒有保存，不能聲稱知道其內容。

另有獨立意圖保存錯誤：`pkg/plugin/plugin.go:1060`用`req.Message`初始化OriginalBusinessQuestion，本輪變成「確認核准」。實際final_report即如此；不等於已證明LLM上下文中的原始問題也被移除。

## 隔離反事實測試

`.scratch/y5-manual-failure-Hyg/stage_probe_test.go`以Go overlay執行真AgentLoop，僅使用fake LLM/MCP成功profile，不執行使用者Python／資料查詢／Dashboard寫入。只改selector的prepare旗標：

```
cd .scratch/ask-o11y-release-build
GOPROXY=off ../go/bin/go test -overlay=../y5-manual-failure-Hyg/overlay.json ./pkg/agent -run '^TestHygStageBoundaryProbe$' -count=1 -v
```

- false：3次LLM呼叫，保留稽核回覆、done，PASS。
- true：5次LLM呼叫，回覆丟失、grafana_preview_missing、沒有done，FAIL（預期的缺陷重現，不是修復成功）。

loop.go及plugin.go與先前部署candidate逐hash相同；served marker仍y5-input-20260909。此次未重新驗證running executable hash，日誌與事件本身是live行為證據。

## 為何先前續測成功

先前run5是已有frame及失敗分析的明確修訂續跑，指示完成報告與Dashboard，不是新的分階段品質核准流程。真writer成功只能驗證那條路徑，不能外推一般手動新session成功。

修復方向：區分原始目標、本輪核准範圍和最終交付；允許合法稽核／待核准停頓並保留回覆，仍不得把缺Dashboard的最終交付當完成。不能用移除所有missing-preview檢查、強迫越過核准或延長timeout掩蓋。

此次僅診斷與本機隔離probe，沒有修改production、重啟服務、重跑使用者分析、改原session/artifact或Goal。
