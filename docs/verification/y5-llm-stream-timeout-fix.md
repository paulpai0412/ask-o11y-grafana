# Y5 真實分析的 LLM 串流逾時

## 範圍與授權

2026-09-10，使用者要求主代理修本機服務恢復、真由Ask O11y上傳u1_by_date.csv動態分析，Dashboard呈現整個實際過程（含分佈、相關性），不是固定分析腳本。使用者另明確授權本機修改Grafana LLM插件，僅將grafana-llm-app加入本機開發白名單；不改模型、provider、帳號或其他插件政策。

## 真因與反例

- 正式resources/api/uploads成功，session `nytPyA9uBHhogJGjO-49y8wTfd7qhiEKpy0dr-4R610`。
- 第一次run在工具前失敗：仍運行的gateway引用磁碟已不存在的舊SDK模組。只重啟既有gateway；29 models不變。之後真正discover/inspect/plan成功，證明不是CSV錯誤。
- 執行run `BAIQpJUOsrhsRRkYAqpl3hPg2Y1LlXywCTTAe_peVdg`查詢成功後，在生成後續工具請求時中止。Grafana-llm於04:23:04Z及04:25:04Z各120秒後記錄`context deadline exceeded (Client.Timeout or context cancellation while reading body)`。Ask O11y表層只見`llm_incomplete_stream`。
- 官方grafana/grafana-llm-app v1.0.8、commit `c72f0da8e0f1ad07ba39f6918dfe1ee559b207a7`的`NewOpenAIProvider`使用`http.Client{Timeout: 2 * time.Minute}`。全部34個Go build manifest來源hash與已安裝版吻合；不是猜測其他層的timeout設定。

## 最小修復與驗證

正式`patches/grafana-llm-openai-timeout.patch`只有一行production變更：2→10分鐘，仍有界。附一個真constructor/SSE reader回歸，使用本機假HTTP server，125秒後回覆終止chunk，沒有模型請求或私密資料。

- 原版同測試在120.02秒失敗。
- 修正版收到125秒後的完整終止訊息；pkg/plugin全測試PASS，Linux backend build PASS。
- 本機缺編譯快取，僅取得原go.mod/go.sum鎖定依賴，未升級依賴；後續測試/build使用GOPROXY=off。
- LSP兩個Go檔無error。

證據：`.scratch/y5-llm-timeout/{before-verified.log,after-and-build.log,plugin-tests-final.log,build.json}`。原始offline cache不足的失敗留在before.log，不當作產品反例。

## 部署與信任界線

原插件signature=valid、type=grafana。依新授權，只在compose.yaml的既有unsigned白名單追加grafana-llm-app；新本機stage去除官方MANIFEST，沒有假稱保留官方簽章。其他前端檔案保留，更新backend與來源manifest，加入y5-llm-build.json。

部署前私下比對resolved compose與運行環境，唯一差異是授權的白名單；image不變。只recreate Grafana，不重啟其他服務或改.env。實際運行中的唯一LLM backend `/proc/.../exe` hash與build.json一致；Grafana/database health與HTTP build marker一致，signature=unsigned符合授權。

備份：`.scratch/y5-llm-timeout/rollback/`及volume `/var/lib/grafana/y5-llm-rollback-20260910/grafana-llm-app`。回滾須在無分析運行時恢復該完整官方插件目錄（含MANIFEST），從白名單僅移除grafana-llm-app，再recreate Grafana；不要覆寫後續compose變更、改retention或動其他插件。

## 分析狀態（尚未完成）

沿用原會話及前次成功frame，新run `aFmkKvGgCA1KxaMmlc2NeoEu44OswnFifd4pOlpG3Mg`進行中。沒有因基礎設施測試或部署成功就宣稱分析/Preview完成。後續真流程仍需核對資料品質、母體、混煤語意、方法與圖表數據及解釋；不恢復/完成既有Goal，也無獨立child review。
