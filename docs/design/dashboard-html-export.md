# Dashboard preview：單檔 HTML 報告

## 決策與範圍（2026-09-15）

使用者核准在 **Ask O11y dashboard preview header** 放置「下載 HTML 報告」，更新設計並實作；另明確核准本機啟用固定版本官方 Grafana Image Renderer、專用服務 token、必要 Grafana 設定／重啟與 Ask O11y 部署。

第一版下載一個可離線開啟的 `.html`，包含 dashboard 標題／說明、UID／版本、產生時間、固定絕對時間範圍／時區、目前變數選擇、所有 panel 的圖片與文字說明。圖片、CSS 全部內嵌；不需要 Grafana 登入或外部網路。這是靜態圖像報告，**不是互動 dashboard**；沒有 hover／zoom，表格只保留渲染的可見列，不宣稱完整明細。

「全部」指目前變數選擇的所有 panels，包含畫面外、collapsed rows 內與 repeat instances，不是列舉所有可能的變數組合。不得默默跳過；單一項目失敗必須在 UI／報告明示不完整及對應項目，不把 partial 報告稱為完整成功。

## 使用流程

1. 僅 dashboard preview 提供 header 按鈕；Explore 等其他分頁不提供。原有分頁、開啟 Grafana、關閉 preview 行為不變。
2. 匯出時固定目前 iframe 的 dashboard、org、時間與變數，不只使用訊息中最初的 URL。保存的 dashboard 與版本是報告來源；不以匯出動作儲存／修改 dashboard。
3. 呈現處理中狀態與錯誤，避免重複啟動；取消／關閉／換頁不留下錯誤歸屬或未清理下載資源。
4. 成功下載單檔 HTML；失敗項目與圖像／表格限制明示，下載前告知檔案已離開 Grafana 的 RBAC 保護，請審慎轉傳。

## 技術邊界

- 重用 Grafana 已有的 dashboard read API 與原生 `/render/d/...`／`/render/d-solo/...` 圖片路徑，由目前使用者的 Grafana session 授權。不要讓 MCP／服務帳號替使用者繞過 RBAC。
- 實作採原生逐 panel `/render/d-solo/`，不走 full-page 截圖：官方整頁路徑只捲動，不能保證 collapsed rows。遞迴讀取 saved panels，展開目前選擇的 row／panel repeats；All 選項使用 iframe 的即時 variable options，而非可能過期的 saved defaults。无法列舉時中止並提示選擇明確值，不默默略過。
- 透過 iframe 的 Grafana runtime `getTemplateSrv()` 與當前 dashboard scene，固定畫面實際使用的絕對時間、變數選擇及版本。此接點已在 Grafana 13.1.2 實測；不是跨版本不變的 API 保證。未載入／dirty scene／版本不符會明確報錯。
- 圖像只有 PNG 等可信 raster data URL；標題、說明與變數以純文字 escape。HTML 不帶 dashboard JSON、SQL、查詢模型、cookies、service token、script 或外部資源。對不可信 HTML 文字不得直接插入 markup。
- 固定時間／篩選條件不等於資料庫交易快照：多張圖的查詢時間可能不同，報告須區別「固定查詢範圍」和「資料原子快照」。
- 不新增 MCP、LLM 流程、直連 DB、Grafana core patch、公開分享、排程、額外 datastore 或全量表格匯出。

## 已確認的環境事實

- 本機 Grafana 為 13.1.2。啟用前 `/api/frontend/settings` 回傳 `rendererAvailable=false`、空 renderer version；啟用後為 true，且已實際成功渲染 PNG。
- 官方目前穩定 Image Renderer 為 5.12.3（2026-09-08 發布）。採服務，不使用已棄用的 renderer plugin。服務 token 不進 Git；renderer 不公開 host port。
- 主機記憶體有限，不能把官方 production 建議的 16 GiB renderer 資源視為已具備。本機驗證採有界資源／單次匯出，資源限制與實測另記，不宣称 production sizing 驗收。

## 完成條件

- 真實 Ask O11y preview header 點擊可下載含全部 Product Category dashboard panels 的 HTML。
- 檢查時間／變數／主要金額與目前 dashboard 一致；報告離線開啟且沒有網路請求。
- 單元／UI regression 覆蓋 escaping、URL／org／time／variables、collapsed／repeat inventory、錯誤與下載生命週期。
- 權限不足不會產生越權圖片；dashboard 原版本／內容／分享設定不變。
- TypeScript／lint／相關測試、source review、實際部署讀回與瀏覽器驗證；build 與 mock 不是 E2E。使用者後續要求改由主 agent 全程執行，本次 source review 為主 agent 自查，不稱為獨立審查。

## 參考

