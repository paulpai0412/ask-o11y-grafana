# Ask O11y：LLM 主導的最小分析執行鏈（提案，未批准實作）

2026-09-12。Main-only，僅來源碼評估；沒有部署、修改產品碼、查詢資料或重跑分析。

## 結論與範圍

建議以 installer 已固定的 upstream `8395ae10c3e38beae56329e4174a14a9a6d4c680` 重新整理最小差異，不在 33-patch stack 後面再追加反向修補。這不是升級 upstream 的提案。

LLM 決定方法、特徵、切分、前處理、工具組合、圖表與報告。Host 只執行權限／隔離／資源限制、資料與產物搬運、真實執行狀態及外部寫入授權。不要固定 ML template、analysis contract、完整性裁判或 report workflow。

「只保留 timeout／UI／skill／prompt」若按 patch 檔名執行不可行：第一個 patch 就混合四類功能與 artifact resolver；上傳、actor/session transport、跨回合 tool results 亦非純 UI。要保留現有安全資料流，仍需要小量 integration／transport glue，或把它移到 MCP；不能以 prompt 替代。

不處理先前模型品質、u1 業務分析等 backlog；不新增通用工作流、狀態機、儲存系統或自動恢復框架。

## 已核對的重要現況

- `scripts/build-install-ask-o11y.sh` 按序套用 33 個 Ask O11y patches；LLM provider timeout 與 OpenSandbox execd completion 是另外的套件／patch。
- 固定 upstream 的 `pkg/agent/loop.go` 本來就提供 RBAC／enabled tools 過濾後的 MCP tool loop；不需要重造 LLM orchestration。
- 最新 `ask-o11y-llm-flow-removal.patch` 已移除 selector 與逐次分析 approval，但大量歷史 delivery／effect／resolver 程式仍存在。
- 最終 source 的 `availableAgentSkills`／`selectedSkillMessages` 沒有找到 production caller（搜尋 build 的 agent/plugin/mcp Go source，排除 tests）。保留 Markdown 或 embed 不等於 skill 真的進入模型 context。
- `pkg/plugin/prompt_defaults.go` 同時存在「分析免逐次确认」與「query/Sandbox 仍需 confirmed Analysis Preview」；設定 helper 保留已有非空 prompt。只換 binary 無法消除保存的舊規則。
- `sandbox-analysis-mcp/server.py::_execute_python_analysis` 仍呼叫 `read_plan_contract`、驗 input audit、做 report preflight／manifest／repair；目前一般 Python runtime errors 回傳 stop，只有 syntax/indentation 有明確修碼指引。
- `artifact-bridge-mcp/server.py::resolve_plotly_bindings` 的新 native figure 路徑依賴 report manifest；舊 execution/index 路徑走 legacy reader。移除 report contract 必須同步調整這個 consumer，不能假設現有路徑已完全獨立。
- `grafana-panels/asko11y-plotly-panel/src/module.tsx` 已經可用 `options.figure` 的 native Plotly JSON 呈現；Plotly plugin 不需要知道模型、split 或分析 contract。

## 33 patches 的處置方向

以下是抽取功能後的退役方向，不是可以直接 git apply 的新清單。

