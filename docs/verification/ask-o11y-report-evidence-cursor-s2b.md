# S2b：單一 inspection ref — source-only 驗收

2026-09-10。子 TODO-9094da8a；母 TODO-01ba5263 仍 in_progress。主代理實作／複查，沒有獨立 review。這不是原分析 Goal、產品報告品質或部署驗收。

## 交付

- `inspect_report_artifacts(report_manifest_ref)` 內部準備 fresh context，同次交付 bounded facts/catalog 與最多八個 artifacts 的實際證據；以最新 `inspection_ref` 續讀。
- `compose_ml_dashboard(inspection_ref, synthesis, uid, title)` 解析最多八份已存在的 receipts，重新驗身份、context digest、完整 view coverage、比較／uncertainty facts 與敘事。沒有自動補看、背景計算、storage「最近成功」搜尋或新 planner。
- Legacy prepare/context/artifact_ids/inspection_refs 仍有效。新舊參數互斥；歷史三欄 receipt 格式僅用舊顯式介面，沒有替舊證據補造 digest。新 prepare 不覆寫先前 context；原 execution、provenance、receipt 不遷移。
- 缺 inspection 要續讀，不要求改敘事；損壞／未授權 receipt/context 停止，不盲重試。少引用既有科學 facts 仍是 synthesis 修復；原 exact approval、full-data、derived-input、Preview／publication 政策保留。
- Go 在 event／approval 前，僅將省略的 inspection mode 綁為 `spec`。原 `validateInspectionTransport` 與 `requiresExactAnalysisApproval` 函式未改；顯式 vision/null/空值/非法型別仍拒絕，不在核准後改參數。
- Delivery 綁實際 manifest/context/inspection refs、目前 plan、已選工具。只保留 cursor 不升級 `not_assessed`、不證明理解，也不是 Grafana write。壓縮後失去 facts，須重讀 retained manifest，不重跑 query/analysis。

## 可重跑證據

所有資料為暫存 ArtifactStore 的合成 fixture，不使用舊 CSV/session/operation。目錄：`.scratch/report-slimming-s2b/`。

| 檢查 | 證據／邊界 |
| --- | --- |
| 公開 RPC／store cursor | `scripts/check-report-evidence-cursor.py`：九 artifacts 分 8+1、spec/vision MCP blocks、store 重建後續讀、單批／多批 composition、resolve 的 `42.0%`、fresh context、caller／receipt 不變、混用／跨報告／跨 actor／缺失／重複／超限／損壞／context digest 負例；舊三欄 receipt 仍可顯式 compose，但不能冒充新 cursor |
| 科學 coverage | `scripts/check-analysis-coverage.py`：新入口和 legacy 同樣保留比較與 uncertainty citations；缺 facts 不得以 input-row count 或敘事取代 |
| 真 Go request | `TestReportCursorSchemaReachesLLM` 使用 Bridge 產生的公開 schemas／實際 response，經真正 Loop、Proxy、HTTP、LLMClient；假 MCP／LLM 驗 schema 完整、預設參數在事件前綁定、完整第一批 evidence 進下一個真 request。不是 live model |
| Delivery／跨 turn | `TestReportCursorDeliveryBinding`／`TestReportCursorOpaqueState` 驗 manifest 起始／續讀／單 ref compose／resume、ref/plan/tool 負例，以及既有有界 history JSON 保留 refs；不聲稱保留全部 facts 或模型理解 |
| 其他回歸 | legacy Bridge、synthesis、dashboard contract/compositor/write gate、analysis coverage、Y5 repair、Plotly recovery/Bridge、approval、no-fixed-flow、local-defaults settings self-check、shell syntax |
| 正式配送 | `patches/ask-o11y-report-evidence-cursor.patch` 接 installer；28 patches／215 Go、TS、TSX、Markdown sources byte-identical。從重建來源跑 `go test ./... -skip Redis -count=1` 與 CGO0 build，`GOPROXY=off`、`GOTOOLCHAIN=local`；兩個 wire-fixture env 均有設定，不以 skip 冒充測試 |
| 前端旁帶核對 | 發現 current panel 與 S2a isolated source 有三處純排版差異；保留 current file，不歸因或回退。兩者 SWC emitted code 相同，並重新跑現態 `tsc --noEmit --incremental false`、SSR／Bridge fixture；不是 source byte-identical、webpack build 或 browser 驗收。見 `panel-format.diff`／`panel-format-equivalence.json` |
| 診斷 | 十一個主要 source/test 檔 primary LSP confirmed clean；額外 panel LSP silent-on-clean 為 inconclusive，另以 tsc 通過驗證。最後 cached lens 73 files 僅六項舊 document/shell warnings；新文件 link 重查已清、TODO ID 拼字誤報已標記。不是完整 project scanner/security audit；opengrep silent／歷次 timeout 不算 PASS |

