# 第一批：問題理解與 ontology 對齊

2026-09-08。使用者核准實作後，由主 agent 單獨處理。範圍是分析前的問題／語義對齊，不是完整需求—結果驗收或新分析方法。未部署、未改動 ontology snapshot 內容、未提升任何欄位或使用者權限。

## 實作

- `ontology_contract.py::field_view` 保留來源明確提供的 display name、aliases、definition/description、operating limits；不從欄名、相關性或 LLM 文字推導。
- `ontology-mcp/server.py::resolve_concepts` 支援這些明確登錄的名稱／別名，回傳 dataset-qualified candidates、完整候選數與截斷旗標。多義詞不自動選第一個；未知詞不靠 keyword 補答案。
- 既有 `get_semantic_context` 新增 `context.alignment_basis`：來源語義指紋、欄位覆蓋率、未記錄的定義／單位／availability／lineage／operating limits、未核准角色、source snapshot identity 與明示的 causal/operational/authorization knowledge limits。註冊資料另保留 bounded dataset evidence；上傳資料始終維持 observed，不能靠 hints 自填 approved 升級。
- `intent_status=proposal_not_confirmation`、`role_status=recorded_metadata_not_task_selection`、`actions_granted=[]` 是固定的權威界線。工具的 intent 是待確認描述，不是可信的使用者核准；語義指紋也不是 approval token。沒有新增執行授權或平行 intent store。
- 明確拒絕重複／未知欄位、重複來源欄位 identity、超額欄位與過大回應；不偷偷截斷 context 假裝完整。
- Ask O11y 預設 prompt、data-understanding skill 與配置產生器同步引導：先用白話描述問題、母體／期間、怎樣算有用的答案，再解釋方法；把已知事實、提議與重要未知分開。只詢問與本題相關的缺口，不把所有 metadata gaps 變成必填問卷，也不要求新手認證工程安全範圍。

這些文字沿用現有 Analysis Preview 與 approval/version/scope 邊界，不新增固定步驟、固定圖表、固定 target 或模型路由。技術細節放在白話說明之後，並非刪除精確 scope。

## 可重現交付

Go default prompt 與 embedded skill 透過 `patches/ask-o11y-semantic-alignment.patch` 及後續 `patches/ask-o11y-business-question-binding.patch` 交付，安裝器目前為 19 patches。沒有執行會安裝／重啟的 `build-install-ask-o11y.sh`。

`check-ask-o11y-patch-stack.py` 現在也比較重建後的實際 prompt/skill bytes。修正舊 analyst check 對「最後三個 patches 必須固定」的過期假設，改為驗證必要 overlays 各一次及相依先後；仍從實際 installer 重建並跑 Go regressions。

## 驗證

- `scripts/check-semantic-alignment.py`：RED 重現別名無法解析；GREEN 覆蓋多義詞、未知詞、來源定義、缺口、未核准角色、metadata coverage、source snapshot/knowledge limits、語義變更指紋、讀取不改來源、上傳 metadata 授權入口、無核准授予、重複／過量／過大輸入與輸出。
- `check-upload-auto-snapshot.py`、`check-query-planner.py`、`check-security-negative-contracts.py`、`check-ml-no-hardcoded-report.py`。
- Ontology `--self-check`、配置產生器 `--self-check`（僅寫本地 dry-run JSON）。
- 重建 19-patch source 並跑全部 `go test ./pkg/...`；另跑 analyst prompt/skill/approval regression。
- 受影響檔案的 Python/Go targeted checks、`git diff --check`、沒有 staged files；最終批次另記錄 `sandbox-analysis-mcp/ml_autoresearch.py` 的 Python LSP timeout，沒有 diagnostic returned，不把它宣稱為 LSP clean。

原始 logs、baseline diff、重建 helper 與 dry-run payload：`.scratch/semantic-alignment/`。baseline diff SHA256：`b0945e47614bda67e2e14e5c0ee9f447fcd733db5812b9f134da9cf3f73a318d`。

## 限制與下一個核准點

- 這是 metadata/runtime 輸出與 prompt 組裝的機械驗證；沒有真 LLM 對話、新手使用測試或瀏覽器驗收。Prompt 指引不是新的語義 runtime gate。
- 未填補現有 snapshot 缺少的專家知識、別名、公式或安全界線。沒有記錄的知識維持未知，不能因程式支援該欄位就宣稱已具備製程專家知識。
- 原有模型/欄位政策、固定 legacy validator 的限制仍在；沒有把新提議變成已核准分析契約。
- 運行中的服務／plugin 設定尚未更新，現在手動操作不代表測到這批修改。未執行 build/install/restart 或 settings apply。
- 需求與實際結果逐項核對、可信分析能力擴充、結果敘事與真實案例驗收仍是後續批次。既有未完成獨立 review／部署 gate 未被取消。
