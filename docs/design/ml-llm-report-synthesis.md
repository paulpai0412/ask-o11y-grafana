# Generic LLM Report Synthesis + Grafana-native Plotly

2026-09-11 設計更新：[不卡產出的 Plotly 修訂](ask-o11y-novice-report-simplification.md#2026-09-11-修訂以順利產出為預設不再自訂-plotly-方言) 優先於本頁相衝突的歷史規則。移除自製 Plotly 功能白名單、預設原樣呈現完整 figure；計算／來源與各圖可呈現狀態分離，單圖錯誤明示但不封鎖已有分析結果。尚未實作／部署，下方歷史驗收不代表新設計已完成。

状态：既有实现与历史 E2E 已有记录；2026-09-06 capability-driven report manifest、Plotly/image presentation 與 TDD vertical slices 已完成，localhost/browser 手測待使用者驗收。

当前规范见 [平台架构修订](natural-language-analysis-platform.md) NLAP-01、07、10。本輪待辦为 `TODO-9ae85041` 及其子项。下方历史结果不代表当前全部路径已符合新规范。

2026-09-10 S2a source 修訂：以下最小敘事介面相容舊完整 v1 payload；未部署／未做真 LLM 或 browser 品質驗收。產品目標與剩餘工作見 [新手報告瘦身設計](ask-o11y-novice-report-simplification.md)。

## Generic profile amendment

`prepare_ml_report`、`inspect_report_artifacts`、`compose_ml_dashboard` 名稱沿自 ML 歷史，但 contract 不限 ML manifest；`ask-o11y-data-profile-v1` 可直接進同一條 evidence-bound report pipeline。Profile report 的 facts 必須保留完整輸入 row/field coverage，圖表聚合只能作視覺展示。LLM 先讀完整 bounded facts、artifact catalog 與每個 view spec，再以 vision/spec 批次檢查全部 artifacts；最後輸出整份 synthesis，每個 panel 保留白話觀察、解讀、限制與 evidence；只有需要個別解釋的 view 才補敘事，不逐圖重複填寫同一模板。若 validator 退回，僅依原始錯誤與 refs 修正，不重跑已成功的 query/profile。

Profile 不會自動升級成 ML：WFERP/ERP 或 upload 若沒有明確預測意圖，報告只做描述性、診斷性或比較性敘事；ontology candidate 仍標示 `inferred`/`observed`，不得冒充 approved。

## 决议

分析计算保持 deterministic；报告的重点选择、跨图推理、每图叙事与正文／附录编排交给 Ask O11y LLM。LLM 必须一次读取整份 bounded report context，不逐图独立生成 caption；所有数值由 fact references 渲染，LLM 不得改写事实、部署状态或权限结论。

Telco 专用 builder 只可作为 E2E fixture，production contract/compositor 不得包含 dataset 名、字段名或固定 chart 清单。

## 流程

```text
Sandbox deterministic analysis
  → report-source-v1 + PNG and/or sanitized Plotly JSON
Host-owned report normalizer
  → report-manifest-v1 + opaque report_manifest_ref
Artifact Bridge.inspect_report_artifacts(report_manifest_ref)
  → 内部 fresh context + bounded facts/catalog + 实际 artifact batch + inspection_ref
Artifact Bridge.inspect_report_artifacts(inspection_ref)（仍有 pending artifacts 才续读）
  → 实际下一批 spec 或 PNG MCP image blocks + 最新 inspection_ref
Ask O11y LLM（分 bounded 批次看完完整报告与全部图）
  → ReportSynthesis JSON
Artifact Bridge.compose_ml_dashboard
  → 最新 inspection_ref + LLM synthesis → 核对 context/全部 receipts/facts/views → opaque dashboard_ref
Artifact Bridge.resolve_dashboard_refs
  → resolve dashboard_ref + asset/query bindings → Grafana writer
```

## Host-owned report manifest

报告 source 是 producer 对图表与 bounded facts 的显式声明，不是任意 summary JSON。Host 从成功 execution 的已捕获 MIME 输出建立 `report-manifest-v1`：

- 每个 artifact 以稳定 `artifact_id` 声明 fact refs 与可选 figure/PNG output name；Host 自己计算 output coordinates、MIME、digest，不把它们暴露给模型。
- 有效 sanitized Plotly figure 优先产生 `render.mode="plotly"`；只有没有 figure 时才可选择合法 PNG 的 `render.mode="image"`。
- 新設計：單圖格式／渲染失敗保留該圖及錯誤，不連帶拒絕其他同源分析結果；不得静默降级或宣稱全部完成。此規則取代舊「sanitizer 失敗即拒絕整份 manifest」；具體實作／相容要求見 2026-09-11 修訂。
- purpose/conclusion/facts 与 artifacts 有大小、字符、数量上限；禁止 raw rows、路径、URL、token、HTML 与 code carriers。
- Manifest ref 继承 execution 的 org/user/session 授权与 retention；普通 summary、未声明输出或手动 legacy index 不能获得 report 成功身份。旧 trusted profile/ML execution 如需相容，必须先经过 host-owned `reexport_trusted_report`，产生 fresh execution/provenance/report-manifest refs；generic Python 不得 re-export。

## LLM inspection transport

S2b 的共同入口是 `inspect_report_artifacts(report_manifest_ref)`；它內部完成 metadata preparation 並傳回實際證據，`prepare_ml_report` 留給舊顯式介面，不再是必經呼叫。Preparation 消费 Host-owned `report_manifest_ref`，不让模型选择 `manifest_output_index`。Host 会持久化 bounded report context，并为每张 Plotly 图提供 deterministic `figure_spec`：trace types/count、subplot `view_id`、title、X/Y label、unit/tickformat、scale/range、point count 与 min/max。它返回 opaque `report_context_ref`，不暴露 raw rows、output index 或 signed URL。旧 `execution_ref + manifest_output_index` 不再是 Bridge 输入；相容既有 trusted 产物时由 Sandbox host 执行 bounded re-export，再把 fresh `report_manifest_ref` 交给 Bridge。

`inspect_report_artifacts` 以 `report_manifest_ref` 開始、以最新 `inspection_ref` 續讀；舊 `report_context_ref, artifact_ids, mode` 仍相容。每次只能提供一種來源 ref：

- `mode=vision`：返回 PNG 为 MCP `image` content blocks，同时返回完整 sanitized Plotly JSON 与 figure/view specs。
- `mode=spec`（預設）：供不支持 vision 的模型使用，返回完整 Plotly JSON、aggregate values 与 view specs，不假装已视觉看图。
- 每次 inspection 写入 opaque `inspection_ref`；可 bounded 分批，但 compose 前必须覆盖整份 report artifacts，且選取的 views 必須有 inspection。省略逐 view 敘事不減少 inspection coverage。
- 預設每批選下一批最多八個 pending artifacts，可用 `artifact_ids` 指定較小批次；`remaining_artifact_count=0` 表示 receipt coverage 齊全，不是模型已理解或分析完成。上限按 artifact 數量計，不保證 transport token／bytes 大小。
- `compose_ml_dashboard(inspection_ref, synthesis, uid, title)` 從最新 receipt 取得 context 和先前有界 receipts，重新驗證全部 coverage；不會替 LLM 補做 inspection。舊 `report_context_ref + inspection_refs` 仍可用，不能與新參數混用。
- 每次 prepare 寫 fresh context；新 receipts 含 context digest 及最多七個 prior refs，不改舊 receipts／原始 execution/provenance。Context 改變須停止調查，不是敘事修復。無 digest 的歷史 receipt 使用舊顯式介面，不能冒充新 cursor。
- 若跨 turn 只剩 opaque refs 而無證據內容，重新 inspect retained manifest；不是重跑 query/analysis，也不能用「已寫 receipt」代替閱讀。

## Model-first 双轨解读

LLM 必须读取 deterministic facts、sanitized Plotly JSON 与 figure/view specs；只有傳輸／模型支援 vision 且實際收到圖片時，才能依 PNG 檢查分布形狀、顏色、重疊與渲染問題。数值结论只能来自 model/facts；视觉观察只能描述像素中可见的形状，不得自行产生数字。两者冲突时以 data model 为事实，并将冲突标记为图表／渲染问题。

`view_narratives` 可省略或只涵蓋需要個別解釋的 selected views，不得重複或引用未選取的 view。提供時必須有 `view_id`、`headline`、`data_observation`、`interpretation`、`limitation` 與 `evidence`；`next_step` 可省略，`visual_observation` 缺省為 `null`。Vision receipt 不強迫產生視覺形容詞；只有 spec receipt 時仍禁止非 null 的視覺觀察，不得聲稱看過圖片。

## ReportSynthesis contract

```json
{
  "format": "ask-o11y-report-synthesis-v1",
  "report_title": "string",
  "thesis": "string（敘事不得含數字，數值由 evidence 渲染）",
  "thesis_evidence": [{"fact_ref": "facts.signal", "format": "number_2"}],
  "sections": [
    {
      "section_id": "LLM 产生的稳定 id",
      "title": "string",
      "purpose": "本 section 在本次报告中要回答的问题",
      "panels": [
        {
          "artifact_id": "manifest artifact stem",
          "view_ids": ["deterministic subplot view id"],
          "headline": "跨 views 的 panel 总结",
          "observation": "观察到什么",
          "interpretation": "为什么重要",
          "limitation": "不能推论什么",
          "evidence": [{"fact_ref": "facts.signal", "format": "number_2"}]
        }
      ]
    }
  ]
}
```

上例為最小 shape，artifact/view/fact IDs 必須取自本次授權 context。可选 `cross_chart_context`、`next_step`、`view_narratives` 由 LLM 按需要補充。Section 缺省 `collapsed=false`、`narrative_blocks=[]`；panel 缺省 `view_narratives=[]`、`priority=supporting`、`preferred_width=full`。Host 只填機械預設，不生成敘事或修改原輸入／歷史收據；顯式空文字仍拒絕。舊完整 v1 payload 保持內容與呈現相容；新 candidate 需成套更新 validators/compositor/plugin，不能假設舊服務已接受最小 shape。

不规定 section 数量、role、标题、顺序或正文／附录结构。LLM 根据用户目的、artifact capabilities 与整份报告事实动态决定；validator 只限制 bounded shape、安全字符、artifact/fact evidence 与总量。

## Fact / claim guard

- Preparation（共同 inspect 入口或舊 `prepare_ml_report`）只从 server-owned `report-manifest-v1` 递归产生 bounded scalar fact catalog，不暴露 raw rows；没有 fresh manifest 的旧 execution 不得进入 synthesis。
- 每个 panel 必须引用存在的 artifact 与至少一个 `evidence {fact_ref, format}`。
- LLM narrative 字段不得包含数字；数值 evidence chips 由 compositor 根据 `fact_refs` deterministic 渲染。
- deployment / operational status 只能读取 manifest，LLM 不可覆盖。
- 未知 artifact、未知 fact、重复 section id、越界 section/panel 数、HTML/script/URL 全部 fail closed。

## Generic dashboard compositor

- 只读取 LLM 产生的 section 顺序、collapsed、priority、preferred width 与 artifact references；不识别固定 story roles、dataset、字段或图名。
- LLM 决定 section 数量、图表选择、正文／折叠与顺序；compositor 不自动重排内容，只在尺寸不足或安全越界时 fail closed／提升为 full width。
- 所有图片 evidence panel 一律建立 `asko11y-plotly-panel`：sanitized figure 使用显式 plotly mode，PNG-only 使用显式 image mode；模式来自 artifact capability，不依 dataset/model 名称。
- Text panel 仅用于叙述，不得用 `<img>` 或 CSS 图片绕过 plugin。非法 figure 不静默降级；PNG image mode 不冒充互动图。此為 NLAP-07 的 source 契約，live 部署與 browser 結果須另看對應驗證紀錄。
- narrative 保留同 panel 的觀察／解讀／限制／數值證據；可選跨圖關係／下一步缺省時不顯示空標題。沒有逐 view 敘事時，單圖也必須保留主要白話說明。
- 2026-09-11 新設計預設以完整 figure 呈現，取代下方 responsive grid／拆分 view 的強制重排；檢視摘要不能識別某 trace 不構成拒絕原生圖的理由。
- Grafana row 使用 `askO11ySectionId` metadata；write gate 只验证 section id、panel narrative、artifact/fact evidence 与 bounds，不验证固定角色、标题、顺序或中文词句。Compose 后以 opaque `dashboard_ref` 传递完整 dashboard，避免把大 JSON 再生成一次。

## Plotly responsive grid

统一 `responsive_subplot_grid(count)`：

| count | grid |
| ---: | --- |
| 1 | 1×1 |
| 2 | 1×2 |
| 3–4 | 2×2 |
| 5–6 | 2×3 |
| 7–9 | 3×3 |
| 10–12 | 3×4 |

每个 axis 同时取得 x/y domain；禁止 12 图单排。ChartSpec / synthesis width 控制 full/half；多子图、heatmap、SHAP、error slices 由 LLM 标为 full，但 compositor 会以 min-size guard 覆盖不安全的 half。

## Plotly plugin reuse decision

已查核 Grafana Plugin Catalog：`nline-plotlyjs-panel` 是 active、community-signed、支持 Grafana theme、resize 与静态 data/layout/config；`ae3e-plotly-panel` 较旧；Grafana Labs 维护的 Business Charts 使用 ECharts 而非 Plotly。nLine 最接近需求，因此复用其 `useTheme2`、theme merge、resize handler 思路。

不直接采用 nLine：其 `useScriptEvaluation` 对 dashboard `options.script` 使用 `new Function`，且 panel 暴露 Processing Script/event script/datasource 能力；不符合本项目只接收 Bridge-sanitized static figure、无 eval/script、同 panel evidence narrative 与受控 PNG fallback 的安全边界。保留最小内部 plugin，并在正式部署前签章。

参考：

- <https://grafana.com/grafana/plugins/nline-plotlyjs-panel/>
- <https://github.com/nline/nline-plotlyjs-panel/blob/main/src/useChartConfig.ts>
- <https://github.com/nline/nline-plotlyjs-panel/blob/main/src/useScriptEvaluation.ts>

## Grafana-native theme

`asko11y-plotly-panel` 使用 `useTheme2()`：

- paper/plot background transparent，继承 panel background。
- font、axis、grid、zero line、legend 使用 Grafana theme tokens。
- light/dark 自动切换；不把颜色写死在 report figure。
- `ResizeObserver` 读取实际容器，`autosize=true`，变化时 `Plotly.Plots.resize()`。
- 小于 figure 最小尺寸时显示「展开查看」或 PNG fallback，不压缩成无效缩图。

## TDD slices

### A. Report manifest and synthesis contract

RED：Plotly-only、PNG-only、valid pair selects Plotly、invalid figure + PNG rejects、missing/duplicate/orphan output、普通 summary + 错误 index rejects、bounded facts/raw-carrier rejection。GREEN：`ml_report_contract.py` 的 Host normalizer、immutable manifest ref 与 fact catalog；不得出现 required role/order。

RED：不同 section 形状的 valid synthesis、unknown artifact/fact、数字幻觉、HTML/URL、重复 section id、越界 sections/panels。GREEN：报告 synthesis 仍只依赖 manifest/ref 提供的 capability 与 facts。

### B. Figure inspection + Bridge tools

RED：multi-subplot figure spec 的 view/title/X/Y/scale/range/point bounds；vision/spec 两模式；MCP image blocks；opaque report_context_ref/inspection_ref；未检查 artifact/view 不得 compose；vision/spec receipt 与 visual_observation 一致性。GREEN：`ml_figure_inspection.py`、`prepare_ml_report`、`inspect_report_artifacts`。

### C. Generic compositor

RED：classification、correlation、time-series 三个不同 fixture 经同一 compositor；production source 不含 fixture dataset/field names；每个 selected view 必须有独立 evidence-bound narrative；vision view 必须有视觉观察、spec-only 必须为 null；opaque bindings 可 resolve。GREEN：`ml_dashboard_compositor.py`、`compose_ml_dashboard` 与 view-level renderer。

### D. Plotly responsive/theme

RED：1..12 grid rows/columns/domain 不重叠；axis 最小宽高；dark/light theme pure function；ResizeObserver/fallback；Chromium screenshots。GREEN：shared grid + themed plugin。

### E. Ask O11y skill / E2E

Skill 只规定安全工具边界：先取得完整 bounded report context，再由 LLM 自主规划报告 flow/content 并一次输出 synthesis，最后交 compositor/validator；不得规定分析步骤、section 角色、固定图表或固定文字。测试 fixture 可提供不同形状的 deterministic synthesis；production 不可使用 Telco builder。

## 实作与验证结果

- 新增 `ml_report_contract.py`：flow-agnostic synthesis schema、bounded fact catalog、artifact/fact evidence guard、numeric hallucination 与 unsafe text fail closed。
- 新增 `ml_dashboard_compositor.py`：忠实保留 LLM section/order/content/collapsed；不识别 dataset、字段、固定图名或 required roles；同 panel 写入结构化 narrative/evidence。
- Artifact Bridge 新增 `prepare_ml_report`、`inspect_report_artifacts` 与 `compose_ml_dashboard`；prepare 持久化 opaque context，inspect 支持 vision/spec、PNG MCP image blocks、完整 sanitized Plotly JSON、figure/view/axis/scale specs 与 receipts；compose 强制整份报告 inspection coverage。
- Ask O11y skill 已移除固定回报模板、固定五幕、固定 chart list；要求一次阅读整份报告再 synthesis。
- Plotly responsive grid 通过一到十二个 subplot domain/non-overlap tests；plugin 使用 Grafana `useTheme2`、transparent theme merge、ResizeObserver 与 `Plots.resize`，无 Processing Script/new Function。Multi-view figure 在同一 Grafana panel 内拆成独立 subpanel cards，移除原 subplot domain/anchor 并保留每个 view 标题与独立 X/Y。
- Deterministic axis policy：count/bar 从零、category/linear 明示、rate 使用 percent tickformat、figure specs 暴露 title/unit/scale/range/min/max；plugin v0.2.1 浏览器实测 X/Y title 正确。Multi-view 会移除原 subplot domain/anchor、关闭单 trace 重复 legend，并依 view spec 生成独立 subpanel。
- 真实 LLM synthesis：读取完整 report context 与全部图，动态选择四个 section、八个 artifacts；通过 evidence validator 后写入 UID `dynamic-llm-report-e2e`。
- Chromium 在 dark theme 与两种 viewport 通过：图表、数值 evidence、观察／解读／跨图关系／限制／下一步同 panel 可见，无 page/console error。Compositor 显示顶层 LLM thesis；LLM 亦可动态加入无图 conclusion section。
- Model-first per-view synthesis 已验证：当前真实报告的八个 artifacts／十七个 selected views 各自有独立 data observation、visual observation、interpretation、limitation、next step 与专属 evidence；不再以一份 artifact caption 复用所有 subpanels。Bridge 依 inspection mode 验证：vision view 必须有 visual observation，spec-only 必须为 null。
- Single-view 去重：只有一个 selected view 时，UI 只显示 view narrative，不重复渲染 panel-level summary；多 view 时才显示跨-view panel 总结。静态 PNG 与 Plotly plugin 使用相同规则。
- Live MCP 实测：vision batch 返回 text + 八个 image blocks；spec batch 返回六图完整 JSON/spec 且不声称 vision；两份 inspection receipts 覆盖十四图后 compose 成功，缺覆盖 fail closed。
- Executive/closing evidence：synthesis 新增 `thesis_evidence` 与任意 evidence-bound `narrative_blocks`；当前顶部以每单位 actual/TP/FN/FP/TN/action workload 与基准改善说明执行结果，最终 adoption judgement 绑定 recall/precision/错误量/泛化/营运状态。
- Deterministic classification manifest 新增 `operational_summary`（可配置 normalization denominator）；成本矩阵 provided 与 business approved 分离，默认未核准，当前状态正确显示营运采用待确认。
- 当前 LLM 将正文精简为决策相关 views，并动态加入默认 collapsed technical evidence section（资料切分、模型／调参、校正／门槛、稳定性、完整诊断图）；production 不固定该 section 或图清单。

## 验收

- Live WFERP check：`scripts/check-wferp-data-understanding.py` 以 schema search 的实际 table/field evidence 动态产生 bounded SQL，完成 Grafana Query → full profile → all-artifact inspect → generic compose；不呼叫 ML。
- Native recovery 的历史运行有修正错误记录，但现有 `check-native-recovery-e2e.py` 只验证摘要，不能证明完整运行链；NLAP-01 要求从原始 run/session/tool-call events 验证，不允许合并 continuation 冒充 fresh E2E。
- Production 不得依业务字段名或固定 chart list 决策；2026-09-05 审查发现 upload roles 与 regression 文案仍有业务 hardcode，待 NLAP-08 修正，不能宣称全项目已无 hardcode。
- 同一 compositor 渲染 classification、correlation、time-series fixtures。
- 每个正文 chart 都有本报告上下文相关的 narrative 与 validated evidence facts。
- Plotly 在 Grafana dark/light 与 1440×900、1280×720 无白底割裂、重叠或无效缩图。