```bash
export REPORT_CURSOR_FIXTURE_OUT="$PWD/.scratch/report-slimming-s2b/cursor-fixture.json"
./.venv/bin/python scripts/check-report-evidence-cursor.py
export PLOTLY_TOOL_FIXTURE_OUT="$PWD/.scratch/report-slimming-s2b/tool-fixture.json"
./.venv/bin/python scripts/check-plotly-recovery.py
./.venv/bin/python scripts/check-analysis-coverage.py
python3 scripts/check-ask-o11y-patch-stack.py
python3 .scratch/report-slimming-s2b/rebuild_check.py
```

`rebuild_check.py` 只做本地 source 重建／測試／build；沒有執行 installer、安裝依賴、套 live settings 或重啟服務。Source fingerprints、guard 比對、delta、logs hashes 見 `final-checks.json`、`final-hashes.json`、`source-delta.diff`、`log-hashes.json`、`acceptance.json`。

## RED 與調整（不改記為通過）

- 舊 Bridge 不接受 manifest 起始 inspect，舊 Go 不辨識 inspection delivery：`cursor-red.log`、`go-red.log`。最初 fixture 共用 PNG 名稱被既有 normalizer 拒絕，修成每 artifact 唯一名稱，沒有放寬 validator。
- Resume composition 起初缺 manifest identity，補 Bridge 實際返回 manifest/context/inspection refs，再由 Go 比對，不從文字猜測：`resume-binding-red.log`。
- 新 opaque-state test 起初把整個帶 host marker 的文字當 JSON；改用既有 `decodePriorState` 驗有界 JSON records，不修改 production compactor：`opaque-state-fixture-mismatch.log`。
- Schema-only／直接 helper 測試未發現 host 拒絕省略 mode；加入真正 Loop tool call 才重現 `HOST_VISION_TRANSPORT_UNAVAILABLE`。在既有核准前綁定點修正 default，原 guard 不動：`host-mode-default-red.log`。
- 最終 checker 起初將 S2a 的 `{sha256, ...}` record 當字串比較；修正證據 consumer，保留失敗 log `final-check-schema-mismatch.log`。另發現上述 panel 格式差異，沒有把它冒稱 byte-unchanged。
- 主代理複查另修 recovery 分類：receipt identity 先驗；有效 receipt 缺 coverage 要續讀，而不是反覆改 synthesis。不是獨立審查。

## 未涵蓋／下一步

沒有部署、live settings、真 LLM／OpenSandbox／browser、舊資料重跑、舊 operation retry、歷史 artifact 修改、Goal 操作、child、commit/push。Go 跳過名稱含 `Redis` 的 tests；沒有 race／Redis 整合或獨立 security review。PNG 是合成 transport fixture，不是視覺可讀性驗收；Sandbox source byte-unchanged；本片無前端行為修改，旁帶格式差異依上表核對，沒有重做前端 build。

批次上限是 artifact 數，不是 token/byte 保證；refs 變少、流程少一個 prepare 呼叫，不等於實測成本／速度／成功率變好。S3 安全科學標籤、S4 核心／指引收斂、S5 真模型與新手報告 QA 仍未完成，整體產品目標不得關閉。
