# Generic LLM Report Synthesis + Grafana-native Plotly

状态：implemented（TDD + live Grafana E2E）

## 决议

分析计算保持 deterministic；报告的重点选择、跨图推理、每图叙事与正文／附录编排交给 Ask O11y LLM。LLM 必须一次读取整份 bounded report context，不逐图独立生成 caption；所有数值由 fact references 渲染，LLM 不得改写事实、部署状态或权限结论。

Telco 专用 builder 只可作为 E2E fixture，production contract/compositor 不得包含 dataset 名、字段名或固定 chart 清单。

## 流程

```text
Sandbox deterministic analysis
  → manifest + PNG + sanitized Plotly JSON
Artifact Bridge.prepare_ml_report
  → opaque report_context_ref + artifact catalog + bounded fact catalog + figure/view specs
Artifact Bridge.inspect_report_artifacts
  → PNG MCP image blocks + sanitized Plotly JSON + inspection_ref（vision 不可用时仍有完整 spec）
Ask O11y LLM（分 bounded 批次看完完整报告与全部图）
  → ReportSynthesis JSON
Artifact Bridge.compose_ml_dashboard
  → require report_context_ref + inspection receipts → validate facts/artifacts/views → generic dashboard with opaque bindings
Artifact Bridge.resolve_dashboard_refs
  → Grafana writer
```

## LLM inspection transport

`prepare_ml_report` 不只列出 artifact 名称；它会持久化 bounded report context，并为每张 Plotly 图提供 deterministic `figure_spec`：trace types/count、subplot `view_id`、title、X/Y label、unit/tickformat、scale/range、point count 与 min/max。它返回 opaque `report_context_ref`，不暴露 raw rows 或 signed URL。

`inspect_report_artifacts(report_context_ref, artifact_ids, mode)`：

- `mode=vision`：返回 PNG 为 MCP `image` content blocks，同时返回完整 sanitized Plotly JSON 与 figure/view specs。
- `mode=spec`：供不支持 vision 的模型使用，返回完整 Plotly JSON、aggregate values 与 view specs，不假装已视觉看图。
- 每次 inspection 写入 opaque `inspection_ref`；可 bounded 分批，但 compose 前必须覆盖 synthesis 选择的所有 artifact/view。
- `compose_ml_dashboard` 不再接受原始 execution/manifest 参数，只接受 `report_context_ref`、`inspection_refs` 与 synthesis；缺 inspection coverage fail closed。

## Model-first 双轨解读

LLM 必须先读取 deterministic facts、sanitized Plotly JSON 与 figure/view specs，再读取 PNG 验证分布形状、颜色、聚类、重叠与渲染问题。数值结论只能来自 model/facts；视觉观察只能描述像素中可见的形状，不得自行产生数字。两者冲突时以 data model 为事实，并将冲突标记为图表／渲染问题。

每个 selected `view_id` 都必须有独立 `view_narrative`：`data_observation`、`visual_observation`、`interpretation`、`limitation`、`next_step` 与 evidence。Vision receipt 覆盖的 view 必须提供视觉观察；只有 spec receipt 时 `visual_observation` 必须为 `null`，不得声称看过图片。

## ReportSynthesis contract

```json
{
  "format": "ask-o11y-report-synthesis-v1",
  "report_title": "string",
  "thesis": "string（不可含自行编造的数字）",
  "sections": [
    {
      "section_id": "LLM 产生的稳定 id",
      "title": "string",
      "purpose": "本 section 在本次报告中要回答的问题",
      "collapsed": false,
      "panels": [
        {
          "artifact_id": "manifest artifact stem",
          "view_ids": ["deterministic subplot view id"],
          "view_narratives": [
            {
              "view_id": "deterministic subplot view id",
              "headline": "此 view 的结论句",
              "data_observation": "来自 Plotly model/facts 的观察",
              "visual_observation": "来自 PNG 的形状观察；spec-only 时为 null",
              "interpretation": "为什么重要",
              "limitation": "不能推论什么",
              "next_step": "下一步",
              "evidence": [{"fact_ref": "bounded fact id", "format": "percent_1"}]
            }
          ],
          "headline": "跨 views 的 panel 总结",
          "observation": "观察到什么",
          "interpretation": "为什么重要",
          "cross_chart_context": "与整份报告其他证据的关系",
          "limitation": "不能推论什么",
          "next_step": "下一步验证什么",
          "evidence": [{"fact_ref": "bounded fact id", "format": "percent_1"}],
          "priority": "primary | supporting | technical",
          "preferred_width": "full | half"
        }
      ]
    }
  ]
}
```

不规定 section 数量、role、标题、顺序或正文／附录结构。LLM 根据用户目的、artifact capabilities 与整份报告事实动态决定；validator 只限制 bounded shape、安全字符、artifact/fact evidence 与总量。

## Fact / claim guard

- `prepare_ml_report` 从 manifest 递归产生 bounded scalar fact catalog，不暴露 raw rows。
- 每个 panel 必须引用存在的 artifact 与至少一个 `evidence {fact_ref, format}`。
- LLM narrative 字段不得包含数字；数值 evidence chips 由 compositor 根据 `fact_refs` deterministic 渲染。
- deployment / operational status 只能读取 manifest，LLM 不可覆盖。
- 未知 artifact、未知 fact、重复 section id、越界 section/panel 数、HTML/script/URL 全部 fail closed。

## Generic dashboard compositor

- 只读取 LLM 产生的 section 顺序、collapsed、priority、preferred width 与 artifact references；不识别固定 story roles、dataset、字段或图名。
- LLM 决定 section 数量、图表选择、正文／折叠与顺序；compositor 不自动重排内容，只在尺寸不足或安全越界时 fail closed／提升为 full width。
- 有 Plotly output 时建立 `asko11y-plotly-panel` + PNG fallback；否则建立 image/text panel。
- narrative 以同 panel 的结构化区块呈现：观察／解读／跨图关系／限制／下一步／数值证据。
- Grafana row 使用 `askO11ySectionId` metadata；write gate 只验证 section id、panel narrative、artifact/fact evidence 与 bounds，不验证固定角色、标题、顺序或中文词句。

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

### A. Report synthesis contract

RED：不同 section 形状的 valid synthesis、unknown artifact/fact、数字幻觉、HTML/URL、重复 section id、越界 sections/panels。GREEN：`ml_report_contract.py` + fact catalog；不得出现 required role/order。

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

## 验收

- Production Python/TypeScript 中无 `Telco`、业务字段名或固定 14 chart list。
- 同一 compositor 渲染 classification、correlation、time-series fixtures。
- 每个正文 chart 都有本报告上下文相关的 narrative 与 validated evidence facts。
- Plotly 在 Grafana dark/light 与 1440×900、1280×720 无白底割裂、重叠或无效缩图。
