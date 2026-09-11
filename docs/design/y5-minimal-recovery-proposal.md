# Y5 簡化方案（Git 審視後的提案，尚未實施）

## 核對結果

- 主 repo HEAD 為 `2273c97`，前序 `3dad071` / `dd6d9ec`。外部 Ask O11y source base 是 `8395ae1`，不是同一個版本序列。
- `dd6d9ec`、`2273c97` 已有 report_manifest → prepare → inspect → LLM synthesis → compose → approved writer 的報告分工。原有 compose 驗證來源、inspection 與引用，不以 Go 判斷科學完成。
- HEAD build script 套14個patch；working tree又接了7個未追蹤patch：approval-default、report-dashboard-provenance、semantic-alignment、business-question-binding、writer-gates、selection-closure、delivery-state。不能把它們全部當成同一次變更或全部回退。
- HEAD沒有目前的 `_analysis_coverage` 或 generic report repair helper。Sandbox對HEAD已有大幅未提交差異，但包含不同時期修正，不能以行數判斷哪些該刪。
- 已證實新delivery-state曾因重複實作來源／plan判斷而不相容於真producer；這是新增回歸，不是原繪圖失敗的根治方式。
- 本次只執行 `git apply --reverse --check`，未改source；delivery-state patch不能反向套用到目前scratch source，證明後续main修改與正式patch尚不同步。scratch測試不能代表可重建候選。
- 現有BuildContextWindow僅保留近期messages；TrimMessagesToTokenLimit可再丟棄較早內容。已確認計畫沒有專用保留位置。這是可觀察的流失風險；尚未證明Y5某次模型request確實丟掉哪一段，不能當成已證實唯一根因。

Git source只能證明當時設計與改動，不能直接證明舊binary在使用者環境完整通過。因此不做整庫reset，也不宣稱回到某commit自然就會好。

## 建議：回到原報告分工，不另建需求驗證框架

| 處置 | 範圍與理由 |
| --- | --- |
| 從候選撤出新Go delivery-state判斷層 | 不讓Go另外猜工具能力、重新驗證artifact lineage或推斷分析完成。保存目前修改與失敗測試，不直接覆寫／刪除工作區 |
| 保留必要修正 | Plotly有界相容處理、報告失敗仍保留provenance、既有session授權／writer effect與UID安全、missing-value approval、trusted regression/baseline、工具窄選。不能以簡化移除安全與數值正確性 |
| 合併重複提示 | 問題對齊規則保留一個主要來源；不在多層prompt反覆疊加同義條文，不再添補丁式指令 |
| 既有repair只處理例外 | 有可信保留結果才修報告；正常分析不必經repair，不為通過狀態檢查强迫重算 |
| 不新增固定要求引擎 | 不做領域關鍵字、指定圖表清單、固定DAG或強制ML。不要先建全面的requirements/state平台 |

## 唯一必要的缺口：保留已確認計畫，供原有報告合成核對

1. 沿用既有session／approval資料保存的確認內容。若沒有明確引用，只補明確的已確認計畫引用，不用關鍵字猜「確認」或把最新followup當原問題。確認邊界及資料能否提供這個引用，須在實作前核對。
2. 每次恢復分析與報告合成時提供同一份有界確認內容；不要只依可被截斷的聊天歷史。這是context保留，不是第二個runtime planner。
3. 沿用原有synthesis階段，由LLM逐項對照「承諾／實際證據／缺口」。引用仍由既有Bridge檢查；不另寫語義判定器。缺項可產出明示缺口的partial，不默默換成另一項分析。
4. 最終chat沿用這份報告的交付說明，不從「writer成功」再生成一個分析完成判定。

此方案降低目標遺失及無聲換題，不保證LLM永不犯語義錯誤；仍必須測其實際行為，不能把self-check文字當正確性證明。

## 實施順序與停止條件

1. 先在隔離目錄重建2273c97所描述的patch stack，保留所有現有dirty/untracked；不執行build-install（有部署／重啟副作用）。
2. 用同一份可信fixture／fake executor比較舊分工與最小候選，真producer回傳串到既有公開入口；先normal完整報告，再report failure保留結果恢復，再合法partial。附計畫在context裁剪與followup後仍可見的檢查。
3. 第一條報告／Preview鏈通過前不追加其他功能。每個保留patch須有必要性與回歸證據；不把不相干失敗藏入「既有問題」。
4. 只把必要變更同步為可重建patch，核對candidate hashes及原始logs。source、offline artifact、installed runtime、live readback與browser驗收分列。
5. 提案未授權reset、部署、重算、Dashboard寫入或發布。依目前使用者要求由main處理，不再啟動子代理；缺獨立審查不得宣稱已通過。
