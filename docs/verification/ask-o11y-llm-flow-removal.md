# Ask O11y：移除流程卡控（原始碼驗收）

2026-09-12。主代理獨作，使用者授權包含通用全量資料與逐次分析確認限制。**本次原始碼與離線驗證完成；沒有部署、重跑真分析或更新正式設定。**

## 已移除

- 每回合工具預篩、24-tool 上限、重選補償、自動 skill 注入及 run-local scope／attempt 票券。Run 直接提供 RBAC／MCP enabled selections 過濾後的完整工具目錄。
- query、profile、structured ML、generic Python、document preprocessing、revision 的逐次分析 approval。實際 dispatch 的權限與資料隔離仍在。
- 必經 prepare→inspect→compose、完整 inspection／fact coverage、逐數引用、固定 narrative 欄位、compositor 唯一入口與 host 分析完整性否決。可直接以 manifest compose，亦可手工組合法 source-bound Dashboard；inspection 是可選分頁 reader，EOF 成功。
- 預設 prompt、設定 helper 與兩份隨產品提供的 advisory skills 中的通用全量、禁止抽樣／衍生、受控 ML 唯一資格及固定報告流程。generic Python 可採用可用套件及適當前處理；原始資料與實際來源保留。
- 最終回答的 host 品質警告改寫；不將 `not_assessed` 當成功或失敗。真實工具狀態與寫入結果仍保留。

## 保留

身份／RBAC／enabled tools、actor/session 隔離、artifact來源與內容一致性、實際工具錯誤、Sandbox／資源上限／取消、防重複與 indeterminate 操作保護、安全呈現與合法引用驗證。提供的 fact/artifact 引用仍需有效；沒有引用不再是交付否決。

外部寫入繼續使用既有 approval policy（`approved` 設定仍可自動核准），未新增權限或放寬資料邊界。selector 不再決定 Preview／publication flags；模型按意圖選擇，既有 writer 與明確 request flags 保持。個別任務要求及 publication 授權仍必須遵守。

設定 helper 產生新的預設 prompt，但 apply 時保留既存非空 `defaultSystemPrompt`；離線 mock 驗證不覆寫自訂文字、不修改傳入 payload。本輪沒有 apply。日後配送須另行檢視既存 prompt，不能假定更新 binary 就改掉已保存的舊指令。

## 驗證

證據目錄：`.scratch/llm-flow-removal/`。

- `red.log`／`tests.log`：完整工具目錄及免逐次分析核准的原 source RED→GREEN；跨確認兩回合各 30 tools，無 selector 額外模型回合。enabled-tool 拒絕仍有效。
- `go-all-final.log`：`go test ./pkg/... -skip Redis -count=1` 通過。最終 prompt／skill 變更再由 `check-ask-o11y-autonomous-analyst-verified.log` 在乾淨重建目錄驗證 agent／plugin／mcp。Redis 整合與 race 未作本輪驗收，不宣稱全服務 E2E。
- `patch-stack-final.log`：33 patches 按 installer 順序成功；最後一個是 `patches/ask-o11y-llm-flow-removal.patch`。
- `full-source-match.log`：固定 upstream＋32 patches＋移除 patch，全部 96 個 `pkg` source／test／skill 檔與候選逐 byte 相同（包含兩個舊測試檔刪除）。不是只驗第一 slice 備份。
- `*-delivery.log`：report contract、compose／resolve、可選 inspection／EOF、numeric prose／text-only report、manual／mixed bindings、跨 org/user/session 拒絕、markup／invalid reference／manifest corruption 拒絕等回歸通過。
- `sandbox-self-check-final.log`／`bridge-self-check-final.log`：兩個 MCP self-check 通過；包含 derived output 與安全負案例。
- `check-ml-plotly-panel-plugin-verified.log`：隔離前端重建通過（既有 bundle-size warnings）。
- `browser.log`：新編譯 panel 在 fixture Chromium 中驗證 optional evidence 不崩潰、完整 Plotly、局部錯圖、1440／768、零外部請求／browser errors。**Grafana shell 是 stub，不是 Ask O11y 真 UI E2E。**
- 其餘 metadata／source evidence／Plotly／profile／cost assumption／no-fixed-flow 檢查通過，個別 log 保留。
- 最終 10 個相關檔 primary LSP 無 error；`git diff --check` 通過。Lens 的 test JSON fail-fast、assert message tuple 與已具 async `.catch` 的 fixture parser 誤報已標示；`diagnostic-source-check.log` 保留 source-bound 核對。未改正確程式去迎合 stale diagnostics，亦不宣稱全 repo 無警告。

原先失敗的 Go fixtures、舊 v1／強制 inspection assertions、漏包 skills 的中途 patch、隔離 build 缺少 `PLOTLY_PYTHON`、錯誤 Go 相對路徑及相對 `--out` 訊息錯誤之 log 皆保留；最終驗證如上，未豁免真實失败。一次對 live completion helper 的 `--help` 誤用在缺少必要環境變數時即停止，未建立 Sandbox／呼叫服務。

## 交付界線

既有 OpenSandbox completion 修正保留。沒有 restart／deploy／commit／push、沒有改舊聊天或 Dashboard、沒有重送舊 indeterminate operation。Iris 分類與 California 迴歸的真 UI 驗收仍未完成，母分析任務不結案；需另獲部署及 live 執行授權。
