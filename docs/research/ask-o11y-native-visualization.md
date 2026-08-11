# Ask O11y v0.3.2：原生視覺化與外部 MCP 的實際邊界

查核日期：2026-08-10。原始碼固定於 Ask O11y `v0.3.2`。

## 結論

Ask O11y 是 MCP **工具聚合與 agent loop**，不是把任何外部 MCP 回傳值自動轉成 Grafana panel 的轉接器。

它有兩條不同的原生視覺化路徑：

1. **聊天內嵌圖**：LLM 在回覆中輸出 PromQL/LogQL/TraceQL fenced code block；前端只解析這三種語言，並以 Grafana Scene `SceneQueryRunner` 對 Prometheus/Loki/Tempo datasource 執行原 query，再用選定的原生 panel 渲染。
2. **持久 Dashboard**：Ask O11y 將 Grafana LLM app 的 built-in MCP 註冊為 MCP server，URL 是 `/api/plugins/grafana-llm-app/resources/mcp/grafana`；LLM 透過該 server 的 dashboard CRUD tools 建立 panel JSON，panel target 仍是 Grafana datasource query。

所以原生 Grafana 可以直接顯示「來源 datasource 可重跑的 query」；不需要先把結果轉成另一份 chart data。

## 外部 Analysis MCP 為何不能直接沿用

MCP `tools/call` 的回應不是 Grafana datasource。原始 Ask O11y 的 MCP proxy 只會：

- 動態列舉各 MCP 的 tool schemas；
- 將工具呼叫依 server-id 路由；
- 回傳 tool result 給 agent。

它不會將任意 JSON、DataFrame 或 artifact 註冊成 Grafana query target。因此，分析結果若不再能從原始 datasource 重算，就至少需要一個 Grafana 可查詢的表面：

- 只使用 Grafana transformations 能完成的計算：Dashboard 直接 query 原 datasource，將 transformation 寫進 panel；無須 Analysis MCP。
- ML／預測／相關矩陣等結果：將輸出保存成受控 PNG artifact；橋接器解析為短效簽名 URL，供模型撰寫的 image/text dashboard panel 顯示。

這不是多餘資料流，而是 Grafana 必須在 panel render 時取得資料的必要介面。不能安全地讓 dashboard 直接引用一次性 MCP tool result。

## 對此 repo 的影響

目前已採用 sandbox 路徑：Ask O11y built-in `mcp-grafana_update_dashboard` 是唯一 dashboard writer；隱藏的 Artifact Bridge 只解析 query-only 的 `$plan_ref` 或 analysis PNG asset placeholders。舊 Engineering/Finance MCP 與外部 Renderer 已移除。

分流如下：

- **query-only**：built-in Grafana MCP 建立保有原 datasource targets 的 dashboard。
- **analysis-required**：Sandbox Analysis 產出 PNG artifact 與摘要；Bridge 安全地解析 image binding，仍由 built-in MCP 寫入 dashboard。

不要要求 Ask O11y 從任意 JSON 猜圖；分析輸出至少應包含 panel type 與欄位語意（time/value、category/value、x/y 等）。

## Primary sources

- Ask O11y v0.3.2 `pkg/plugin/plugin.go`：`ensureBuiltInMCPRegistered()`，built-in server endpoint 與 service-account authentication。
  <https://github.com/Consensys/ask-o11y-plugin/blob/v0.3.2/pkg/plugin/plugin.go#L467-L513>
- Ask O11y v0.3.2 `pkg/mcp/proxy.go`：generic tool aggregation and routing; no result-to-Grafana adapter.
  <https://github.com/Consensys/ask-o11y-plugin/blob/v0.3.2/pkg/mcp/proxy.go>
- Ask O11y v0.3.2 `src/components/Chat/utils/promqlParser.ts`：chat renderer accepts only PromQL/Prometheus, LogQL/Loki, TraceQL/Tempo fenced blocks.
  <https://github.com/Consensys/ask-o11y-plugin/blob/v0.3.2/src/components/Chat/utils/promqlParser.ts>
- Ask O11y v0.3.2 `src/components/Chat/components/GraphRenderer/GraphRenderer.tsx`：creates a Prometheus `SceneQueryRunner`, then builds Grafana panels locally.
  <https://github.com/Consensys/ask-o11y-plugin/blob/v0.3.2/src/components/Chat/components/GraphRenderer/GraphRenderer.tsx>
- Ask O11y v0.3.2 `pkg/plugin/prompt_defaults.go`：native dashboard creation is delegated to dashboard create/update tools.
  <https://github.com/Consensys/ask-o11y-plugin/blob/v0.3.2/pkg/plugin/prompt_defaults.go#L64-L72>
- Current local config: `.scratch/poc/ask-o11y-workflow-tools-current-settings.json` (`useBuiltInMCP: false`).
