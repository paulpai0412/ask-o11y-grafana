# U1 報告被拒絕：原因與漏測範圍

2026-09-11，主代理診斷／自查，非獨立審查。只重播保留的 figure JSON 到本地 validator，沒有重跑 CSV 分析、query、Sandbox、repair、Dashboard writer，沒有修改 production source 或重啟服務。

## 1. 實際故障

Ask O11y run `A44ieZR0_M-9YIzkbkzwPHznUE4zByzGnOUZtiW4wD0` 的 Query 成功；Sandbox receipt 表示 computation succeeded、report rejected。保留的 execution 為 `run_456c5e9c58e447d0aae5e4982077d271`。

直接解析該 execution 的四個 Plotly MIME，再各自呼叫目前 `ml_plotly_contract.sanitize_figure`：

| output index / 檔名 | 重播結果 |
| --- | --- |
| 6 / heat_rate_time.json | `expected finite numeric value in trace.y` |
| 7 / main_source_box.json | accepted |
| 8 / spearman_ranked.json | `unsupported trace key 'error_x'` |
| 9 / top_association_scatter.json | `unsupported layout.annotations[] key 'xanchor'` |

因此不是只有一個壞值；只修第一張圖仍不足。這是目前 source validator 對保留產物的重播；live receipt 直接證實的第一個拒絕是 trace.y，不能宣稱已對 live validator 個別重送其他三圖。

### 第一個拒絕的逐步證據

- 原 CSV 第 140 行（第 139 筆資料），日期 `2026-07-29`，`熱耗率` 為空字串。
- 保留 Python source 第 209 行：`go.Scatter(x=d[date_col],y=d[ycol],mode='lines+markers',name='熱耗率')`，直接把整欄交給 Plotly。
- 同程式第 163–164 行雖為統計建立了 dropna 的 y/y_time，但時間圖沒有使用它們。這是統計與繪圖兩條資料處理路徑不一致，不是整份資料必須刪列。
- 保留 figure 的 `y` 是 Plotly `dtype=f8` / `bdata`。解碼後 index 138 為 IEEE NaN，同索引日期正是 2026-07-29。
- `_decode_typed_array` → `_check_array` → `_check_number`；後者 `math.isfinite` 拒絕 NaN。這不是原始 JSON 語法損壞，也不是結果憑空溢位。
- `normalize_report_manifest` 驗每張圖；第一個 exception 中止整份 manifest，不靜默丟圖，因此計算成功不等於報告通過。

### 另外兩個拒絕

- Python 第 223 行主動使用 `go.Bar(...error_x=...)` 畫信賴區間；現有 `ALLOWED_TRACE_KEYS` 沒有 `error_x`。
- 第 229 行使用 `make_subplots(...subplot_titles=sel)`；Plotly 自動生成的 annotation 含 `xanchor`，而 `ALLOWED_ANNOTATION_KEYS` 不接受。其他 annotation 限制也需一併考慮，不能只刪第一個鍵視為已修復。
- 這兩項是常見 Plotly 表達能力與本專案有限契約不相容，不等於惡意內容；validator 正在執行現有規則。

## 2. 實際 runtime 沒有用到新能力描述／recovery 行為

用已授權 Grafana 身分，唯讀 GET `/api/plugins/consensys-asko11y-app/resources/api/mcp/tools`，保存只包含 execute/revise 的公開工具描述：`diagnosis/runtime-selected-tools.json`。

實際服務仍提供舊的 trace 名稱列舉，沒有新版 `Exact host Plotly capability`、`nested_keys`、禁止 NaN/null 的規則。磁碟 `sandbox-analysis-mcp/server.py::_plotly_capability_description` 則已包含這些內容，故 public runtime 與已驗 source 不一致。沒有保留本次完整 LLM HTTP request，不能逐 byte 斷言当時的 request；但現有 public catalog 和本次舊版錯誤 receipt 是直接證據。

本次錯誤仍指示：`call repair_generic_report ... without rerunning computation`；新 source 對 deterministic contract rejection 明確要求 `correct_python_same_frame`、禁止用 generic repair 重驗原壞圖。這不是單純字句差異：修錯誤路徑的 source 沒有反映在實際回覆。

