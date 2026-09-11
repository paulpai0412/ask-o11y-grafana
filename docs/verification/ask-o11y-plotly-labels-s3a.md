# S3a：科學圖表標籤（source-only）

子 TODO-23fa655d，母 TODO-01ba5263。主代理實作／自查；沒有部署或獨立審查。

## 最小修正

`ml_plotly_contract.py` 與 Sandbox 配送副本改以 stdlib `HTMLParser` 辨識實際 markup，不再因所有 `<`／`>` 拒絕普通文字。`p < 0.05`、`Temperature > 100`、`a < b` 可用；實際 HTML tag、comment、declaration、原有 URL／javascript scheme 與全部 key／資料量限制仍拒絕。Malformed declaration 轉為既有受控 ValueError。

不 escape 或 unescape 儲存資料，避免把 `a<b` 與 `a&lt;b` 合併成同一類別，也維持重複 sanitize 的 idempotency。安全文字呈現沿用 installed Plotly 3.1.0 的 native text-node sink；不新增 renderer、dependency 或 UI code。HTML entities 沿用原有 literal-text 行為，未趁此更改其他報告文字政策。Capability 由共用 sanitizer 自動揭露。

## 驗證

證據目錄 `.scratch/report-labels-s3a/`：

- `red.log`：舊 sanitizer 確實拒絕科學比較標籤。
- `labels.log`：原字串／category／caller input 不變、idempotency、HTML／scheme／長度負例、兩份配送 byte-identical；真暫存 ArtifactStore 的 manifest → 公開 inspect/compose RPC → resolve 均通過。
- `dom.log`：實際 installed Plotly 執行於 jsdom，SVG 文字正確、raw/entity 類別仍分開、encoded `<b>` 為文字而非 markup、無新增 link/script/image/foreignObject 或外網請求。Canvas/SVG geometry 為 stub，**不是 browser/pixel/互動驗收**。
- `contract.log`：既有完整 Plotly contract suite（型別／keys／精度／點數／bytes／圖數限制）通過。
- `cursor.log`、`coverage.log`、`recovery.log`：既有 cursor、科學 coverage、Plotly recovery 回歸通過。
- `go-capability.log`：僅跑 `TestPlotlyCapabilityAndErrorReachLLM`，確認目前 capability／錯誤進真正 Go Loop 的 LLM request；MCP／LLM 為 mock，非 live model。

```bash
export PLOTLY_LABEL_FIXTURE_OUT="$PWD/.scratch/report-labels-s3a/figure.json"
./.venv/bin/python scripts/check-plotly-labels.py
node scripts/check-plotly-label-dom.cjs
./.venv/bin/python scripts/check-ml-plotly-contract.py
```

未放寬其他層：encoded script literal 在 Bridge 的既有 `script` guard 仍被拒（`encoded-script-downstream.log`）；沒有刪除該 guard 讓測試過關。其他 report 敘事的角括號限制與常用樣式仍待後續處理，所以 **S3 整體未完成**。

只改兩個 sanitizer copies，新增兩個 scoped checks；沒有 Go／前端 production 變更，因此不重跑完整 Go patch stack、webpack 或全系統。沒有新增權限／依賴、重跑使用者 CSV、修改歷史 receipts、重試舊 operation、Goal 操作、child、commit/push。S4、S5 與真新手報告品質驗收仍待完成。
