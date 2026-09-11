# Y5：計畫到 Python 的上游斷鏈調查

本次只讀歷史產物、Git/source及限定時段Grafana logs；另以兩筆合成字串及Go overlay執行無副作用的診斷。未執行歷史generated Python、未呼叫LLM、未重跑使用者分析、未修改production source、未部署或派child。

## 1. 已直接重現：資料概況將分類字串誤判為數值

`sandbox-analysis-mcp/data_profile.py::profile_columns`：

```python
numbers = [_number(value) for value in present]
is_numeric = bool(present) and len(numbers) == len(present) and not temporal_declared
```

`numbers`與`present`長度必然相同；轉換失敗的None並未排除。因此非空的非日期字串也被標成numeric，接著_numeric_summary過濾None，得到count0、所有統計null、categorical=null。

歷史Y5 session messages[5]的真profile回傳，四個煤源欄位全是：semantic_kind=measurement、missing_rate=0、numeric.count=0、categorical=null。沒有印尼／澳洲等類別資訊。這不是推測LLM會看到什麼，而是已保存的工具回傳事實。

以真profile_columns及合成 `{'source':['Country-A','Country-B'],'reading':[1,2]}` 重現相同誤判；沒有載入live artifacts或執行使用者數據。Git `log -S 'is_numeric = bool(present)'`定位到88ded75；不是本輪Go delivery-state才引入。

因果界線：這證明供給LLM的資料概況錯誤，直接破壞其國家欄位理解依據；不能據此宣稱它必然只能生成錯誤程式。Python仍可自行檢查df，而兩次歷史程式沒有完成原計畫承諾的語義確認。

## 2. 已直接重現：跨回合恢復沒有前次失敗程式與工具錯誤

`pkg/plugin/plugin.go::handleAgentRun`只還原session message的role/content，再附compactPriorToolState。該helper：

- 跳過有error的tool call；
- 不帶tool arguments（因此不帶前次python_code）；
- 成功result僅保留選定refs／output_summary等欄位，剝除instructions、一般metadata、provenance。

歷史messages[9]/[11]的Python call有arguments與error，沒有response。真helper對兩者均返回空字串。第二次生成時，按此接線，上一版code及原始結構化工具錯誤不會從這條history路徑送給模型；留下的是assistant那段「沒有有效數值序列」等自然語言摘要。它不是在同一份程式上取得準確錯誤再做局部修正，而是在有資訊損失的下一回合重新生成。

此外同一helper对messages[5]的成功工具資料做硬切：真Go輸出只剩16384 bytes且不是有效JSON。原計畫並不能由這個截斷的成功狀態取代。

可重跑證據：`.scratch/y5-llm-input-diagnosis/input_contract_test.go`、`overlay.json`、`host-context-probe.log`。命令從release-build執行：`GOPROXY=off GOTOOLCHAIN=local ../go/bin/go test -overlay /home/timmypai/apps/grafana/.scratch/y5-llm-input-diagnosis/overlay.json ./pkg/plugin -run '^TestY5ReadOnlyInputDiagnosis$' -count=1 -v`。exit0表示確認上述故障行為，並非產品驗收通過。helper出自既有`ask-o11y-dynamic-tools-and-timeout.patch`。

## 3. 歷史runtime確認：模型檔位按授權短句重選

限定讀取2026-09-09T03:15–03:36Z的Grafana logs，只投影run/model/messageCount/toolCount等非敏感欄位：

| 時間UTC／run | 當前訊息 | model／source | messageCount | selectedTools |
| --- | --- | --- | --- | --- |
| 03:29 f1Tbti… | 確認執行並產出dashboard preview | large／auto | 9 | 85 |
| 03:30 FbJRr0… | 授權執行 Python 視覺分析 | base／auto | 11 | 85 |
| 03:32 tmSPd9… | 同意 | base／auto | 13 | 85 |
| 03:34 rj2sq… | 同意 | base／auto | 15 | 85 |

`selectAgentModelForTask`查看conversationType及當前message，並不看已確認整體任務；含dashboard選large，短中文授權選base。auto選出的值沒有像explicit session model一樣固定。真Go helper已重現此分流，且8395ae1上游已有該selector。

這是模型路由不連續的直接證據，不是base必然寫錯的因果實驗；也尚未核對base／large背後實際模型映射。主代理thinking設定與Ask O11y runtime模型檔位是兩回事。

85工具亦由歷史log確認，窄選修補尚不能視為當時已安裝；工具過載是風險，不足單独證明某行錯誤的原因。

## 4. 資料交付路徑不是「輸出dataframe後由Dashboard挑圖」

Git2273c97的execute_python_analysis schema已明示：df是輸入；Plotly為預設，Python直接emit figures as *.json；derived datasets/model-input chaining disabled；DataFrame輸出以CSV下載。歷史A/B程式也都是emit Plotly圖與摘要，沒有獨立的分析結果dataframe→Dashboard製圖階段。

因此同一次LLM code generation同時承担資料理解、方法／分組、統計、圖形製作及呈現相容性。Plotly正常producer輸出的typed-array／Express hovertemplate又被當時validator拒絕，是介面不相容，不應全歸因LLM不會Python。當時實際tools/list完整快照尚未找到；不能用今天schema冒充當時模型完整輸入。

## 5. 第一次可見偏離與不能下的結論

- 第一版code已把每日共享熱耗率展開為煤源槽位觀測，未完成原計畫承諾的混煤／比較方式確認；這发生在第二次retry之前。
- 第二版code將國家值改成A/B/C/D欄位身份，四組熱耗率完全相同；是重新生成造成的分析語義改變，不是格式修正。
- profile本身提供錯誤分類資訊；接續history丟失前次code／error；模型路由按當前短句切換。這些均有實際source／receipt／probe支撑，應先處理這些上游缺陷，再考慮完成狀態。
- 沒有兩次完整ChatCompletion request body與當時installed schema fingerprint。因此尚不能判定原計畫文字是否被token裁剪、哪段prompt最終衝突，或哪個底層模型造成錯誤。
- messageCount11/13不支持直接宣稱「原計畫一定被recentCount裁掉」；目前defaultRecentMessageCount=15，仍需當時設定與request body才能判定。撤回把目標漂移／計畫遺失視為已證實唯一根因的說法。

## 修正優先序（僅調查結論，未實施）

先修有確定反例的profile型別判斷；再讓同一任務的模型選擇與code/error/ref接續一致、有界且不截斷JSON；確認實際工具schema與Plotly producer契約吻合。不要先新增Go語義完成判定器，也不要藉機改成全新的dataframe製圖架構。之後才以明確計畫→實際Python→結果逐項核對，評估剩下的模型遵循問題。
