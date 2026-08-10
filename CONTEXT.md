# Adaptive Ask O11y ML Platform Context

The active decisions are `docs/adr/0001-grafana-executes-datasource-queries.md`, the retained boundaries of ADR 0002, and `docs/adr/0003-isolated-python-analysis-mcp.md`.

## Runtime planning

Ask O11y's pinned LLM is the only runtime planner. It selects registered MCP tools from user intent, schemas, authorized Grafana metadata and intermediate results. It does not follow a fixed DAG, keyword router, `next_step` chain, method enum or panel template.

Metadata discovery may happen before the advisory preview. Datasource query, Sandbox analysis and dashboard mutation happen only after the user confirms the preview. A material change requires a revised preview and confirmation.

Generated Python runs only in the dedicated Sandbox Analysis MCP; it never runs in Ask O11y or an infrastructure MCP process.

## Four permission seams

1. **Data Query Planner MCP** creates bounded query plans from authorized metadata and never executes a datasource query.
2. **Grafana Query MCP** is the sole Grafana metadata and datasource-read boundary and writes bounded opaque frame artifacts.
3. **Sandbox Analysis MCP** transfers one authorized native Grafana columnar frame plus validity rules as bounded JSON, constructs filtered `df` in a fresh network-denied OpenSandbox Code Interpreter, and returns opaque rich-output artifacts. It also lists, inspects, and revises retained analyses across conversations.
4. **Grafana Renderer MCP** is the approval-gated dashboard-write boundary. It generically renders authorized Sandbox PNG, sanitized HTML, text, and JSON outputs without accepting raw MIME from the model.

All four MCP services listen on loopback, require a service bearer, and bind that bearer to configured server-side org/user identity.

The retired Engineering and Finance Analysis MCPs, their fixed method contracts, and `analysis_core` are not runtime paths.

## Grafana data and rendering

Grafana remains the only datasource executor. Cross-MCP payloads use opaque refs such as `artifact://<run_id>/grafana-frame`, authorized against org/user run metadata. Model-visible arguments never contain physical paths, credentials, tokens, raw datasource queries, full frames or complete Jupyter payloads.

Ask O11y's built-in Grafana MCP remains enabled alongside the external artifact/Sandbox capabilities. The LLM dynamically selects query, Sandbox, and dashboard tools; these seams impose no fixed path. Sandbox output is analysis evidence, not automatically a dashboard. When requested, the generic Renderer consumes an authorized `sandbox-execution` ref, previews supported MIME outputs, and creates Grafana text panels only after both its one-time capability and Ask O11y host approval succeed.

## Artifact retention and provenance

The PoC stores authorized server-side artifacts under `.analysis-artifacts/runs/<run_id>/`; this physical path is never exposed to the model. Startup cleanup removes large frame, query, Sandbox execution/code, and generated chart artifacts after retention expiry.

Sandbox provenance records the source hash, input ref, image, runtime class, seed, limits and validity filtering. The image pins the common tabular/ML stack, including SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn, and Optuna. Dependency source, immutable versions, licenses, NOTICE obligations and lock/SBOM entries are recorded in `docs/third-party-reuse-manifest.json`, `NOTICE`, `docs/sbom.json`, and `uv.lock`.