- 最新 deployment acceptance 已記錄 `deployed_code_and_panels`、`settings_applied=false`。
- `scripts/build-install-ask-o11y.sh` 只建置／安裝 Grafana app/backend 和 Plotly panel、重建 Grafana container，沒有更新／重啟 Python MCP 或建 Sandbox image。
- 觀察到 Sandbox/Bridge PID 297111/297110 啟動時間為 2026-09-10 20:46:13；兩者 cwd 為本 repo。
- execution provenance 的 image ID 為 `sha256:8b10bab6f0125cf019c7a41c09622bc1f236427866e2271e6ee5605ea8da66bc`；Docker image Created 為 2026-09-10 13:53:35 +08:00。未進入／執行 image，也未證明其每個內部 module hash。

**重要界線：**成套更新能補上缺少的能力描述和正確 recovery，不會讓這四張未改的圖自動合法；當前 source 重播仍有三張拒絕。Prompt 也不保證 LLM 永不產生違規圖。

## 3. 為何先前測試沒有阻止此故障

1. **有測 NaN「會被拒絕」，沒有測缺值圖的成功交付。** `scripts/check-ml-plotly-contract.py::numeric_validation_case` 明確 assert NaN reject；typed_heatmap_case 也測 NaN reject。測試成功表示 guard 正常，不表示 LLM 能把缺值資料畫成合法報告。
2. **成功 fixtures 太理想化。** valid_scatter_case 是三個有限數字；trace-types case 的 bar/box/scatter 是手工最小合法 JSON。沒有這次帶缺值的完整時間圖＋誤差棒＋自動 subplot 標題組合。
3. **Recovery/LLM transport 是 source/mock 測試。** `scripts/check-plotly-recovery.py` 用兩列合成資料和注入 executor，產生預先寫好的圖；`TestPlotlyCapabilityAndErrorReachLLM` 透過 httptest mock MCP/LLM，證明新描述可以送達，不證明 live MCP 已提供新描述，更不證明真模型會遵守。
4. **部署驗收止於 app/panel，而非整套分析服務。** 健康、build hash、patch byte-match 無法代替 runtime tool schema/recovery readback。本次測試前沒有把這個落差擋住，是主代理驗收缺口。
5. **S3a/S3b 的 scope 是文字安全與敘事。** SSR/jsdom/合成 RPC 測的是 escaping、比較文字、receipt；文件明示非 live LLM/OpenSandbox。S5 真模型新手報告驗收一直未完成，不能用局部 PASS 代替產品交付。

## 4. 可重跑診斷

```sh
./.venv/bin/python .scratch/u1-ask-o11y-live-20260911-103027/diagnosis/replay.py
./.venv/bin/python .scratch/u1-ask-o11y-live-20260911-103027/diagnosis/replay.py --require-accepted
```

第一個命令確認三個拒絕及最小重現，exit 0；第二個要求所有保留圖通過，實跑 exit 1（RED）。`replay.json` / `acceptance-red.json` 保存四圖結果、NaN 日期位置及全部原 execution run 檔案 SHA256，前後相等。沒有執行保留 Python 程式。

## 5. 建議的最小後續（未實作）

- 先補部署一致性檢查：從真 Grafana public tool catalog 比對能力描述；不只看服務 port 與工具名稱。
- 保留本次四圖作 consumer 回歸；另用小型含缺值資料＋真 Plotly producer 驗缺值呈現、誤差區間與 subplot 預設。安全拒絕測試保留。
- 明確處理缺值為圖上的缺口／有限片段，不能填零、刪原資料或跨缺值默默連線；用已支援且不遺失不確定性的表示方式，或經受控擴充契約後驗證。不能靠關閉 sanitizer／默默丟圖過關。
- 原 execution 不改寫、不直接呼叫舊 instruction 建議的 generic repair；後續修圖／重算／部署須按授權及 durable operation 狀態分別處理。

補正前次進度：兩個 `approval_resolved` receipt 的 comment 均為 `auto-approved by approval policy`。輪詢腳本停止不等於服務端 run 停止；前次「沒有執行 query」說法被後續 receipt 推翻。這是另一個觀察／控制缺口，不是本次 manifest rejection 的原因。
