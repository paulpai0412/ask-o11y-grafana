# Plotly capability / recovery source review

2026-09-10；主代理自行複查，不是獨立審查。範圍為 TODO-4e930d36 本輪修改的目前 source；不是整個 dirty working tree 的歷史差異。

## 結論

方向合理，但目前實作不予驗收／部署。前次「初始實作、部分測試通過」不等於完整能力契約或 recovery 正確。

## Findings

1. **P1 — 既有 indeterminate repair 被 preflight 分支遮蔽。** `sandbox-analysis-mcp/server.py:1808-1822` 在計算 operation identity／進入 run_once（既有 reconciliation 入口）前先驗 candidate，失敗就要求重新 execute corrected code。若同一 source 已有 indeterminate repair，回覆不含該 operation ID，也沒有先 reconcile 的要求。隔離重現：真 ArtifactStore.run_once 建立 identity 後中斷；reconcile 回覆 indeterminate；再呼叫 repair，卻得到 correction_required=true 與重算指示。沒有實際重送，但回覆違反「未知操作先 reconcile」契約。應先唯讀檢查既有 operation 並保留其狀態；對尚無 operation 的 candidate 才 preflight，通過後才能 reserve。
2. **P1 — 設定 prompt 仍矛盾。** `scripts/configure-ask-o11y-workflow-tools.py:60` 仍要求 generic Python computation succeeded + report rejected 就呼叫 repair_generic_report once；`:71` 卻要求 contract error 不呼叫 repair。這正是本輪要消除的誤導。應刪除舊廣泛規則，按驗證拒絕／持久化失敗／未知 outcome 使用一致指示，不靠後段覆蓋前段。
3. **P2 — final error 的生命週期不正確。** `.scratch/ask-o11y-release-build/pkg/agent/delivery_state.go:41` 在每個 recognized tool observation 都清空 lastToolError。失敗報告後再取得相同 plan 的成功回覆（並非新資料或成功修復）就會消失。隔離 Go probe 已重現。應在相關錯誤確實被新交付／新資料取代時清除，而不是任意成功工具。
4. **P2 — final error 不是安全摘要。** 同檔 `:42-46` 將整段 tool content 複製到 host warning，包含非 error 欄位；1024 bytes 截斷也會切壞 UTF-8。合成 sentinel 欄位確實被加入 warning，中文字串確實被切壞。這不是證明目前已洩漏真秘密，但實作沒有達成設計中的「只保留安全可顯示錯誤」。應選取結構化 error／必要 status 和 opaque refs，按既有安全文字策略限制輸出，不能直接展示整包 JSON。
5. **P2 — capability summary 仍不完整。** `ml_plotly_contract.py:81-103` 與 sandbox copy 只輸出 trace 類型、layout／axis keys、forbidden keys 和部分 limits；缺 ALLOWED_TRACE_KEYS、styling 子欄位及其值限制。只有兩份手工維護的 function，不是完整單一來源契約；測試也未保證所有 metadata 與 sanitizer surface 一致。tickangle 的有界支援本身合理，但不能據此宣稱完整能力已揭露。
6. **交付阻塞 — 正式 patch 尚未封裝。** 本次重新執行 `scripts/check-ask-o11y-patch-stack.py` exit 1：reconstructed pkg/plugin/prompt_defaults.go differs from tested source。scratch Go source 的通過不代表 installer 可重建本次修改。

## 實際驗證

- `.scratch/plotly-repair-review/delivery_review_test.go`：複製目前 delivery_state.go 到 temp 目錄，以 go test 執行；沒有修改 production source。`go-probes.log` 的 PASS 表示上述故障被成功重現，不是產品驗收通過。
- `.scratch/plotly-repair-review/recovery_probe.py`：在 import server **前**設定 temporary ANALYSIS_ARTIFACT_ROOT，使用合成 frame、plan、fake executor，執行真 source verification、run_once、reconcile 和 repair。`recovery-probe.log` 保存結果。
- `.scratch/plotly-repair-review/patch-stack.log`：本次正式重建檢查失敗。
- 前次 `Sandbox self-check`／repair tests 使用 fake executor seam，不是 live OpenSandbox／自然語言 LLM E2E；不可稱部署後已驗證。既有 synthetic self-check 可支持基本分流，但未覆蓋本次重現的 legacy indeterminate 和 error lifecycle。
- 前次為 lint 把測試 `trusted_ml_contract is False` 改成 `not trusted_ml_contract` 會接受 None/0 等值，屬不必要弱化；後續應恢復精確布林斷言，將誤報正確標記，不以改測試語意消除診斷。

## 不變的正確方向

- sanitizer 保持權威，未知 key fail closed。
- tickangle 僅允許 auto 或有界數值，而不是允許任意 axis 屬性。
- 無既有未知 operation 時，先 preflight 再 reserve，避免 deterministic failure 製造假的 indeterminate。
- generic repair 不應改原 computation receipt 或 renderables；修正 code 沿用授權 frame，仍經既有授權／approval。

本輪只複查並新增隔離 probe／報告；未修 production、部署、使用真資料重算、重試原 operation、修改 Goal 或 commit/push。
