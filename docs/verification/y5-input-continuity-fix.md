# Y5 上游輸入修正（主代理）

本輪修的是 production 程式，不是把診斷測試改成成功；原故障診斷與log保留。沒有部署、重啟、呼叫真LLM或重跑使用者分析，也沒有派child。Goal仍維持原暫停／未完成狀態。

## 實際改動

- `sandbox-analysis-mcp/data_profile.py`：numeric及temporal inference均檢查每個轉換結果非None，不再以等長陣列判定成功。分類文字不會消失成numeric.count=0，混合欄位不靜默丟棄無法轉換值。
- `pkg/plugin/plugin.go::compactPriorToolState`：保留失敗call的完整arguments/error並標ok=false；明確錯誤的writer也不當成成功。16KB上限改為完整JSON紀錄及明示省略，不截斷code/ref/JSON。過大欄位先省略output_summary、再arguments/error；紀錄總量超限保留較新的完整紀錄，揭露省略數。既有raw frame不加入whitelist。保留內容標為不可信資料，不是新指令、批准或自動重試授權。
- 同檔`handleAgentRun`：auto選模沿用既有selector評估會話內user訊息，不因短授權／重試句把已出現的large需求降回base。沒有新領域關鍵字、DAG、provider或模型設定。明選request/session model仍優先。這是保守的會話級延續，不推斷何時變成另一個新任務；新session重新判斷。
- `patches/ask-o11y-input-continuity.patch`承載Go production及回歸測試；`scripts/build-install-ask-o11y.sh`已追加套用。本輪未執行installer。

## 檢查與直接證據

目錄 `.scratch/y5-input-fix/`，原始碼改前副本在`before/`及`baseline.json`。

| 命令／檢查 | exit | log |
| --- | --- | --- |
| `.venv/bin/python -B scripts/check-profile-field-types.py .scratch/y5-input-fix/before/sandbox-analysis-mcp/data_profile.py` | 1（正確抓到舊碼缺陷） | profile-before.log |
| 同一check對目前正式data_profile.py | 0 | profile-final.log |
| 新Go回歸測試＋before-overlay.json還原舊plugin.go | 1（失敗資訊消失、JSON無效、降成base） | go-before.log |
| 真handler＋fake LLM檢查輸出request內的模型、前次code/error | 0 | go-after.log／go-final.log |
| release-build：`GOPROXY=off GOTOOLCHAIN=local ../go/bin/go test ./pkg/agent ./pkg/plugin ./pkg/mcp -count=1` | 0 | go-final.log |
| `.venv/bin/python -B scripts/check-y5-minimal-repair.py`（內部臨時artifact root及fake executor） | 0 | generic-regression.log |
| `.venv/bin/python -B .scratch/y5-input-fix/check-patch.py` | 0 | patch-rebuild-final.log |
| 保留Y5 session讀取＋真helper歷史overlay（不執行code） | 0 | historical-after.log |
| 既有`check-ask-o11y-patch-stack.py`完整版本一致性 | 1，見下方既有阻塞 | full-stack-check.log |

重建檢查從本地8395ae1 clone套用全部22個正式patch，三個本輪Go檔案逐byte等於測試source；再跑重建後plugin全套tests及`go build ./pkg/...`，均成功。未安裝／啟動這個build。

歷史overlay確認messages9/11的完整原arguments/error不被改寫、仍為失敗；messages5現在是有效有界JSON。這是context傳遞修正的證據，不是歷史分析已修復／重算。

新回歸涵蓋六類profile欄位、完整失敗參數、Unicode超量資料、總紀錄上限、失敗writer、短授權的真model request、明選base不被auto覆蓋。所有資料運算測試使用合成資料，不讀取原CSV。

五個受影響Python/Go檔案LSP無error；installer `bash -n`、兩個repo `git diff --check`成功；lens mode=all無error（session cache範圍，不宣稱全project掃描）。

## 尚未完成，不可混稱通過

- **先前delivery-state主代理scratch改動仍未同步正式patch**：完整版本checker指出`pkg/agent/loop.go`不同。本輪三個Go檔案已精確重建，但整個Y5候選版本尚不能宣告source一致或可部署。沒有藉改checker預期掩蓋此差異。
- 沒有本輪獨立review（遵守main-only）、installed runtime fingerprint、browser/live readback或新分析驗收。
- 無歷史完整model request，模型檔位修正不等於已證明模型會正確完成所有分析要求。兩筆缺provenance的歷史產物仍不可升格修復或發布。
