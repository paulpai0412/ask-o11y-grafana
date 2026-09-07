# Ask O11y：按需能力重選與有界運算授權

## 交付範圍

2026-09-07，接續 [自主分析師指引](ask-o11y-autonomous-analyst.md)。使用者要求完成來源後
再一起重部署。本批不安裝、重啟、套用 Grafana 設定、接觸 live data 或發布 Dashboard。

新增最後一層 `patches/ask-o11y-bounded-autonomy.patch`，由 installer 在 analyst patch
之後套用，總 stack 為 14 patches。共用 `.scratch/ask-o11y-release-build` 保持未改；
實作／測試在隔離 clone。設定 helper 的 custom prompt 也同步更新。

## 同一 LLM 按需重選

`ask_o11y_select_capabilities` 是 AgentLoop 本地工具，不是新 MCP server 或另一個 planner。

- `{}` 回傳本 run 已經過 RBAC、server selections、exclusions 過濾的 catalog 與 embedded
  Skill names/descriptions。未連接、未授權、停用的工具不因重選取得權限。
- 同時提供 `tools`、`skills` arrays 會**取代** active selection；未知名稱、重複項目、
  額外欄位及超額會拒絕，失敗不清空既有選取。上限沿用 24 tools／2 skills，不加配額。
- LLM 在看到能力缺口時自行使用，不是每步固定重選、不使用 keyword analysis router、
  不多呼叫一個模型。既有 datasource-family 保守限制保持不變。
- 每次模型請求才注入當前 Skill；不累積舊 Skill 進歷史。near-limit、truncation、preview
  nudge 也保留當前 Skill 並做 token trimming。
- 執行仍檢查 active selection 與既有 RBAC/settings。Publication 模式不提供本地重選／
  scope 工具；成功 Preview 後關閉本 turn 的工具與 scope，不能從同一批 tool calls 繼續操作。

## 有界授權，不是任意程式授權

`ask_o11y_approve_analysis_scope` 透過既有 approval UI/broker 請使用者核准：

- 一個**本 run 成功 Grafana Query 回傳的 frame**；prose、歷史或自行捏造的 ref 不算。
- 使用目的、允許的 deterministic compute tools、`max_calls`（不超過本 run iteration limit）。
- 僅 `sandbox-analysis_profile_dataset` 與 `sandbox-analysis_execute_ml_contract` 可納入。
  ML 執行的 `contract_ref` 必須等於取得該 frame 的原 query plan。
- scope 是 run-local 記憶體狀態，綁定 org/user/session；不序列化成 history、不重用 tool-wide
  grant，不在新 run／程序重啟後復原。重新提出／撤回 scope 先清除舊 scope，拒絕不保留授權。
- 每次 scoped dispatch **先扣除次數**；失敗／不確定結果也扣，不因 retry 重置。耗盡或不符
  範圍，回到新的 exact-call approval；使用者拒絕後不執行。原 sandbox resource limits、
  plan/frame provenance、ML contract validation、full-data、receipt guards 均保留。
- 不能新增 query/data、改 ML contract、寫入或發布。`tools: []` 只撤回，不授權。

**自由 Python 不適用可重用 scope。** 現有 Python tool schema 無法證明任意程式遵守
同一 target/features/split 契約。不能只比對 frame 就宣稱已驗證，因此每次 generated-code
執行仍須 exact-call approval。方法與程式依然由 LLM 設計，使用者只確認，無須教它分析。
這是授權邊界，不是固定分析流程；不建立 AST 關鍵字封鎖器或假冒語義驗證器。

用途文字是給使用者理解的目標，host 只能確定性核驗資料 ref、工具、契約、actor 與次數；
不聲稱程式能判斷任意業務語句是否被滿足。每個範圍都是明確確認，不是從「請分析」推定。

## 核准 ID 防重放

Scope、ordinary exact-call approval（含 scope 耗盡後 fallback）、以及 scoped auto-resolved
trace event 全部使用 **host 產生的新 opaque ApprovalID**。模型 ToolCall.ID 只留在
ToolCallID 作展示，不再是 broker key。核准回覆的 ID 必須吻合本次 request；取消不执行。