| 現有 patch（省略 ask-o11y- 與 .patch） | 建議 |
| --- | --- |
| dynamic-tools-and-timeout | 拆開；保留必要 timeout、UI、skill 接入與最小 artifact binding；不要整包沿用 |
| upload-datasets | 保留 CSV/XLSX UI、後端代理與大小／session 檢查；不只是按鈕 |
| ml-method-skill、data-understanding、autonomous-analyst | 合成目前真正需要的 advisory skills／prompt；不保留歷史疊加 |
| upload-capability-closure | 移除工具預選／補償；原生完整 authorized tool catalog 足夠 |
| upload-session-header、upload-session-attachment | 抽出 actor/session header、attachment persistence；移除非必要自動工具／參數綁定 |
| plan-ref-repair | 移除 host 猜測／補參數；LLM 使用實際 tool results |
| nlap-authority-and-effects | 移除分析流程 authority；抽出身分隔離及不安全 transport retry 修正 |
| preview-recovery、effect-store | 不保留整套自動恢復機制；保留必要的 effect 身分、狀態及「未知不重送」語義，先用既有紀錄/native readback |
| grafana-session-refresh | 保留必要的 Grafana 原生 session refresh 整合 |
| bounded-autonomy | 移除選擇器、分析 attempts/scope／方法卡控；保留原生總資源上限 |
| approval-default | 不沿用 blanket auto-approve 作為簡化手段；分析免重複批准與外部寫入授權分開 |
| report-dashboard-provenance | 移除報告專用 ownership/recovery 大框架；保留授權 artifact resolution、實際目標 UID 與不可盲目覆寫 |
| semantic-alignment、business-question-binding | 有用的語意／問題記錄留作 context，不作分析准入；合併到 prompt/metadata |
| writer-gates | 移除固定呈現流程限制；保留實際寫入授權、合法內容及目標校驗 |
| selection-closure | 移除 selector 機制 |
| delivery-state、delivery-continuity、llm-turn-control | 移除 host 分析完成裁判、producer/報告流程追蹤；保留原生 tool/SSE error/done 與必要的 UI 連續性 |
| input-continuity、session-tool-history | 以最後的普通 assistant/tool history 恢復為準；不要舊 field whitelist/ref registry；核对模型選擇是否已由 session/native 機制覆蓋 |
| plotly-recovery | 移除分析專用恢復與整段重跑指令；顯示單圖錯誤、保留成功輸出，由 LLM 據錯誤修正 |
| novice-report-slimming、report-interface、report-evidence-cursor、prompt-cleanup | 保留必要 prompt/UI 效果；移除固定 report schema／cursor 關卡，不延續歷史修補鏈 |
| native-plotly-delivery | 保留 native Plotly 接線／錯誤顯示；刪除與舊 delivery-state 的耦合 |
| assessment-status | 移除分析完整性評分／not_assessed 裁判；保留真正工具狀態 |
| llm-flow-removal | 其自由編排方向成為新基線，不再當第 33 層反向補丁 |

另外保留：`grafana-llm-openai-timeout.patch` 的必要 provider timeout；`opensandbox-execd-completion.patch` 的 request/reply/idle 配對修正（直到確實被採用版本的上游修正取代）。後者不是分析 contract。

## 推薦執行鏈

概念圖是資料相依，不是固定步驟或必經流程：

    使用者 → Ask O11y 原生 LLM/tool loop
                      ↕ authorized MCP capabilities
          資料查詢／metadata   通用 OpenSandbox Python   Grafana dashboard write/read
                   frame ref → code/results/figure ref → Plotly plugin

- 不需分析就直接回答或使用 Grafana 原生查詢／Dashboard 功能。
- 需要分析時，LLM 取得 authorized frame，再生成 Python；保留現有 `df`／`emit` 的小型 I/O 介面。無需學新的 ML/report contract。
- LLM 決定是否比較模型、如何切分、是否抽樣／補值、如何解釋；披露變更，遵守個別任務限制，原始來源不覆寫。
- Python 產出數值、文字、表格或 native Plotly figure。Host 回傳有大小限制的真實結果及 opaque refs，不要求必有圖表或標準 report-source。
- LLM 寫 Dashboard layout／敘述，使用 figure ref；host 取回原圖 JSON，不讓 LLM 複製大量陣列或猜 asset URL。保留一個小 resolver，退役 prepare/inspect/compose 的固定報告系統。
- 圖表 renderer 只處理 Plotly，不評估分析品質。失敗圖表不吞掉其他結果。

### MCP 需要同步精簡

1. Ontology 不作固定服務依賴／准入。需用到的單位、欄位、業務定義可以留在 metadata/context，不強制 snapshot/role approval 才能分析。
2. Planner 不再負責 target/features/split/algorithm policy。先重用既有安全查詢功能，將必要只讀 SQL／datasource／response bounds 驗證放在真正執行入口；讓 LLM 不必先取得一張 analysis plan 票券。不可直接改成不受限 SQL 或 Python 直連 DB。
3. Sandbox 只保留通用 Python、輸出與執行狀態；新分析入口不暴露 `execute_ml_contract`、固定 profile、trusted reexport／report repair 工作流。由 Python 套件完成相同計算。
4. Bridge 只留產物讀取／安全 binding。不新增新的 compositor MCP 或另一套 manifest/report contracts。
5. `config/adaptive-mcp-capabilities.json` 與 settings helper 的「恰好五個服務」斷言要一起改，不然停用 ontology 等仍會被設定工具拒絕。

