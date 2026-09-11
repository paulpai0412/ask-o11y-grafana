# Plotly能力揭露與recovery修正

2026-09-10。主代理實作與自行複查；source-only，不是獨立review或live E2E。

## 回答原問題

原始 `unsupported layout.xaxis key 'tickangle'` 已送給LLM；錯的是host附帶的generic repair指示。Repair只重建manifest，不改Python、圖表或原始receipt，因此同一不合法figure再次驗證仍然失敗。後續indeterminate訊息又遮蔽了真正原因。

本版不依賴LLM記住一般Plotly文件，也不宣稱prompt可以保證零違規。工具能力指引降低錯誤率，sanitizer才是最終驗證邊界。

## 本版修改

- `ml_plotly_contract.py`：能力摘要直接取自validator共用的trace／layout／axis／nested鍵集合和數值常數，包含固定config、值限制、最小合法示例。補出marker、line、shape、colorbar等區別；line只列實際可接受的color/width（dash/shape此前已被styling validator拒絕，並非縮減已接受功能）。
- Sandbox配送副本與root要求byte-identical，`scripts/check-plotly-recovery.py`鎖定；不是兩份各自手工維護的prompt。
- `execute_python_analysis`、`revise_python_analysis`實際工具描述內含完整摘要；無新增能力工具、授權或固定查詢步驟。Selector的短目錄用於選工具；分析LLM收到完整selected tool description。
- Contract rejection仍回傳原始error，並附`evidence.error_code=report_contract_rejected`、`recovery_action=correct_python_same_frame`、原frame/receipt refs和能力摘要。修正程式是新的exact-call approval，不重查資料或盲目重送。
- 只有已通過manifest validation、後續持久化失敗才導向generic repair；分類依實際失敗階段，不僅按例外類型猜測。
- Repair在candidate preflight前先辨識既有operation。既有terminal結果經reconcile重播；未知或無法驗證completion則保留operation ID、原report error與`recovery_action=reconcile_operation`，不指示重算。沒有既有operation時才preflight，通過後reserve。
- Go final summary不再echo整包JSON／任意raw exception，也不byte切割文字。僅显示有界結構性validator錯誤或固定分類，詳細原錯誤仍在授權工具訊息。相同plan與其他frame的成功不清除未解決錯誤；對應frame/source receipt成功report或新plan/query才可取代。
- 預設prompt與設定生成腳本移除手抄allowlist和「report rejected就repair」矛盾。
- 測試boolean檢查保留精確False語意：`isinstance(value, bool) and not value`，不是接受None/0的單獨truthiness。

## 證據

目錄：`.scratch/plotly-recovery-fix/`。

| 檢查 | 結果 |
| --- | --- |
| 保存的修前來源＋新回歸 | RED：`python-red.log`、`go-red.log`；歷史具體故障另見`plotly-repair-source-review.md` |
| `scripts/check-plotly-recovery.py` | PASS：實際RPC能力、angle正負邊界、原error、same-frame修code、immutable receipts、preflight不reserve、既有unknown/terminal、persistence-only repair |
| 真Go AgentLoop＋mock LLM | PASS：完整Python producer工具描述byte-equal進入LLM request；原拒絕JSON不變地成為tool message；final保留結構性錯誤。`go-llm.log`與rebuilt tests |
| `scripts/check-y5-minimal-repair.py` | PASS：不重跑compute/query/profile、lineage/tamper拒絕、真正late write failure保持indeterminate |
| Plotly contract／Artifact Bridge／presentation | PASS：`plotly-contract.log`、`bridge.log`、`presentation.log` |
| Sandbox self-check | PASS：import前设置temporary artifact root；remote executor為fake，未碰live artifacts |
| Settings self-check | PASS：`--local-defaults --out <absolute scratch path>`，未`--apply`；第一次相對out只在輸出relative_to時失敗，未修改live settings |
| 正式patch重建 | PASS：25 patches、213份Go/TS/TSX/Markdown來源byte-identical |
| 重建來源Go tests/build | PASS：`go test ./... -skip '^TestRedis'`、`CGO_ENABLED=0 go build ./pkg`，包含真LLM-request transport測試（LLM為mock） |
| 主要LSP | 10個修改檔零error；cached lens(mode=all)零blocking，不是全專案清潔證明 |

正式新增：`patches/ask-o11y-plotly-recovery.patch`，installer已接線。

SHA256：`d2aad1144974e00ea24e7c629a998c5bcfa9969b28c4a60403f5d817d59b23f4`。

`source-rebuild.json`記錄重建位置與213個來源hash；`final-hashes.json`記錄本版Python/patch/腳本及log hashes。

## 診斷處理與限制

- Auxiliary `no-boolean-in-except`把合法例外tuple／其常數名稱誤報為boolean expression；已讀碼、標記false-positive，語法／primary LSP及實際測試確認。未減少捕捉例外種類以取悅規則。
- Gitleaks列出的scratch Helm `envFromSecret`是Secret名稱引用；Go文件是upstream說明、ECDH測試是固定向量。已逐項讀取／遮罩確認並標誤報，沒有發現可據以要求rotation的真憑證，也未移除開發工具或輪替任何憑證。
- Live OpenSandbox、真LLM自然語言與瀏覽器仍需部署授權後另用新session驗收。能力摘要不是完整Plotly原生JSON schema或渲染保證；目前不支援的複雜樣式／grid／arrangement已明示避免使用，未靜默擴大sanitizer。
- 未執行Redis整合測試（防DB15清理）、race（CGO關閉）、独立review或frontend完整build（本輪無frontend變更）；這些不偽報通過。
- 未部署、重算使用者CSV、重試原indeterminate operation、改舊session/artifacts/Goal、commit或push。原分析與Dashboard交付狀態不因此升級。
