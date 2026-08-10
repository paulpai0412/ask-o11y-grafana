# ADR 0002: Adaptive Ask O11y ML MCP Topology

Status: Superseded by ADR 0003

## Historical decision

Ask O11y was selected as the only runtime LLM planner. It previews an advisory plan, waits for confirmation, and dynamically selects registered tool schemas without a fixed DAG, keyword router, target, method, panel template, or mandatory next step.

This ADR originally introduced deterministic Engineering and Finance Analysis MCPs plus `analysis_core`. ADR 0003 removes those components and replaces them with one isolated Python Sandbox Analysis MCP.

## Decisions retained by ADR 0003

- Query Planner is plan-only.
- Grafana Query is the only datasource executor used for opaque analysis-frame artifacts.
- Artifacts are bounded and bound to authenticated org/user context.
- Ask O11y's built-in Grafana MCP remains available for native dynamic query/dashboard work.
- Every custom Renderer mutation requires both Ask O11y host approval and a short-lived, exact, one-time server capability.
- No caller-controlled boolean constitutes write approval.
- Direct database access, runtime skills, fixed workflow chains, and a universal analysis framework remain rejected.
- The official MCP Python SDK and `grafana/mcp-grafana` remain evaluated reference implementations rather than adopted runtime dependencies; see `docs/third-party-reuse-manifest.json`.

The current architecture and acceptance criteria are defined by `docs/adr/0003-isolated-python-analysis-mcp.md` and `docs/design/sandbox-analysis-mcp.md`.
