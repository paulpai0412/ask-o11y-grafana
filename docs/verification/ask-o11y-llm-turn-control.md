# Ask O11y：本輪決策修正與主代理複查

2026-09-10 · TODO-13ddb32d · **source-only，未部署**。

## 修正

依 [設計](../design/ask-o11y-turn-decisions.md)，分析LLM決定繼續、說明、修訂或詢問；能力selector不再用歷史Dashboard目標強迫本輪交付。

- `loop.go` 移除成功execution後的強制writer、兩次nudge、`grafana_preview_missing`、writer後封閉所有工具及固定發布問句。保留正常回覆及既有真正錯誤／資源上限處理。
- 實際system request必定附加本輪要求，即使使用custom prompt：解讀回答所對應的提案、保留條件、分開本輪與長期目標、說明證據／限制／待決事項，不擅自擴大授權。
- `delivery_state.go` 將writer證據與分析覆蓋分開。裸`ok:true`、錯誤／無identity結果不能成為writer succeeded；缺writer明示`not_verified`。模型文字不升級host狀態。
- Query回傳的真實計畫provenance可保留顯示問題；新Query清掉舊交付的writer／coverage，普通Dashboard讀回不清除。Query metadata不授權、不提升分析coverage。
- 預設prompt及 `scripts/configure-ask-o11y-workflow-tools.py` 同步移除「execution turn一定製圖／立刻停止／固定問發布」的矛盾要求。
- 未接線decision ledger移至 `.scratch/y5-llm-turn-control/deferred-foundation/`；sessionstore還原為既有patch stack版本。沒有新聊天狀態機、關鍵字分類或第二套授權系統。

正式交付：`patches/ask-o11y-llm-turn-control.patch`，SHA256 `30968f7d4d65a83b7ab1801769eafe5e257abc6da24941901f4baba42446fc89`。Installer已改用此存在的patch，不再引用不存在的foundation patch。

## 主代理 review（不是獨立 review）

| 發現 | 處理／證據 |
| --- | --- |
| selector旗標使稽核正常停頓失敗、增加無必要LLM calls | 刪除主控路徑；同一prepare=true的真Loop情境修正前紅、後綠 |
| 成功writer後無法讀回 | 移除工具封閉；真Loop收到get_dashboard_by_uid呼叫 |
| 程式改了但提示詞仍強迫製圖／停止 | 同步修正兩個prompt來源；custom prompt下實際request仍包含本輪要求 |
| 裸ok:true可冒充writer驗證 | 需要非空UID及非錯誤結果；負例與正常native結果回歸 |
| 新增Query上下文後可能沿用先前writer／profile狀態 | 另加紅測試 `query-evidence-before.log`，修正重置後綠；只重置證據，不決定下一個工具 |

仍保留的機械限制是RBAC、工具allowlist、exact-call審批、opaque ref與session/owner、Preview／發布寫入正規化、receipt去重、資源上限及既有ML/data-policy驗證。Report工具的opaque輸入依賴也是契約要求，不是所有分析必經順序。未更動這些守門器或模型／provider／權限。

## 驗證

證據目錄：`.scratch/y5-llm-turn-control/`。

- `before.log`：原Loop對新增情境失敗，含missing-preview、額外calls、讀回受阻及缺system要求。
- `final-targeted.log`：5種真Loop／mock LLM情境、writer identity、Query證據重置、模型自主後續操作通過。
- `producer.log`、`producer-fixture.json`：真Python public producer、隔離fixture；未執行使用者資料或retained generated Python。12個producer×終止方式replay通過。
- `rebuild.log`、`source-rebuild.json`：從base `8395ae10c3e38beae56329e4174a14a9a6d4c680`依installer順序套24個patch；212個Go/TS/TSX/Markdown來源逐byte相同，無ledger原型。最終重建目錄 `rebuild-7fe5vs94`。
- 重建目錄 `go test ./... -skip '^TestRedis' -count=1`（設定producer fixture）與 `go build ./...` 通過，涵蓋agent/mcp/plugin/openapi/rbac；見 `rebuild-tests.log`、`rebuild-build.log`。
- `plotly.log`：既有Plotly安全回歸通過。`settings-check.log`：設定生成器self-check通過，未apply。第一次使用相對`--out`觸發既有`relative_to`錯誤；診斷後改用絕對路徑，未改無關邏輯。
- 8個本輪檔案primary LSP無error；最終Query變更再驗2檔。`lens_diagnostics(mode=all,severity=error)`於53個session檔案無未處理error，**不是全專案掃描**。
- 舊Python測試的18個aux ast-grep findings經讀碼確認為精確`is False`斷言及測試應向外拋出的JSON失敗，已記錄false-positive disposition；未加catch或改弱斷言來消音。
- Installer只做 `bash -n`，未執行安裝。相關檔案 `git diff --check` 通過。

## 界線與待驗

- **未部署、未重啟服務、未改既有session/artifacts/Goal、未commit/push。** 原Preview仍partial，歷史BLOCKED不改寫。
- Mock LLM證明主控不再強迫流程，不證明真LLM每次都正確理解條件或說實話。自由文字中的錯誤主張不由關鍵字刪除；host metadata及警告不會因此變成verified。
- `OriginalBusinessQuestion` legacy欄位仍保留原始本輪輸入；`BusinessQuestion`可取已驗證工具上下文。無來源時不補造「完整原始目標」，也沒有新跨回合ledger保證。
- 舊Redis測試因會操作localhost DB15而明確跳過；本輪已移除ledger，未新增Redis邏輯。環境無CGO/gcc，未跑race detector；不將以前的race-unavailable說成通過。
- 未改frontend，未做新的browser驗收。使用者另行授權部署後，仍需全新session以正常自然語言走「只稽核→附條件核准／要求修訂→後續分析→可選Preview／讀回」；不可用人工修訂prompt或舊artifact續跑代替。
- 獨立review未執行（使用者main-only）。此文件是主代理source review及機械驗證，不是runtime或完整分析驗收。

## 簡短回顧

流程控制與prompt須一起檢查；一次writer成功後的讀回也是合法LLM下一步。取消流程強制時，仍須測證據狀態不會沿用到新資料。不要為了防模型虛報，反過來把合法的稽核／詢問強迫成完整交付。
