# Adaptive Ask O11y ML Platform Context

The active decisions are `docs/adr/0001-grafana-executes-datasource-queries.md`, the retained boundaries of ADR 0002, and `docs/adr/0003-isolated-python-analysis-mcp.md`.

## Runtime planning

Ask O11y's pinned LLM is the only runtime planner. It first selects a compact relevant subset from the currently registered tool schemas, then composes those capabilities from user intent, authorized Grafana metadata and intermediate results. Successful opaque refs and identities are restored as compact cross-turn evidence; no raw tool payload is restored. It does not follow a fixed DAG, keyword router, `next_step` chain, method enum or panel template.

Metadata discovery may happen before the advisory Analysis Preview. Datasource query and Sandbox analysis happen only after the user confirms it. Pure analysis ends with a chat Result Preview. When Grafana output was requested, execution instead ends with an approval-gated, expiring Grafana Preview URL that the user can inspect; a chat summary is not treated as a visual preview. Formal publication requires a later explicit confirmation and a second host-approved Renderer call. A material change requires a revised Analysis Preview and confirmation.

Generated Python runs only in the dedicated Sandbox Analysis MCP; it never runs in Ask O11y or an infrastructure MCP process.

## Four permission seams

1. **Data Query Planner MCP** creates bounded query plans from authorized metadata and never executes a datasource query.
2. **Grafana Query MCP** is the sole Grafana metadata and datasource-read boundary and writes bounded opaque frame artifacts.
3. **Sandbox Analysis MCP** transfers one authorized native Grafana columnar frame plus validity rules as bounded JSON, constructs filtered `df` in a fresh network-denied OpenSandbox Code Interpreter, and returns opaque rich-output artifacts. It also lists, inspects, and revises retained analyses across conversations.
4. **Grafana Renderer MCP** is the approval-gated dashboard-write seam. It reads the live Grafana panel catalog, builds dynamic native query panels from an authorized `plan_ref`, can bind named CSV Sandbox outputs to native inline Infinity queries, and still renders authorized PNG/sanitized HTML/text artifacts. `prepare_dashboard_write` binds the exact payload to a server-issued capability without writing; the separately host-approved `create_temporary_dashboard_preview` creates an expiring preview-tagged dashboard. After explicit confirmation, `create_dashboard_from_artifacts` promotes the exact payload at the same UID. It never accepts raw frames, query bodies, MIME payloads, datasource identity, or panel targets from the model.

All four MCP services listen on loopback, require a service bearer, and bind that bearer to configured server-side org/user identity.

The retired Engineering and Finance Analysis MCPs, their fixed method contracts, and `analysis_core` are not runtime paths.

## Grafana data and rendering

Grafana remains the only datasource executor. Cross-MCP payloads use opaque refs such as `artifact://<run_id>/grafana-frame`, authorized against org/user run metadata. Model-visible arguments never contain physical paths, credentials, tokens, raw datasource queries, full frames or complete Jupyter payloads.

Ask O11y's built-in Grafana MCP remains enabled alongside the external artifact/Sandbox capabilities. The LLM dynamically selects query, Sandbox, and dashboard tools; these seams impose no fixed path. Sandbox output is analysis evidence, not automatically a dashboard. When Grafana output is requested, the generic Renderer accepts either an authorized `sandbox-execution` ref or an authorized `plan_ref` plus schema-declared visualization specs and returns a real temporary Grafana Preview URL. A later confirmed write preserves the preview UID while removing preview status and returns distinct `dashboard_uid`, `dashboard_slug`, and `dashboard_url`; only then may Ask O11y state that the dashboard is formally published.

## Artifact retention and provenance

The PoC stores authorized server-side artifacts under `.analysis-artifacts/runs/<run_id>/`; this physical path is never exposed to the model. Startup cleanup removes large frame, query, Sandbox execution/code, and generated chart artifacts after retention expiry.

Sandbox provenance records the source hash, input ref, image, runtime class, seed, limits and validity filtering. The image pins the common tabular/ML stack, including SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn, and Optuna. Dependency source, immutable versions, licenses, NOTICE obligations and lock/SBOM entries are recorded in `docs/third-party-reuse-manifest.json`, `NOTICE`, `docs/sbom.json`, and `uv.lock`.
