# Ask O11y 精簡 source：新手自然語言分析

## 授權與回退

- 2026-09-12 使用者批准建立 branch、按直接維護 source 的方向重新設計並開始實作。
- Branch：`feature/ask-o11y-minimal-llm`；原 `master` 保持 `762c262`。
- 開始修改前 checkpoint：`87e03b532e50e758fcf0ae268ccf6a4d96abaead`，包含之前未提交的 source/patch 修正，不含 `.env`、資料、session 或執行產物。
- Main-only，本機開發與提交；不 push、不部署、不更動正式設定或既有 Dashboard。真 live 驗收於部署/測試授權確認後執行。
- 回退 source 可在乾淨 checkout 另開 rollback branch 指向 checkpoint；不用 `reset --hard`。Git 回退不會自動回退已部署 image、設定或 Dashboard；本階段不改這些資源。

## 使用者體驗

使用者可以直接說「這份資料有什麼值得注意？」、「哪些因素與熱耗有關？」、「能否預測這個結果？幫我做圖」。使用者不必知道模型、特徵工程、切分、MCP、Python、artifact refs 或 contract。

系統從問題和可用資料自主決定探索、比較、統計或機器學習；不強迫每個問題先跑 profile/ML。只對會改變答案的業務歧義、新資料權限或外部寫入範圍提出簡單問題。技術參數由 LLM 選擇，結果先解釋「發現什麼、代表什麼、不能推論什麼」，方法細節按需要提供；不加固定報告章節 gate。

分析授權後不逐次詢問 Query/Python。生成錯誤由 LLM 根據已確定失敗且去敏的錯誤修正；逾時/未知結果不盲目重送。負面結果亦可交付，不為通過 gate 改模型。

Dashboard Preview 目前仍是 Grafana 中已儲存的 Dashboard，建立前確認寫入與分享範圍；正式發布另有授權。分析結果不可因原始 session 私有而誤認為 Dashboard 也私有。

## 最小實作

- `ask-o11y/`：從固定 upstream `8395ae10c3e38beae56329e4174a14a9a6d4c680` 匯入並直接維護，保留 LICENSE。不是 nested Git repository，也不在 `.scratch` 編輯正式 source。
- 沿用原生 Go LLM/MCP loop、登入/RBAC、session/run、錯誤呈現、原生寫入 approval。移除自訂 selector、分析/報告狀態機、品質裁判。
- 移入必要 UI/上傳後端、actor/session 傳遞、tool history、timeout；保留少量安全產物接線。原有知識以短 prompt/skills 真正載入，不重建 skill selector。
- MCP 資料入口驗真正權限/只讀查詢/回應上限；不驗模型、split、feature policy。移除 mandatory analysis plan 票券，不開 Python 直連資料庫。
- OpenSandbox 接受授權資料和 Python，回傳真實結果/圖表與執行狀態。保留既有 `df`/`emit`，不要求 ML/report-source contract；沿用隔離、無任意網路/secret、CPU/RAM/timeout、取消與安全輸出。
- Plotly 接收保存的 native figure；LLM 只寫 layout/敘述與引用，不抄大量數值。Bridge 只做安全讀取/解析，不編排 report。
- 建置從 source 出發，不 clone/apply/reset/source rm；建置與安裝分開，預設不部署。舊外部 provider timeout/execd completion 屬其他套件修正，不因刪 Ask O11y patch 一併撤掉。
- 旧 Dashboard/artifacts/sessions/effect receipts 不刪；只保留它們實際需要的讀取相容性。不新增 runtime framework、datastore 或通用 recovery schema。

## TODO（TODO-aab6376c）

- [x] M0：branch 與回退 checkpoint。
- [x] M1：正式 source／無 patch 本機建置可用；原生 loop + 必要 UI/上傳/session/timeout；沒有移入自訂 analysis/delivery 控制。Go/TS/前端建置通過；不是現行 MCP 整合或 live 驗收。
- [ ] M2：資料 MCP 與通用 Python 執行鏈精簡；取消分析 contract/plan 准入；保留安全查詢、正確 completion/cancel/status、已完成錯誤可修。
- [ ] M3：native figure 直達新 Preview，移除 report workflow；原圖/來源不變、授權/UID/version/readback、舊 Dashboard 相容。
- [ ] M4：新手 prompt/skills 實際載入、設定與工具目錄精簡；custom prompt 明確遷移，不靜默覆寫；移除退役 patch/source/test 接線。
- [ ] M5：實際 Ask O11y UI 上傳→LLM 自選方法/碼→Sandbox→圖表/Preview；reload續談與重用結果、已完成錯誤修碼、負面結果、長任務/取消/未知不重送、跨使用者拒絕/寫入授權、舊 Dashboard。

