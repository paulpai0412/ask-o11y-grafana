# Upload失敗：WSL重啟後本機服務沒有恢復

2026-09-10，主代理唯讀調查；未修改production、啟停服務、建立session、上傳檔案或重跑分析。只執行既有隔離upload handler tests與TCP連線檢查。

## 已證實的失敗鏈

1. Grafana日誌記錄2026-09-10 08:09:18、08:09:35、08:10:53（UTC+8）的真上傳請求，路徑`/api/plugins/consensys-asko11y-app/resources/api/uploads`、HTTP502。
2. 同時的backend錯誤均為`Upload proxy failed`，對`http://127.0.0.1:8772/uploads`的PUT遭`connect: connection refused`。
3. 真`pkg/plugin/uploads.go::handleUpload`在multipart、file/session與session ownership檢查之後才發出此PUT。client.Do連線失敗直接回502／`Upload service request failed`。這些失敗不是檔案內容解析、ML缺值政策或Plotly驗證。
4. 現在8772確實不監聽；五個Python MCP的全部部署PID均已不存在，8768/8771/8772/8773/8777全部拒絕連線；未在上次部署重啟的OpenSandbox 8080也不可達。Grafana3000及gateway4000則可連線。

## 為何服務消失

- systemd journal記錄9月10日07:26:05的WSL2 kernel啟動、07:26:10 systemd startup finished；另有07:55:12/18的一組啟動事件。第一批MCP connection-refused紀錄始於07:27:44，緊接WSL啟動之後。
- journal boot history顯示上一個boot結束、目前boot的新紀錄。不能只用`uptime -s`計算日曆時間：WSL有clock-change紀錄，計算值與journal啟動事件不一致；此處以上述原始journal事件為證據。
- Docker三個服務Grafana／Redis／CSV的restart policy都是`unless-stopped`，目前已自動恢復。
- 上次五個Python MCP由`.scratch/y5-local-deploy/restart-python.py`的`subprocess.Popen(..., start_new_session=True)`啟動，PID寫在`.scratch/live-services/*.pid`。這只是背景進程，不會在WSL重啟後自動恢復。
- system與user systemd unit查詢均exit0，未見這些Grafana/MCP/OpenSandbox相關服務單元；原啟動腳本也只是nohup／PID file管理。現在PID檔仍是舊PID，對應process已不存在。

**根因是部署生命週期不完整：Docker部分會重啟，本機Python MCP/OpenSandbox部分沒有一同被開機／失敗恢復管理。** 因而出現Grafana頁面正常，但upload所需的8772服務不存在。這是部署缺口，不是CSV／LLM／新的資料型別修正造成的檔案解析錯誤。

無法由現有證據判斷誰或哪個Windows操作觸發WSL重啟，也沒有聲稱取得各Python舊process的退出signal；舊PID已被回收。這不影響上述缺少自動恢復的因果鏈。

## 檢查與證據

`.scratch/y5-upload-diagnosis/`：

- `upload-and-first-failures.log`：真upload502及每個MCP的首次連線拒絕。
- `boot-history.log`、`startup-events.log`：WSL/systemd啟動及clock-change事件。
- `current-state.json`：目前TCP結果、部署PID缺失；8772的connect errno為111。

隔離既有handler檢查：

```sh
cd .scratch/ask-o11y-release-build
GOPROXY=off GOTOOLCHAIN=local ../go/bin/go test ./pkg/plugin \
  -run '^TestHandleUpload(ProxiesOwnedSession|RejectsUnownedAndOversized)$' -count=1
```

exit0。它證明在隔離健康upstream下既有upload轉送及ownership/size guards仍可運作；**不代表目前live upload通過**。本輪沒有為了測試而偷偷寫入真上傳服務。

## 應修正的位置

應讓OpenSandbox與五個MCP具備原生服務管理（例如systemd）的開機啟動與失敗重啟，保留現有網路隔離、image digest、90天retention及認證；並把跨重啟的端到端upload驗證納入部署交接。不是改CSV parser、放寬授權、清資料或重啟Grafana。

上次部署只確認當下PID、健康、檔案及image一致，未完成跨重啟存活檢查；不能以當時PASS取代現在服務可用性。本輪尚未實施修正或恢復服務。
