# Y5 localhost 手測候選部署

使用者明確核准：先同步並驗證完整候選，再部署localhost；另核准產物保留期7→90天並先備份。主代理執行，沒有派child、commit/push、新增套件/provider、啟動分析或寫入Dashboard。

## 已部署

- Build ID：`y5-input-20260909`。
- <http://localhost:3000>
- 可讀版本標記：`/public/plugins/consensys-asko11y-app/y5-build.json`。
- 運行中的唯一Ask O11y backend SHA256：`4cf07f2320c2d2c46b21fe7087f2a8970d61a3aa22224d35b58da4cd0c475e8f`。
- Sandbox image：`sha256:a28051971fac72cd20a483c5907f60309615c24865b5addb252f24a80c8cb97a`，以digest寫入既有SANDBOX_IMAGE設定。
- Grafana app／Plotly panel共36個安裝檔案與build逐檔相同；五個Python MCP已重啟，實際進程的image／90天retention設定已核對。OpenSandbox daemon、gateway、Redis與資料庫未重啟。
- 原2902個artifact檔案全部byte-identical，沒有因啟動清理遺失或修改。未重跑使用者分析。

## 候選同步與驗證

證據目錄：`.scratch/y5-local-deploy/`。

- 新增`patches/ask-o11y-delivery-continuity.patch`：同步既有main版本delivery_state、loop、loop_test與真producer replay；沒有再把舊round4測試成功冒充目前版本。
- 修正共用SidePanel production按鈕的aria-label為`Close panel`，適用Dashboard與Explore；既有測試未改預期。
- 全23個正式patch重建及既有完整patch-stack checker：exit0。候選所有329個tracked／untracked source檔案逐byte等於保留的release-build checkout；原checkout未刪除重建。
- 真Python public producers輸出→真Go loop，fake LLM／transport的generic修復12種正常／budget／LLM error／truncated路徑；連同agent/plugin/mcp suites exit0。fixture在本輪臨時root產生，不使用歷史成功fixture冒充新執行。
- 前端34 suites／492 tests全通過；typecheck、production webpack、Linux backend build及Plotly panel build通過。
- synthetic profile型別、missing-value approval、trusted regression、analysis coverage及generic repair回歸通過。沒有讀取原CSV做分析重跑。
- LSP、lens session error checks及git diff checks通過；不等同獨立review或全project LSP掃描。

### Sandbox程式層不可漏部署

profile與ML模組是從Sandbox映像的`/opt/ask-o11y`載入，不是直接讀host source。因此除了重啟MCP，也更新了映像。

原Dockerfile完整離線重建exit1：系統套件層無快取，network=none下apt無法取得fonts-noto-cjk。沒有放寬網路／安裝新套件來繞過。改以`Dockerfile.code-update`重用既有依賴映像ID `sha256:121b2697d46d237b0ec9221ece0b6aed7f99e422a90981d69376f4a56eb88141`，只COPY既有八個程式檔。code-only build exit0；八檔hash與host source全相同；容器內以network=none、read-only及synthetic資料跑profile check通過。這不是宣稱原Dockerfile完整重建成功。

## Runtime直接核對

- `runtime-verification.json`／`running-backend.sha256`：實際`/proc/.../exe`匹配build，不只核對磁碟檔案。以既有Grafana UID472讀取，未增加container capabilities。
- `grafana-readback.json`：Grafana database health、app health、HTTP served build marker、公開MCP入口的repair_generic_report均成功；沒有呼叫agent/run。
- `service-readback.json`：五個實際MCP的tools/list可用。
- `python-restart.json`、`python-source.json`、`candidate-source.json`、`image-source-hashes.txt`：部署來源／進程／映像證據。
- 首次Jest命令重複指定互斥worker參數而失敗；修正命令後才得到真測試結果。首次readback缺env auth映射／helper global，修正只讀探針後使用既有專案Grafana readback helper成功。原失敗logs保留，未重複部署來猜結果。

## 備份與回滾界線

- `.scratch/y5-local-deploy/rollback/`：原app、panel、完整artifacts archive、私密env備份（0600；不要貼出或分享）。
- Docker volume內舊plugin目錄在`/var/lib/grafana/y5-rollback-20260909/`，不放在plugins掃描目錄內，避免重複plugin ID。
- 若需回滾plugin：先停止Grafana，把目前兩個plugin目錄移到另一個保留位置，將上述舊app/panel移回plugins，再啟動並核對舊binary hash。不要清除資料庫、CSV或artifacts。
- Sandbox舊image仍保留；回滾時只改其digest，**不要整份還原env而把retention降回7天**，否則下一次啟動仍會清理舊產物。
- 舊Python進程的完整已載入程式版本未有可驗證快照，不能承諾精確還原其記憶體版本。plugin／image／資料備份可回復；進一步Python回滾需先選定可信source。
- 本輪沒有執行回滾。

## 使用者手測

1. 強制重新整理Grafana（Ctrl+Shift+R），確認版本標記為本候選。
2. 開新session，重新提出原分析問題與交付要求，由你操作資料與核准；不要用歷史Y5的「同意」當成新session授權。
3. 確認煤源profile有分類資訊、原計畫在授權後延續；若報告失敗，觀察是否保留原code/error，而非改成另一個問題。
4. 核對Dashboard與最後chat是否保留原問題、缺口與partial狀態；工具成功不可等同分析完成。
5. 若失敗，保留session ID及畫面／錯誤，不先反覆重跑。

尚未做browser、手動分析驗收或fresh independent review。此交付是已安裝且核對版本的手測候選，不代表Goal完成或熱耗率問題已回答；歷史兩筆缺provenance的產物仍不可升格修復／發布。
