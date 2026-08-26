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
  → artifact catalog + bounded fact catalog + restrictions
Ask O11y LLM（一次看完整报告与全部图）
  → ReportSynthesis JSON
Artifact Bridge.compose_ml_dashboard
  → validate facts/artifacts → generic dashboard with opaque bindings
Artifact Bridge.resolve_dashboard_refs
  → Grafana writer
```

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
          "headline": "本图的结论句",
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

### B. Generic compositor + Bridge tools

RED：classification、correlation、time-series 三个不同 fixture 经同一 compositor；production source 不含 fixture dataset/field names；opaque bindings 可 resolve。GREEN：`ml_dashboard_compositor.py`、`prepare_ml_report`、`compose_ml_dashboard` tools。

### C. Plotly responsive/theme

RED：1..12 grid rows/columns/domain 不重叠；axis 最小宽高；dark/light theme pure function；ResizeObserver/fallback；Chromium screenshots。GREEN：shared grid + themed plugin。

### D. Ask O11y skill / E2E

Skill 只规定安全工具边界：先取得完整 bounded report context，再由 LLM 自主规划报告 flow/content 并一次输出 synthesis，最后交 compositor/validator；不得规定分析步骤、section 角色、固定图表或固定文字。测试 fixture 可提供不同形状的 deterministic synthesis；production 不可使用 Telco builder。

## 实作与验证结果

- 新增 `ml_report_contract.py`：flow-agnostic synthesis schema、bounded fact catalog、artifact/fact evidence guard、numeric hallucination 与 unsafe text fail closed。
- 新增 `ml_dashboard_compositor.py`：忠实保留 LLM section/order/content/collapsed；不识别 dataset、字段、固定图名或 required roles；同 panel 写入结构化 narrative/evidence。
- Artifact Bridge 新增 `prepare_ml_report` 与 `compose_ml_dashboard`，live MCP tools/list 已可见；Plotly capability 以 trace/axis 数与 chart family 自动给 full/half/min-height guard，不看 artifact 名。
- Ask O11y skill 已移除固定回报模板、固定五幕、固定 chart list；要求一次阅读整份报告再 synthesis。
- Plotly responsive grid 通过一到十二个 subplot domain/non-overlap tests；plugin 使用 Grafana `useTheme2`、transparent theme merge、ResizeObserver 与 `Plots.resize`，无 Processing Script/new Function。
- 真实 LLM synthesis：读取完整 report context 与全部图，动态选择四个 section、八个 artifacts；通过 evidence validator 后写入 UID `dynamic-llm-report-e2e`。
- Chromium 在 dark theme 与两种 viewport 通过：图表、数值 evidence、观察／解读／跨图关系／限制／下一步同 panel 可见，无 page/console error。

## 验收

- Production Python/TypeScript 中无 `Telco`、业务字段名或固定 14 chart list。
- 同一 compositor 渲染 classification、correlation、time-series fixtures。
- 每个正文 chart 都有本报告上下文相关的 narrative 与 validated evidence facts。
- Plotly 在 Grafana dark/light 与 1440×900、1280×720 无白底割裂、重叠或无效缩图。
