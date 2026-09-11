# Plotly 原生完整圖交付 — source 驗收

2026-09-11；TODO-b102106f。主代理實作與自查，非獨立審查。

## 結論與完成範圍

本張 **source-only TODO 完成**。原 U1 四張保留圖已用新契約及真正編譯的 panel 在 Chromium 顯示；沒有重查 CSV、重跑原 Python、修改歷史 receipts 或部署。

- 新 report manifest / figure format 為 v2：使用原生 Plotly schema，不再維護 trace/layout/nested-key 功能白名單。
- NaN 僅在 trace 座標轉為 null，保留位置與缺口；其他非有限值不全域吞掉。
- 完整 figure 呈現，保留誤差棒、annotations、subplot domain/anchor；v2 不走舊拆圖器。
- 單圖失敗留下原 payload 的綁定及 bounded error，其他 facts、圖表與敘事仍可讀。compose 與 Go host 明示 partial，writer 成功不會升格為完整分析。
- 保留來源、owner/session、inspection、citation、exact-call approval、靜態離線呈現及既有資源限制。移除與整體 dashboard 限制重疊的 Plotly 圖數配額。
- `_plotly_legacy.py` 僅供 v1 及舊 execution binding 讀取，bytes 與本輪前舊契約完全相同；不重寫歷史 manifest/hash。

## 直接驗證

證據目錄：`.scratch/plotly-delivery-first/`。

| 必要項目 | 結果與證據 |
| --- | --- |
| 真 producer／四張保留原圖 | `check-plotly-native-delivery.close.log`：缺值、error bars、subplot、polar、typed array、原圖及安全負例通過 |
| 真 Store／public RPC | `check-plotly-native-report.close.log`：inspect → compose → resolve，局部錯誤、facts 保留、來源竄改拒絕、跨身份拒絕、v1 readback 通過 |
| Recovery／coverage／舊 binding／cursor | 四份對應 `*.close.log` 通過；未放寬 indeterminate、來源、比較證據與授權判定 |
| 真瀏覽器 | `browser-close.log`、`browser.json`、`browser-{1440,768}-panel-*.png`：十張圖可畫；含 U1 四圖、polar 及惰性文字，另有一個失敗位置仍顯示敘事／facts；無外連、執行注入、資料變動或水平溢出 |
| Go 與可重建 patch | `rebuild.log`、`source-rebuild.json`、`rebuilt-tests.log`、`rebuilt-build.log`：30 patches／216 sources 完全重建；Go tests（明確排除 Redis）及 offline build 通過 |
| 最終 source | `final-source.json`：本輪檔案、既有 runtime bundle 與歷史 U1 receipts 的 hash 核對 |

瀏覽器使用真 React、編譯後 panel 與 Plotly；Grafana shell/theme 是測試替身。這**不是 live Grafana／LLM／完整 Ask O11y 分析驗收**。原分析的 rejected receipt 沒有因此變為 accepted。

## 配送一致性

- 既有 Python Plotly **6.9.0** 隨附 JS **3.7.0**。panel webpack 直接採用該 JS，避免舊 npm JS 3.1.0 與 native schema 不一致。
- 沒有安裝新套件；將既有 Plotly 納入 host `pyproject.toml`／`uv.lock`，offline lock check 通過。
- build 產生 `plotly-runtime.json`，記錄 JS 版本及 source/panel SHA。兩份 Dockerfile 已包含新契約與 legacy reader 的 COPY。
- 新 Go patch：`patches/ask-o11y-native-plotly-delivery.patch`，已接 installer。
- 三份 panel source 在前次 build 後只有格式差異；已逐份確認 SWC 編譯結果完全相同，見 `panel-format-equivalence.json`。保留目前格式，沒有為 byte-match 回退檔案。

## 診斷與限制

- 最終 13 個指定檔案 primary LSP 全部 clean。`lens_diagnostics(mode=all)` 的 7 個 errors 均在未改的 `wferp/_Source/1_mssql_to_json.py`；不是全 repo clean。
- 最初隔離 typecheck 曾逾時；後續 resolution probe、實際 traced typecheck 與 webpack 成功。逾時不算通過，也不宣稱已定位最初停滯原因。
- 沒有獨立 reviewer（遵守 main-only）、Redis integration、部署或模型呼叫。一般既有傳輸／dashboard 大小限制仍適用；不宣稱所有未測圖型都已驗收。

## 下一個產品交付步驟

母 TODO-01ba5263 保持未完成。取得成套部署授權後，更新 Python MCP/shared modules、Sandbox image、Go host 與 panel，核對真工具目錄及 runtime 版本，再以正式 Ask O11y 完成原報告／Preview。

`build-install-ask-o11y.sh` 仍只安裝 app/panel，已明示不更新 Python MCP 或 Sandbox；不能再把它單獨成功當成整套配送完成。舊 operation 不盲目重送；任何需要新 Python 的修復仍需新的 exact-call approval。
