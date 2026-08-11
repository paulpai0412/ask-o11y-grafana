# Ask O11y natural-language dashboard authoring

## Finding

Ask O11y can accept only natural language from its user, but its current native implementation still has the LLM construct the arguments for a Grafana MCP mutation tool. There is no built-in semantic command such as `create_xy_chart(x, y, plan_ref)` in the configured tool path.

The current write path is:

```text
natural-language request
  → Ask O11y LLM chooses MCP tools
  → LLM emits mcp-grafana_update_dashboard arguments
  → Grafana built-in MCP persists dashboard JSON / patch
```

Thus `mcp-grafana_update_dashboard` is the native Grafana writer, but not a non-JSON dashboard builder. In local evidence, its tool call accepted an arbitrary `dashboard` object and persisted an incompatible `xychart.options` shape; Grafana accepted the dashboard while the panel could not render.

## Source evidence

- Ask O11y's upstream default prompt explicitly directs the LLM to create an empty dashboard and then add panels iteratively with the update-dashboard tool. It is an LLM-authored JSON workflow, not a typed panel-construction API. [Source](https://github.com/Consensys/ask-o11y-plugin/blob/main/pkg/plugin/prompt_defaults.go)
- Ask O11y's loop lists MCP tool schemas, converts them to OpenAI tool definitions, and supplies them to the LLM. [Source](https://github.com/Consensys/ask-o11y-plugin/blob/main/pkg/agent/loop.go)
- Grafana documents its MCP server as exposing dashboard-management tools to MCP clients; it does not claim that the server transforms natural-language chart intent into version-specific panel definitions. [Source](https://grafana.com/docs/grafana/latest/developer-resources/mcp/)
- The installed runtime selected the embedded `dashboarding` skill (`selectedSkills=[dashboarding]`), yet the LLM emitted the obsolete `xychart.options.series.xy` form. A skill is advisory context, not an enforcing API contract.

## Alternatives

| Approach | Does the LLM write Grafana JSON? | Reliability | Trade-off |
| --- | ---: | --- | --- |
| Current Ask O11y built-in MCP | Yes | Low for version-specific panels | Maximum Grafana flexibility |
| Grafana Assistant UI | Hidden from the user, but still generated internally | Depends on its implementation | Different product path; not a semantic builder exposed to Ask O11y |
| Dashboard provisioning / Terraform / Scenes | Yes, as code/config | High after review | Not natural-language runtime authoring |
| Deterministic panel-builder MCP | No; it supplies typed intent | High for supported panel types | Must explicitly support each panel contract |

## Recommended design

Keep Ask O11y and built-in `mcp-grafana_update_dashboard` as the sole writer, but expose a narrow typed authoring tool before it:

```text
create_visualization({
  panel_type: "xychart",
  x_field: "avg_generation_mw",
  y_fields: ["heat_rate"],
  plan_ref: "artifact://.../query-plan"
})
```

The server validates the request against a versioned, installed-Grafana panel contract, produces the panel JSON deterministically, resolves opaque bindings, and delegates the write to `mcp-grafana_update_dashboard`. The LLM still plans dynamically and chooses the panel; it simply cannot invent option shapes.

For unsupported panels or arbitrary plugin options, return a clear capability error or retain an explicitly reviewed JSON-authoring path. A generic natural-language system cannot safely cover every Grafana plugin/version without such a contract layer.
