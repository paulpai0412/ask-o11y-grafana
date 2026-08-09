# Adaptive Ask O11y ML Platform Context

This file describes the active PoC. Architectural decisions and reuse rationale are authoritative in `docs/adr/0002-adaptive-ask-o11y-ml-mcp-topology.md`.

## Runtime planning

Ask O11y's pinned LLM is the only runtime planner. It selects registered high-level MCP tools from the user's intent, tool schemas, authorized Grafana metadata, and intermediate results. It does not follow a fixed DAG, keyword router, `next_step` chain, or method/panel template.

Metadata discovery may happen before the advisory preview. Datasource query, deterministic analysis, and dashboard mutation happen only after the user confirms the preview in the same conversation. A material change requires a revised preview and confirmation.

Production analysis requests do not launch Pi, Codex, Claude, subagents, skills, shell, or arbitrary code execution.

## Five permission seams

1. **Data Query Planner MCP** consumes an authorized opaque metadata artifact and only creates or validates a bounded query plan. It has no Grafana credentials, HTTP client, datasource discovery, query execution, or metadata refresh capability.
2. **Grafana Query MCP** is the sole Grafana metadata and datasource-read boundary. It discovers authorized datasets, writes sanitized opaque metadata artifacts, executes validated plans through Grafana, and writes bounded opaque frame artifacts.
3. **Engineering Analysis MCP** consumes authorized frame artifacts and exposes selectable high-level profile, correlation, predictive, patterns, and timeseries capabilities.
4. **Finance Analysis MCP** consumes authorized frame artifacts and exposes deterministic cost-driver and variance capabilities. Real Finance Grafana/Ask O11y E2E is deferred (`finance_real_e2e=false`).
5. **Grafana Renderer MCP** consumes an authorized AnalysisResult/VisualizationSpec and is the approval-gated dashboard-write boundary.

All five services listen on loopback, require a service bearer, and bind that bearer to the configured server-side org/user identity.

## Deterministic analysis

Engineering and Finance contain domain validation and interpretation. Neither directly accesses Grafana, files, databases, external APIs, or datasource credentials.

`analysis_core` is an in-process Python library shared by those two domain callers. It contains only deterministic frame validation, profiling, correlation, supervised/unsupervised/timeseries mechanics, evaluation, visualization specs, and provenance. It contains no MCP transport, artifact IO, Grafana access, domain rules, or orchestration.

## Grafana data and rendering

Grafana remains the only datasource executor. Cross-MCP payloads use opaque refs such as `artifact://<run_id>/grafana-frame`, authorized against org/user run metadata. Model-visible arguments never contain physical paths, credentials, tokens, raw datasource queries, or full frames.

Renderer supports table, timeseries, bar, scatter, and ESNET correlation heatmap panels. Panel count, fields, titles, and text come from the selected AnalysisResult rather than a fixed dashboard template.

## Artifact retention

The local PoC stores server-side artifacts under `.analysis-artifacts/runs/<run_id>/`; this physical path is never exposed to the model. `ANALYSIS_ARTIFACT_RETENTION_DAYS` defaults to seven days. Startup cleanup removes Grafana frames/query responses, dynamic Engineering/Finance method and analysis artifacts, and generated chart CSV directories while retaining compact evidence and metadata needed for audit.

## Provenance and reuse

Every deterministic method records library/model versions, seed where applicable, assumptions, limitations, validation, and `runtime_agent=false`, `runtime_llm=false`, `runtime_skill=false`.

Third-party decisions, immutable versions/commits, licenses, NOTICE obligations, patches, and SBOM entries are recorded in `docs/third-party-reuse-manifest.json`, `NOTICE`, and `docs/sbom.json`. The official MCP Python SDK and `grafana/mcp-grafana` were evaluated in bounded compatibility spikes; their adoption/rejection decisions are recorded rather than silently vendoring or rewriting upstream servers.
