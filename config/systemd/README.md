# 本機 Grafana 分析服務

使用兩個原生user units管理OpenSandbox與五個MCP；不是分析流程。既有.env、image pin、認證與網路隔離不變。Python由systemd直接追蹤，無須PID檔。

首次安裝（目前已完成）：

```sh
mkdir -p ~/.config/systemd/user
ln -s "$PWD/config/systemd/grafana-mcp@.service" ~/.config/systemd/user/
ln -s "$PWD/config/systemd/grafana-opensandbox.service" ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now grafana-opensandbox.service \
  grafana-mcp@{ontology-mcp,data-query-planner-mcp,grafana-query-mcp,sandbox-analysis-mcp,artifact-bridge-mcp}.service
```

若已有舊nohup/Popen服務，需先核對PID身分並停止，避免重複綁定port；不要盲殺PID或在分析進行中移交。unit的WorkingDirectory適用目前`~/apps/grafana`checkout。

日常操作使用`systemctl --user`，既有`start-local-services.sh`／`stop-local-services.sh`也已接到同一組units。不要再以歷史.scratch部署腳本啟動另一組背景worker。MCP日誌在`.scratch/live-services/<實例名稱>.log`（名稱包含-mcp），OpenSandbox仍為opensandbox.log。

```sh
loginctl show-user "$USER" -p Linger
systemctl --user is-enabled grafana-opensandbox.service grafana-mcp@grafana-query-mcp.service
systemctl --user status grafana-mcp@grafana-query-mcp.service --no-pager
```

無分析運行時可測故障恢復：

```sh
systemctl --user show grafana-mcp@grafana-query-mcp.service -p MainPID -p NRestarts
systemctl --user kill --kill-whom=main --signal=SIGKILL grafana-mcp@grafana-query-mcp.service
# 等待RestartSec後確認新PID、NRestarts增加與8772的authenticated tools/list可用。
systemctl --user show grafana-mcp@grafana-query-mcp.service -p MainPID -p NRestarts
```

2026-09-10已實測故障後自動恢復，並經Ask O11y正式upload入口成功上傳原CSV。證據`.scratch/y5-process-engineering/recovery-test.json`與upload.json。已啟用linger及default.target wants；未為驗收強制重啟整個WSL，也不將此稱為已實測跨WSL重啟。Gateway的模型模組更新後需重啟其既有daemon，不能用舊process的cache驗證新磁碟SDK。

回滾服務管理：在無運行分析時`systemctl --user disable --now`上述六個units，再由明確選定的舊啟動方式恢復；不要同時運行兩套管理。保留90天retention，不清除資料或憑證。不應因本專案回滾就關閉其他user services共用的linger。
