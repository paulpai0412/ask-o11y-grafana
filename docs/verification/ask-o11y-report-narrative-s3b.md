# S3b：報告敘事中的比較符號

Source-only；主代理實作／自查，非獨立審查。TODO-d76569de，母 TODO-01ba5263 仍進行中。

## 變更

- `ml_plotly_contract.contains_markup` 抽出 S3a 原有判別；Root／Sandbox 副本一致，不新增 parser 或 dependency。
- `ml_report_contract` 的 report source／synthesis 與 `ml_dashboard_contract` 共用判別，取消全面拒絕角括號。
- 各自長度、ID、URL／script、數字引用規則不變。長敘事不受較短的 Plotly label bound 誤限；數字仍由 retained fact display 提供，不能直接寫入 synthesis 敘事。
- Bridge 先載入 Plotly contract 再載入 dashboard contract。這些 shared contracts 與 Sandbox 副本須成套配送，不能假設舊 runtime／混版可用。
- 不修改 compositor 或前端 production：沿用 `html.escape`／React 文字節點。儲存字串不 escape/unescape，entity literal 不重解碼。

## 檢查與證據

證據目錄：`.scratch/report-narrative-s3b/`，包含片前快照、RED、logs、final hashes、source delta、`acceptance.json`。

- `red.log`：真 report-source normalization 原先拒絕合法比較文字。
- `rpc.log`：`scripts/check-report-narrative-text.py` 使用暫存 ArtifactStore／合成資料，經公開 inspect、compose RPC 及 resolve；驗證標題／thesis／section／block／panel／view 與 fact label/display、input 不變。
- `render.log`：實際 plugin React SSR＋jsdom 解析 compositor HTML。文字和 entity literal 保留，HTML 已轉義；retained `p < 0.05` 可見，沒有 script/link/image/SVG/iframe/object 注入。Grafana hooks／Plotly pixels 仍 stub，**不是 browser 品質驗收**。
- 負例：raw tags／comment／doctype／processing instruction／malformed declaration／URL／javascript 拒絕；直接敘事數字、未知 fact、spec-only 視覺宣稱仍拒絕。report 長度限制保留，rendered HTML 中 image／active tags 仍拒絕。
- 相關回歸：synthesis、dashboard contract、compositor、legacy Bridge、cursor、analysis coverage、Plotly contract、S3a labels、dashboard write gate 全部通過。
- 七個程式檔 primary LSP 無錯。全 session cached lens 另有 `wferp/_Source/1_mssql_to_json.py` 七個 blocking errors 與六個既有文件／shell warnings；該 ETL 不在本片範圍，未修，不宣稱全 repo clean。新 Markdown analyzer 初次 unavailable，後續兩份文件 primary LSP 無錯；不推定所有 scanner 都完成。
- Scanner 對 `except REPORT_MANIFEST_ERRORS` 的 Boolean 誤報另以 AST／四 exception-class catch probe 核對（`exception-tuple.json`），保留原 handler。精確 `is not False` 亦保留；不是以 equality 放寬 lineage guard。

重現本片新增路徑：

```bash
export REPORT_SYNTHESIS_FIXTURE_OUT="$PWD/.scratch/report-narrative-s3b/render.json"
export REPORT_TEXT_HTML_OUT="$PWD/.scratch/report-narrative-s3b/text.html"
./.venv/bin/python scripts/check-report-narrative-text.py
node scripts/check-ml-plotly-readability.cjs
```

## Gate 邊界

本片 Python-only production；沒有修改 Go／正式 patch stack／plugin production，因此不重跑全 Go 重建或 webpack。驗證 Python 真 consumer、配送副本與相關安全／科學證據 gates；舊 stack 結果不冒稱本輪重建。

無部署、live 模型／OpenSandbox／browser、使用者 CSV 重跑、舊 operation／receipt／Goal 變更、children、commit/push。Synthetic fact／機械 coverage 不證明科學有效性或使用者已得到好報告。其他樣式、S4、S5 未完成；母項需產品驗收才能關閉。
