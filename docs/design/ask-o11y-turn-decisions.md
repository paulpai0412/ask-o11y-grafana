# Ask O11y：LLM 決定本輪流程，主控驗證邊界與證據

2026-09-10，TODO-13ddb32d。此版依使用者最新決定取代先前「以額外版本化決策ledger控制所有問答」的實作方向。未接線的ledger原型保留在`.scratch/y5-llm-turn-control/deferred-foundation/`，不進正式patch。

最新產品目標與瘦身計畫見 [非專業使用者報告與系統瘦身](ask-o11y-novice-report-simplification.md)：解除非安全流程限制，但 LLM 仍須為不懂分析／ML 的使用者提供白話、可追溯的報告，不以純計算成功代替已要求的報告。

原因見 [手動階段核准缺陷](../verification/y5-manual-stage-boundary-failure.md)。既有 [自主分析師](ask-o11y-autonomous-analyst.md)、exact-call approval、artifact authority及publication要求繼續有效。

## 問題不是單純缺少問答綁定

原selector把「本輪同意執行＋歷史曾要求Dashboard」變成prepare旗標。Loop再把旗標與任意execution輸出相乘，拒絕所有未寫Dashboard的無工具回覆，催促後failed；writer成功又封閉全部工具。分析LLM即使要提出品質結果、等待資料處理核准或讀回Dashboard，也不能自行決定停頓與後續。

這是把粗略的能力選擇結果當作不可修訂工作流程，不是合理的安全邊界。

## 責任分界

### LLM：理解、規劃、決定下一步

- 使用完整可用對話解讀最新回答與最近提案；明確分開長期目標與本輪已同意的工作。
- 動態選工具、方法、繼續／修訂／交付／詢問，沒有固定profile→model→dashboard順序。
- 不因資料或工具成功就宣稱原問題已解決；結束時說明本輪做了什麼、證據、未完成事項及下一步。有真正歧義、缺資料或需新增授權時才問，不要求使用者反覆重述已清楚回答的計畫。
- 本輪品質稽核可正常回答並停下，不能因最終需要Dashboard就擅自執行正式分析或製圖。
- 聲稱Dashboard存在時須有真writer成功或authenticated readback證據；不得編造URL。資料排除、填補、重算、發布仍受既有授權約束。

這些要求直接加入實際LLM request的system內容，避免非空custom prompt令它們未生效；不是增加第二個workflow規劃器或關鍵字router。

### 主控：有界執行與如實呈現證據

- 保留RBAC、工具開關、輸入/owner/session/opaque refs、exact-call approval、missing-policy、effect idempotency及publication審批。
- 能力selector只決定可用工具/skills及寫入時的生命週期保護；舊prepare旗標不再代表「本輪必須產出Dashboard」。選擇writer不等必須使用它。
- 移除execution成功後強制製圖、無工具回覆催促、grafana_preview_missing終止，以及成功writer後封閉所有工具/追加固定發佈問句。LLM可自行選擇讀回、解釋、修訂或結束。
- writer結果與分析覆蓋分開記錄。無writer證據就是未驗證，不把模型文字當writer證據；工具失敗／不確定仍明列。普通回覆/待核准不是執行錯誤，run done不等分析complete。
- 顯示問題可沿用實際成功Query回傳的計畫provenance，不把「確認核准」拿來替代已知的資料問題；這不新增授權或補造未記錄的原始目標。新的Query須清除上一份交付的writer/coverage狀態，普通Dashboard讀回則不清除。
- 主控不以自然語言關鍵字判斷「同意」「要Dashboard」「完成」，也不冒稱能確定性驗證所有自由文字結論。

## 最小修正，不增加通用狀態機

沿用既有對話、工具receipt與approval系統。本輪不新增所有聊天必經的提案工具、強制JSON結尾、第二套授權ledger或固定階段enum。

若未來真有多個同時待答提案或不可逆操作，需要機械綁定時沿用/擴充既有明確approval ID；不能把版本綁定當成自然語言理解或科學驗證的替代品。

`ForceGrafanaPreview`只約束已選擇的寫入保持Preview及正確格式；`ForceGrafanaPublication`仍保護明確的發布操作。這些effect guard不決定是否/何時分析或寫入。

## Plotly能力契約與錯誤恢復（2026-09-10追加）

本次手動session `nUqhifccwU_vykdg1NREVTxDtzFAM9ccJ5DdICwLm5I` 證明另一個獨立問題：Python計算成功，但產生的圖含 `layout.xaxis.tickangle`，被實際Plotly contract拒絕；LLM收到錯誤後依host instruction呼叫generic repair，repair再次驗證相同無效renderable，最後被錯誤分類成indeterminate。這不是資料查詢或計算失敗。

