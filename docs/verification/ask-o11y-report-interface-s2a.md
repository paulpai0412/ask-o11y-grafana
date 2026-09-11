# Ask O11y S2a：最小敘事介面（source-only）

日期：2026-09-10。母任務 `TODO-01ba5263`，本片 `TODO-34db0647`。

本片完成報表敘事契約及讀取端相容修改，不代表 S2 全部完成或產品／原分析 Goal 完成。主代理實作與自行複查；沒有獨立 review、部署、真 LLM／OpenSandbox／browser、新資料分析、舊 operation 重試或 commit/push。

## 有效變更

- 沿用 `ask-o11y-report-synthesis-v1`。Panel 必填欄位由十二個減為七個：`artifact_id`、`view_ids`、`headline`、`observation`、`interpretation`、`limitation`、`evidence`。
- 跨圖關係、下一步、逐 view 敘事可省略；若有不同 view 需要解釋，可只提供選取 views 的子集，仍須各自有效的觀察／解讀／限制／證據。不是把同一句話複製到每個 view。
- Section 必填六個減為四個。Host 只填 `collapsed=false`、空 narrative arrays、`priority=supporting`、`preferred_width=full`、缺省 `visual_observation=null`，不生成預設敘事。
- 先驗形狀／數量／文字／fact refs，再複製與 normalization；不修改 caller input 或歷史產物。顯式空字串／不合法值仍拒絕。
- Bridge schema 使用 validator 的 required-field 常數；全 report inspection、selected-view membership、comparison／uncertainty citations、lineage、owner/session、approval、Preview 與 publication gates 保留。Vision receipt 不再強迫寫視覺形容詞；spec-only 仍不能提交非 null 的 visual observation。
- Compositor 與 dashboard write gate 接受 minimal／legacy payload。刪除 repo Python 呼叫搜尋中無 caller 的兩個 private HTML narrative helper；實際 UI 仍沿既有 plugin。
- Plugin 只做相容修正：省略 optional 欄位時不畫空標題；沒有 view narratives 時，單图也顯示主要白話說明；補上既有圖表容器的 `role=figure`。字型、色彩、圖表、折疊互動沒有重設。
- Default prompt、兩份選用 skill、local settings generator 同步取消 mandatory per-view 敘事指引。沒有新增 planner、自然語言 gate、runtime skill 或 MCP tool。

## 驗證與直接證據

證據目錄：`.scratch/report-slimming-s2a/`。

| 檢查 | 結果／邊界 |
| --- | --- |
| 修前 RED | `contract-red.log`：minimal section 被舊 shape 拒絕；`bridge-red.log`：公開 schema 仍要求全部欄位；`render-red.log`：單圖缺少 view captions 時主解釋消失 |
| Shared synthesis contract | `contract.log`：新預設、舊完整內容相等、input 不變、未知／缺必填／空 optional／重複或未知 view／數字與注入負例；非法 shape 在 deepcopy 前拒絕 |
| 真 Bridge RPC/store → compose → resolve | `bridge.log`：minimal 與 legacy 報表、有／無逐 view 敘事；真 fact 格式化為 `42.0%`；缺解釋、缺 inspection、偽造 fact、spec-only 視覺宣稱等仍拒絕。資料／收據都是隔離合成 fixture |
| 舊版相容 | `legacy.log`：本片開始前的三個實際 Python module 與目前版本，使用同一未修改 sanitizer，產生完整 v1 fixture 的 canonical dashboard bytes 完全相等。不是所有歷史報告或混版服務的保證 |
| 寫入与科學證據 gate | `dashboard-contract.log`、`write-gate.log`、`coverage.log`：minimal payload 仍須 comparison／uncertainty facts、正確 metric／objective、原 provenance 與授權；partial 不等於 complete。View cardinality 放寬後，selected-view 唯一性改為明確檢查 |
| 真 React component SSR | `render.log`：image／Plotly 無逐 view 敘事仍有主要解釋；optional 下一步不產生空標題；直接渲染 Bridge resolve 產生的 minimal／legacy options，保留解釋、限制與 fact。Grafana hooks／Plotly pixel rendering 為 stub，**不是 browser 視覺驗收** |
| 前端編譯與安全束縛 | `panel-build.log`、`panel-static.log`：隔離 source copy 的 typecheck／webpack build，mode／theme／view split 與 bundle 無 eval/new Function 檢查。既有 Plotly bundle 約 4.31 MiB，有 webpack size warnings |
| 其餘回歸 | `compositor.log`、`repair.log`、`plotly.log`、`approval.log`、`recovery.log`、`no-fixed-flow.log`、`settings.log`；含 generic recovery／immutable lineage／no redispatch 等 |
| 正式 Go 配送 | `package.log`、`patch-stack.log`、`source-rebuild.json`、`rebuilt-tests.log`、`rebuilt-build.log`：**27 patches／213 Go、TS、TSX、Markdown sources byte-identical**；Go 全套 `-skip Redis -count=1` 與 CGO=0 build 通過，使用 local toolchain／GOPROXY=off。不是 Redis／race 整合測試 |
| 靜態檢查 | 相關 primary LSP 無 error；`lens_diagnostics(mode=all)` 最後回報僅六項前階段文件／shell warnings。合法 exception tuple／精確 False guard 已讀碼並標誤報，舊 turn-end 通知仍可能殘留。沒有宣稱全專案或所有 scanners 完整通過 |