### 最少的必要技術規則

- 真實 actor/org/session 由 host 傳遞；不讓 LLM 填身分或取得 service credentials。
- 資料讀取權限、跨 session artifact 拒絕與來源完整性；這些不評判方法。
- Sandbox 隔離、無任意網路／主機掛載／憑證、CPU/RAM/執行與輸出上限；驗證取消是否真的傳到運算，不只 UI 停止。
- MCP JSON schema、frame/Plotly 可解析性與安全呈現。禁止任意 JS/外部資源不等於限制回歸演算法。
- 保存 code/input/output 的最小出處及執行 ID；成功、失敗、結果未知分清楚。
- 已確定完成的 Python error：回傳去敏且有用的錯誤，LLM 可在同授權資料上修碼；結果未知：先查既有狀態，不盲目重送。
- Dashboard 寫入保留授權、唯一目標 UID、native version/readback。Preview 在目前方案中是已存入 Grafana 的標記 Dashboard，不是零副作用的瀏覽器草稿。
- 圖中嵌入的分析資料會隨 Dashboard ACL 分享；session-private 輸入不會自動讓 Dashboard 也 private，寫入時要確認分享範圍。

## 方案比較

| 方案 | 結果 |
| --- | --- |
| A：直接刪剩 timeout/UI/skill/prompt 四類舊 patch | 不推薦。patch 依賴可能失敗；上傳／session／figure binding 會缺線，Python contracts 卻仍存在 |
| B：從固定 upstream 抽出四類必要功能＋最小 transport/integration 修正，MCP 同步去除分析 contracts | 推薦。沿用原生 loop，LLM 自主分析；不維護新的流程引擎 |
| C：Ask O11y 嚴格只有四類 patch，將 actor/session/figure 接線全部移到 MCP | 可另評估，但不會自動減少總複雜度；MCP 仍需可信 actor/session 傳遞，且可能要更換 writer 路徑。不是此次最小方案 |

四類是交付功能分類，不是為了數字把後端依賴藏進 UI patch。B 的小量整合例外需使用者同意，未視為已授權。

## 推进與完成條件（批准後）

1. 固定目前 source／settings／image 與原 Dashboard 作回退點，整理最小 source delta；不要再層疊歷史 patch。
2. 先走通最小真鏈：Ask O11y UI 上傳 → LLM 自選 Python → OpenSandbox → native Plotly → 新 Dashboard Preview。不以 host 手工呼叫 MCP、手填模型或直接 REST 寫 Dashboard 替代驗收。
3. 依真鏈刪除已失去 caller 的舊分析／報告工作流及新 runtime 的工具宣告，更新短 prompt／skills 並確認實際載入。兩份短 advisory skills 可由既有 prompt 組裝注入，不恢復 selector；若要獨立按需 skill 載入再單獨決定。
4. 只保留必要驗收：上述真鏈；下一回合／reload 可重用結果；Python 已完成錯誤可修；長任務／取消與未知不重送；跨使用者拒絕；未授權寫入拒絕；已發布 Dashboard 可繼續顯示。

成功標準是使用者在 Ask O11y 內完成分析與看圖，不是 patch 數量或 mock tests 的總數。舊 artifacts、sessions、Dashboard 與 effect receipts 不刪除；既有讀取相容性只為實際已存在的產物保留，不擴成多版本框架。

## 證據限制與更正

本文件是 current source assessment，沒有建置新方案，未聲稱 deployed runtime 已吻合或所有 gate 已可刪。

上輪 California 使用臨時 host script 直接呼叫 MCP、手動指定模型／分組，再直接 Grafana REST 建立 Preview；不是 Ask O11y UI 中 LLM 自主編排的 E2E。已存在的結果與 Dashboard 不因此變成虛構，但不能作新方案的 LLM 編排驗收證據。負 R² 是應忠實呈現的結果，並非工具鏈必須阻止出報告的理由。