### 單一能力來源

- `ml_plotly_contract.py` 是安全邊界的權威來源；支援的 trace、layout、axis、styling欄位與數值界限不得只散落在prompt。
- MCP工具描述須由該contract的 bounded capability summary 同步產生或驗證。LLM可依工具描述直接產生程式；必要時可查能力工具，但不能把查能力變成固定分析步驟。
- 本需求的安全視覺選項（包括長標籤需要的 `tickangle`）若納入正式contract，必須有界限與正／負測試；不接受「一般Plotly支援所以host一定支援」的推論。未知欄位仍 fail closed。

### LLM如何取得能力（實作契約）

- 根目錄 `ml_plotly_contract.py` 為來源，Sandbox image 的同名檔為配送副本；回歸要求 byte-identical，不允許兩端自行演進。
- `capability_summary()` 從 validator 共用的鍵集合／limits 產生 trace、layout、axis 與 nested keys、固定 config；附值限制與可驗證的最小示例。欄位清單不是「任意 Plotly 值都支援」；例如 marker.symbol 只支援 circle，line／shape.line 為不同能力。
- `execute_python_analysis` 和 `revise_python_analysis` 的實際工具描述直接帶完整摘要，LLM不用先走強制能力查詢步驟。selector可用短描述選工具，但分析LLM收到的selected tool description不得截掉能力。
- 拒絕回覆仍保留原始 error，evidence加上 `error_code`、`recovery_action`、`frame_ref`、receipt refs，contract rejection再附能力摘要。這是指引不是執行授權；修正Python仍經exact-call approval。
- Prompt不再手抄trace／axis allowlist，也不以「report rejected就repair」概括所有錯誤。自然語言指引不能保證模型永不犯錯；host sanitizer保持最終邊界，不做靜默降級／刪除圖表。

### Recovery分流

0. **先識別既有operation**：source驗證後導出既有identity；若已有operation，先reconcile/replay真terminal receipt。未知、缺失或毀損completion仍回傳operation ID與unknown outcome，不得由candidate preflight遮蔽後指示重算。
1. **Contract rejection**：renderable不符合Plotly contract。保留原始成功計算與錯誤證據，但回覆LLM使用相同 `frame_ref`、修正 `python_code` 後重新執行；不可重查資料，也不可呼叫只複製原renderable的generic repair。
2. **Generic repair**：只處理renderable已通過manifest validation、但manifest／新版本持久化失敗的情況。repair前先做deterministic preflight；若preflight失敗，不建立operation reservation，不得標示indeterminate。
3. **真正持久化未知**：仍建立有界operation並 fail closed；保留operation identity，reconcile完成／失敗前不得自動重派。

所有可安全呈現的contract錯誤、repair錯誤及下一步需保留在tool evidence；final delivery不得只顯示籠統的 `not_assessed` 而掩蓋已知的report rejection。原始error保留於授權tool response供LLM修正；host summary只選取有界結構性validator訊息或固定分類／恢復提示，不複製整包JSON或任意raw exception。不使用byte切割文字。相同plan或無關frame的成功不能清除未解決錯誤；新plan/query或對應frame／source receipt的成功report才可取代。

驗收證據見 [Plotly能力與recovery修正](../verification/plotly-recovery-fix.md)；先前 [主代理複查](../verification/plotly-repair-source-review.md) 保留作為故障重現歷史。

## 驗證與複查要求

1. Hyg型真AgentLoop replay：成功品質結果＋純文字下一階段詢問→正常結束，保留回覆、profile_only/partial，不強制writer。
2. selector同樣的prepare=true不能改變上述結果。
3. writer失敗可保留真error及模型說明，不催促模型反覆寫入；真成功後可讀回，不封閉工具。
4. 沒有writer或只有ok:true但無identity時，不產生host succeeded；模型的成功宣稱仍標示未驗證。
5. 原有opaque report、跨session、出版、idempotency、UID保護回歸不得降低。
6. 實際LLM request包含本輪scope/證據/停頓要求，不只測文件文字。
7. 正式patch可按installer顺序重建，核對真正編譯來源。

實作與複查證據見 [LLM本輪控制修正](../verification/ask-o11y-llm-turn-control.md)。預設prompt和設定生成腳本的矛盾製圖／固定發布問句要求也須同步移除；只改Loop不足。

主代理複查須列出仍有的流程強制與必要安全限制，不能稱獨立review。機械測試是mock LLM/隔離工具，不證明自然語言模型每次遵循；部署後仍需新的手動session驗收。此輪不自行部署、重跑使用者分析、修改舊session/產物或Goal。