M1/M4 可以先完成部分，但不得宣稱 MCP 或 live E2E 完成。先前 California 是 main 手工 MCP/REST 驗證，不可當作 M5 證據。舊業務分析與模型品質 backlog 暫停，沒有新增預測品質門檻。

## 驗證與交付記錄

### M1 本機 source/build checkpoint（2026-09-12）

- `2d2c7d4` 匯入固定 upstream；後續修改只移入必要 UI/上傳、session tool history、actor/session headers、MCP timeout/retry 限制與 context sizing，未移入舊 analysis/delivery/selector 類別。
- `scripts/build-install-ask-o11y.sh` 保留原入口名稱，但**現在只建置** `ask-o11y/`，沒有 clone、patch、source reset/delete、安裝依賴、Docker 或重啟操作；舊 `ASK_O11Y_BUILD_DIR` 不再指定正式 source。不要用此名稱推斷它仍會安裝。
- 既有 `node_modules` 僅以 ignored symlink 重用。第一次建置發現共用 webpack cache 包含舊 scratch source，改建置入口使用原生 `--no-cache`；最後 source maps 查核沒有舊 scratch application source。沒有修改 scaffolded `.config`。
- 使用者明確批准本機使用已安裝 Node 24.18.0；未安裝 Node 22、未改正式 runtime。Go 1.26.5，`CGO_ENABLED=0`，不冒稱 race 檢查通過。
- 聚焦回歸發現 upstream 對未知名称且未標唯讀的工具不要求批准；改以非唯讀為批准預設（保留 operator overrides），以測試驗批准 ID 綁定、未批准不呼叫、null/array/string args 拒絕與 host session 覆寫。這不等於已完成 indeterminate operation 防重送。
- 上傳沿用 session ownership/容量上限，改用 Grafana SDK HTTP client，讓真實 org 覆寫配置中的 org，拒絕將讀取失敗當成成功刪除；補 OpenAPI 與 header 回歸。
- 修正移入 Chat 的 memo 缺少 attachment/session dependencies；不是增加新 UI 框架。

驗證證據在 `.scratch/minimal-source/`：

- `go-test-final.log`：`CGO_ENABLED=0 go test ./pkg/...` 全 package 通過。
- `go-vet.log`：`go vet ./pkg/...` 通過。
- `frontend-tests-final.log`：34 suites / 492 tests 通過；保留既有 React act 警告。
- `lint-final.log`：0 errors，9 個 deprecated API warnings。
- `openapi.log`：OpenAPI valid，2 warnings。
- `build-final.log`：`tsc --noEmit`、webpack production（1 asset-size warning）、Go backend、build stamp 通過。產物 `0.3.2+local.3546d78c8241a4ba` 只在本機 dist，沒有安裝。

診斷限制：舊四個 scripts 的五個告警已按當前 body + primary LSP=0 核對並 mark false-positive；turn-end 仍重報。新增目錄的 lens/TS LSP 亦以無 JSX/ES5/缺 React 的配置報錯，但同一 source 的專案 `tsc --noEmit`、Jest 與正式 webpack 都通過。`lens_diagnostics(mode=all)` 最後仍列 Chat.tsx 28 blocking + 其他 warnings，因此**不宣稱 lens 或全 repo 綠燈**；以 source-bound 專案命令作本階段替代證據，不為 stale/錯配設定改 source 或修工具。

M2–M5 未完成：目前仍沒有新的 generic MCP/Plotly binding、完整取消/未知狀態拒重送、新手 prompt/skill context 或 settings migration。既有 patches 暫留作 rollback 記錄但不再被本機 build 使用，待 consumers 同步完成再移除。這個 source checkpoint 不能部署後宣稱整體產品可用。