- <https://grafana.com/docs/grafana/latest/setup-grafana/image-rendering/>
- <https://grafana.com/docs/grafana/latest/visualizations/dashboards/share-dashboards-panels/>
- Grafana 13.1.2 `public/app/features/dashboard-scene/sharing/ExportButton/utils.ts`：官方整頁 render URL 使用 `height=-1`、`fullPageImage=true`、絕對／現行 URL 參數。
- <https://github.com/grafana/grafana-image-renderer/releases/tag/v5.12.3>

## 使用與部署

1. 開啟 Ask O11y 的 dashboard preview，點 header 的下載圖示 **HTML**（tooltip／accessible name：`Download HTML report`）。
2. 閱讀權限／靜態圖像提示，按 **Generate report**；可取消，不會改 dashboard。
3. 產生完成後按 **Download HTML report**。部分圖片失敗則明示 **Download incomplete HTML**；全部失敗不提供空報告。

`compose.yaml` 固定 renderer `v5.12.3` 與 image digest。沿既有 host network，服務僅聽 `127.0.0.1:8081`，callback 為本機 Grafana。`GRAFANA_RENDERER_TOKEN` 保存在 Git 忽略且 mode 0600 的 `.env`，不寫入 source／HTML；缺值時 Compose 明確拒絕啟動。renderer 限 2 GiB、2 CPUs、單張並行，`GOMEMLIMIT=256MiB`。匯出上限為 100 張圖、48 MiB PNG（base64 後較大），不靜默截斷。

回退時先使用 `.scratch/html-export/plugin-before/` 還原部署前 plugin；移除本次 Compose 的 renderer service 與三個 `GF_RENDERING_*` 設定、停止 renderer，再重建 Grafana。不要刪 Grafana volume／dashboard／session；專用 token 不應轉成其他用途的憑證。這是回退方法，尚未執行回退。

## 驗證與限制（2026-09-15）

- 真實 Ask O11y session `jPGSJ-hC5PMefJEVC3_eNEzG5aybZWWDb7UFBpX7p1o`：header → Generate → Download，實際保存 `.scratch/html-export/product-category-report.html`，390,452 bytes；包含 1 個說明＋6 個資料 panels。下載後仍留在 Ask O11y，而非導航離開。
- 報告 UID `ask-o11y-preview-cat-sales-a7f3`、version 1、2011-05-31～2014-06-30、UTC。已視覺检查圖表／表格數字與原 dashboard 顯示相符（沿用原 panel 的小數位／單位格式，非增加 raw precision）。
- 本機 file:// 開啟，7 張內嵌 PNG 皆可解碼；offline 狀態下 `navigator.onLine=false`、7/7 圖可見、resource requests 為空。無 script／iframe／外部資源標籤，未包含 raw SQL、renderKey 或授權標頭。`offline-verified.json`／`html-inspection.json`／`offline-report.png` 保存讀回。
- Native read 未登入為 401、render 未登入導向登入；直接 renderer 無 token 為 401。Dashboard JSON before/after 完全相同、version 維持 1、folder `dataflow-discovery`，public-sharing read 404。沒有新建／修改／公開 dashboard。
- 最終前端 36 suites／514 tests、TypeScript、scoped ESLint 通過；全專案 lint 0 errors／9 個既有 deprecated warnings。Go 各 package tests 與 build 使用既有 `golang:1.26.5` container 通過，未安裝 host Go；production webpack 通過（既有 asset-size warning）。最終部署的 module SHA 與本機 dist 相同。相關記錄皆在 `.scratch/html-export/`。
- 真 live 驗證為 Admin＋此 7-panel dashboard。collapsed／repeat／All、stale version、跨 org、取消／卸載與輸出安全有 unit regression；沒有新增其他使用者或 dashboard，故沒有把這些稱為全部 live 驗證。Editor／Viewer 真實登入、其他 panel plugins／Grafana 版本與 production 容量未實測。
- Renderer HTTP 200 只代表得到圖像；query error／no-data 可能呈現在圖像內。UI／報告提醒人工檢查，不以 PNG 成功宣稱資料查詢全部成功。

### 本次修正的真實下載缺陷

首次 UI 下載被 Grafana 的 document-level SPA link interceptor 攔截：它對沒有 `target` 的 anchor 呼叫 `preventDefault`，且未辨別 `download`；相同 HTML anchor 在一般頁面可下載，在 Grafana 中卻導航到 Blob URL。實際事件證據為 capture `defaultPrevented=false`、bubble `true`，stack 指向已部署 Grafana `HTMLDocument.Ju`，並讀取該 handler 核對。

下載 anchor 原本不必掛入 DOM，移除該不必要掛載後，原生 browser download 不再經過 document handler；不改 Grafana core、不加攔截器／重試／下載 fallback。已補回歸與真實 header 下載驗證。UI 最後將 header 按鈕縮短為「HTML」、保留完整 tooltip／accessible name 及原有標題換行方式，避免為匯出功能另改既有 pane layout；未改匯出資料路徑。

狀態：本機實作、部署與上述實際下載／離線檢查完成；本次未 commit／push。
