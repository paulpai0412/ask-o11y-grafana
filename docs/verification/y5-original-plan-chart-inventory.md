# Y5 原計畫與既有圖表逐項核對

本次只讀歷史 session、程式文字、execution JSON 與既有 Dashboard readback；沒有執行保留的 Python、修改 runtime、部署、重算或寫 Grafana。以下是保留證據的核對，不是對 localhost 現況的新驗收。

## 1. 原計畫與明確核准

來源 `.scratch/y5-root-cause-recheck/y5-session.json`：

- messages[0]/[2]：製程工程師要求降低熱耗率的視覺分析報告、關鍵參數與彼此關係、不同國家煤源差異、說明及建議。
- messages[3]：分析預覽承諾下列五個交付類別；並未指定固定五張圖或強制 ML。明確說「不先建立預測模型」。
- messages[4]/[6]：「確認執行,並產出dashboard preview」。
- messages[8]：「授權執行 Python 視覺分析」。
- messages[10]/[12]：「同意」是當時兩次錯誤後續的授權紀錄，不自動授權現在重新執行。
- 原計畫明確承諾：139筆、完整資料不抽樣；煤源實際國家值查後確認；多煤源同日保留原結構；揭露缺失資料處理；相關不等於因果。

## 2. 逐項對照

A＝`run_a8c590441ce746bead965716a6587a17`；B＝`run_0cf9ba9b5d6f44f4af397422856f08d2`。檔名以下均指各 execution.results 中的 display_name。

| 原計畫交付類別 | 已保留的實際圖／結果 | 尚未完成或不相符處 | 能否只修呈現 |
| --- | --- | --- | --- |
| 1. 熱耗率時間趨勢，搭配發電量、平均熱值、原煤耗 | A `heat_rate_trend.json`：138個日期與熱耗率值。B 同名圖：熱耗率、原煤耗、平均熱值、平均發電量四條138點序列 | A缺背景序列；B把不同量綱放在同一「原始量測值」y軸，仍需可讀性驗收。139筆中一筆target缺失的圖表排除須揭露 | 圖的數值序列已存在，不是空圖；但兩筆均缺provenance，不能直接透過授權修復路徑發布 |
| 2. 熱耗率與煤質／操作／設備參數關聯 | A `key_parameter_associations.json`：10項Spearman排名，`heat_rate_analysis_results.json`有係數及配對n。B `heat_rate_associations.json`：15項排名 | 不等於已逐項交代所有原計畫參數；程式使用pairwise dropna，報告需揭露各圖／比較有效樣本與缺值處理。report-source未把這些係數登錄為可引用facts | 排名圖已有結果；來源及有效樣本處理未驗收，不能宣稱全數可安全重用 |
| 3. 關鍵參數相互關係：矩陣、散點與聯動解釋 | A `parameter_relationship_matrix.json`：熱耗率＋前8候選參數的9×9矩陣；另有熱耗率對原煤耗散點。B `heat_rate_key_relationships.json`：對原煤耗、平均熱值、真空度、飛灰未燃碳的四組散點 | B沒有A的矩陣；兩筆不能拼接冒充同一已驗收分析。尚無充分的參數間聯動解釋，原計畫提及水份／熱值／用煤量及風門／未燃碳的檢視未見完整交付 | 部分图已有；缺少的關係結果不能靠格式修正生出來，需先界定必要補算範圍 |
| 4. 不同煤源國家：頻率、覆蓋、熱耗率及煤質／操作差異 | A `coal_source_heat_rate_comparison.json`按原始煤源值分6組，共548個槽位觀測，含「印尼+澳洲」。B `coal_source_heat_rate.json`按A/B/C/D欄位分組，每組138點 | A將同日共享熱耗率展開至多槽位，不能當獨立國家樣本或國家效果。B四組y的原始解碼bytes完全相同，只是同138個熱耗率複製4次，完全不是國家比較。未見國家別煤質／操作條件比較與分層分析 | **不能只修圖表格式就算完成。** 必須先釐清國家／混煤／每日樣本的比較定義，再另行核准必要計算 |
| 5. 改善優先順序及監控／分煤源建議，不給未核准設定值 | A/B有關聯摘要與一般「非因果」限制 | 沒有依關聯強度、穩定性、可控性形成可追溯優先順序。A/B `report-source.json`都只登錄rows/valid_rows/excluded_rows，沒有關聯係數與國家比較facts；不能支撐承諾的數值解釋 | 不是多加狀態欄位能解決。需要先有合格的分析結果，再把真正結果接入報告facts與敘事 |

## 3. 實際卡住的位置

1. **最早是報告格式拒絕，不是沒有計算結果。** A有5張Plotly圖，B有4張；execution.error均為null。typed-array及Express hovertemplate與當時validator不相容。
2. **保留結果不等於可以安全修復。** 兩個run目錄都只有metadata.json、sandbox-code.json、sandbox-execution.json，沒有sandbox-provenance.json；不能補造或繞過目前repair授權檢查。
3. **部分分析本身不符合原計畫。** 最清楚的是B把煤源欄位身份當分組，四組相同熱耗率；即使圖能顯示，也不能回答國家差異。
4. **報告facts匯出沒有承接實際分析摘要。** execution內有係數與分組摘要，但generic report-source只提供列數facts與泛用purpose。格式修好仍不等於完成問題導向報告。
5. **最後真的換成profile Preview。** 已保存readback：UID `coal-profile-preview-7d1e`、title「燃煤機組資料概況」、9 panels；其中4個圖panel為缺失、分布、數值共同變動與時間概覽，沒有上述原分析圖。這不是「原圖已寫入但瀏覽器不顯示」。

## 4. 接下來的最小修正界線

- 不繼續擴充Go完成狀態框架來代替畫圖。
- 先確保一般Plotly產物能穿過正式報告與Preview鏈；這只能解決呈現層。
- 原計畫圖必須逐項有可驗證結果，報告fact catalog必須承接真正結果，不能只引用列數。
- 歷史A/B維持inspection-only。完整重建需要新的可信產物；不能因本核對自行重新執行。國家／混煤比較定義、缺值處理以及所需計算範圍需先明確，再取得當前核准。
- 安裝／部署、Grafana寫入、瀏覽器與新session分析驗收均尚未執行；本表不代表完成Preview。

## 5. 證據完整性

本次重新計算 `.scratch/y5-root-cause-recheck/receipt-index.json` 所列8個來源檔SHA-256，全部與已保存值一致。

關鍵定位：

- A sandbox-code.source L21–27（配對dropna／Spearman）、L59–64（9×9矩陣）、L66–93（槽位展開與國家值分組）。
- B sandbox-code.source L19–23（背景趨勢）、L35–48（槽位身份分組）、L51–56（四組散點）。
- A execution.results[0..4]為圖、[5]分析摘要、[7]generic report-source。
- B execution.results[0..3]為圖、[4]分析摘要、[5]generic report-source。
- `.scratch/y5-root-cause-recheck/coal-dashboard-readback.json`：panel4/7/8/9為profile圖。