`before-hashes.json`、`before/`、`source-delta.diff`、`final-hashes.json`、`log-hashes.json` 綁定本片 dirty-tree 基線與最終來源，不能用整個 `git diff` 歸屬本片修改。

### 驗證中發現並處理的問題

- 縮減 coverage fixture 後，一個分散 citation 測試仍直接索引必填 `view_narratives`，造成 KeyError。改成該情境明確提供 optional view 敘事，保留同一比較證據分散位置的 assertion；完整 coverage suite 重跑通過。原始錯誤保存於 `coverage-fixture-mismatch.log`。
- 最後既有 plugin checker 的 in-place build 逾時，且刪除了本地 generated `dist`。**逾時原因未確定，不能當成 PASS。**已核對無殘留 build 進程、runtime Grafana 只掛獨立 volume／ini，沒有掛 source dist。用先前 source-matched 隔離 build 恢復本地 generated dist，沒有碰 installed plugin。
- 將 checker 改為 TemporaryDirectory build、重用已安裝 dependencies，缺依賴時報錯而非自動安裝；新隔離執行完整通過。`local-dist-before.sha256` 再驗原 source dist 未被 checker 修改。原逾時輸出保留 `panel-static-timeout.log`。這是檢查執行環境修正，不是產品問題已被 timeout 證明修好。

## 重現主要新回歸

```bash
./.venv/bin/python scripts/check-ml-report-synthesis.py
./.venv/bin/python scripts/check-ml-dashboard-contract.py
./.venv/bin/python scripts/check-analysis-coverage.py
export REPORT_SYNTHESIS_FIXTURE_OUT="$PWD/.scratch/report-slimming-s2a/render-fixture.json"
./.venv/bin/python scripts/check-artifact-bridge-report-synthesis.py
node scripts/check-ml-plotly-readability.cjs
./.venv/bin/python scripts/check-ml-plotly-panel-plugin.py
```

最後一項需要已安裝的本地 panel dependencies，不會安裝套件或寫入 source dist。不要執行 installer 來代替檢查；installer 會安裝並重啟服務。

## 未完成與下一步

- **S2b**：prepare／inspection／compose 的機械呼叫收斂仍待設計，不能省掉 inspection 收據或偷改身份／來源政策。
- **S3**：合法科學標籤與安全 renderer／sanitizer；本片未放寬 `<`／`>`、HTML、外連或任意視覺格式。
- **S4**：core／prompt 重複邏輯收斂；新增相容 patch 不是 token／速度改善證據。
- **S5**：另行取得部署／新 session 執行授權後，才驗真模型選法、白話報告、數字／圖文一致、必要提問、browser 與真實輪次／成本。母 TODO 與原 Goal 不得由 source 綠燈升級完成。
- 新 minimal payload 需要新版 Bridge／shared contracts／compositor／plugin 成套配送；不要假設舊部署或任意混版已相容。