原因：in-memory／Redis broker 都會依 run+ApprovalID 回傳既有 resolved decision。
沿用模型 ID 可讓新 frame／工具／預算或 scope 外呼叫誤用舊核准，並造成 UI trace dedup。
UI、route、broker 既有格式接受新的 host ID，沒有改 broker 授權 API 或使用者身分判定。

## 回歸與審查

執行（離線來源重建、local HTTP mocks，無 live 模型／Grafana／資料）：

```bash
python3 scripts/check-ask-o11y-autonomous-analyst.py
python3 scripts/configure-ask-o11y-workflow-tools.py --local-defaults --self-check \
  --out "$PWD/.scratch/autonomy-evidence/settings-dry-run.json"
bash -n scripts/build-install-ask-o11y.sh
```

新版 checker 重建完整 14-patch source，執行 agent/plugin/mcp 全 package tests。
原 `check-ask-o11y-patch-stack.py` 會比對共用 release-build；重部署前該 checkout 尚是
舊版本，所以不應拿它的 source mismatch 當新版重建失敗。重建後可再使用原比對器。

測試覆蓋：

- catalog 查詢、replacement、未知／停用／重複／額外欄位與配額拒絕。
- Skill 不累積；replacement 後 near-limit、truncation 及 preview nudge 不漏掉 Skill。
- forged／失敗 frame 不取得 scope；跨 frame/plan/session、額外 context key、query/write
  不能使用 scope；新 run 沒有 scope；拒絕／錯 ID 不產生 grant。
- 重複模型 ToolCall.ID、broker-like cached decision、增加 frame/budget 的重放負例。
- 真正 Run → approval → MCP seam：query 確認、重選、scope 確認、兩次 scoped compute、
  第三次要求獨立確認且遭拒絕；MCP 實際只收到兩次 compute。
- Python 不進入可重用 scope；已核准 ML 仍檢查 exact plan；scope budget 不能篡改。
- 原 agent/plugin/mcp regression 全跑；這不是 live LLM 或統計品質驗收。

第一輪獨立 reviewer `7a7be414-0c2b-47be-99f0-f1004a733888` 找到 P0 approval replay、
P1 arbitrary Python scope identity、P2 nudge 遺漏 Skill；三項均接受並修正。新測試放回
第一輪 candidate 時，重放與各 nudge 路徑確實失敗。第二輪 reviewer
`e18a5f06-b9a2-41fd-9b35-28b883b59b4d` 已確認三項 criteria 均 met，未發現剩餘可觸發
的 P0/P1/P2 阻塞問題；parent 核對最終來源與完整重建結果後接受本批來源交付。
審查與來源指紋保存於 `docs/verification/ask-o11y-bounded-autonomy.json`。
這不代表 live LLM／部署／UI 已驗收。

Team mission：`42e2bc1a-a6c7-4ca0-b05f-2ee7836a593f`。整體 all-role 預檢仍有既有
`team.docs` 缺 skill；本次使用通過原生 targeted handoff preflight 的既有唯讀 reviewer，
無安裝／變更工具、角色或模型。原始證據在 `.scratch/autonomy-evidence/`。

Race test 未通過環境前置：預設 CGO disabled；明確啟用後仍缺 gcc，未安裝 compiler。
記為 unavailable，不冒稱 race-clean。其餘 Go/LSP/重建檢查見 final evidence。

## 統一重部署前注意

來源完成不代表 running plugin 已更新。請保留並審閱組織 custom prompt，再決定如何
遷移；不能為改 prompt 以 fresh payload 盲目覆蓋組織工具設定。此輪未執行 installer 或
`--apply`。部署後仍需用自然語言案例驗證分析品質與 approval UI：

1. 使用者只給決策問題，LLM 自主提方法，而不是問演算法。
2. 能力缺口時重選，而非停在 profiling；沒有缺口不增加重選呼叫。
3. 明確同意有界 scope 後，只有允許的 deterministic compute 免逐次核准。
4. 變更 frame/ML contract、自由 Python、額外 query/write 必須另行確認；取消／拒絕有效。
5. 受眾切換重用同一證據、改解釋深度而不改事實／不確定性。

這些 live 行為仍待重部署後驗證，不由 mock、review 或報告生成自評替代。
