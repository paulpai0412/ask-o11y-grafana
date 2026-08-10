# Adaptive Ask O11y ML platform context

The active decisions are [ADR 0001](docs/adr/0001-grafana-executes-datasource-queries.md), the retained boundaries of [ADR 0002](docs/adr/0002-adaptive-ask-o11y-ml-mcp-topology.md), and [ADR 0003](docs/adr/0003-isolated-python-analysis-mcp.md).

## Runtime planning

Ask O11y's pinned LLM is the only runtime planner. It selects a compact tool and Agent Skill subset from live schemas and composes capabilities from user intent, authorized metadata, and intermediate evidence. There is no fixed DAG, keyword router, `next_step`, method enum, panel template, or required query→analysis→dashboard sequence.

Metadata discovery may happen before the advisory Analysis Preview. Datasource execution, generated Python, and Grafana mutation wait for confirmation. Pure analysis ends with a chat Result Preview. A requested Grafana visualization must produce a real host-approved Grafana Preview URL; formal publication requires a later explicit confirmation.

## Permission seams

The four external MCP endpoints are independent capabilities:

1. **Data Query Planner** creates bounded plans from authorized metadata and never executes them.
2. **Grafana Query** is the only datasource-read executor for analysis frames and returns opaque authorized refs.
3. **Sandbox Analysis** runs generated Python in a fresh network-denied OpenSandbox Code Interpreter and returns retained opaque outputs. It also supports cross-conversation list, inspect, and revise.
4. **Artifact Bridge** is hidden from the model. Immediately before a built-in Grafana write, it resolves authorized `$plan_ref`, `$execution_ref`, CSV field bindings, and opaque asset URL placeholders. It does not select panels, generate chart JSON, or write Grafana.

Ask O11y's built-in `mcp-grafana_update_dashboard` is the sole Dashboard writer. The dynamically selected Grafana dashboarding Skill advises the same LLM that authors the complete Dashboard JSON; live tool schemas remain authoritative. The host enforces approval, opaque-ref resolution, Preview state, and same-UID publication.

All external MCP services listen on loopback, require a service bearer, and bind that bearer to configured server-side org/user identity. Engineering Analysis, Finance Analysis, `analysis_core`, the external Grafana Renderer, and their fixed method/chart contracts are retired.

## Data and dashboard path

```text
Grafana /api/ds/query
  → authorized query-plan and grafana-frame refs
  → trusted validity filtering
  → isolated generated Python
  → named bounded CSV/image outputs plus schema metadata
  → LLM + selected Grafana Skill author Dashboard JSON
  → hidden Artifact Bridge resolves opaque bindings
  → approved built-in mcp-grafana_update_dashboard
  → real ask-o11y-preview Dashboard
  → explicit confirmation
  → host-normalized patch removes the Preview tag on the same UID
```

Model-visible arguments never contain physical paths, credentials, raw frames, datasource query bodies, full execution payloads, MIME bodies, or signed asset URLs. Native panels receive trusted query/CSV targets from the bridge. Image panels contain an LLM-authored `$asset_url_NAME` placeholder plus an opaque `askO11yAssetBindings` entry; the bridge replaces only the URL. Sandbox's generic signed asset endpoint validates authorization and streams stored bytes without producing HTML, panel JSON, or charts.

Preview state lives only in the host-enforced first `ask-o11y-preview` tag. After a successful Preview write, the host retains its returned UID under the org/user/session identity and disables further tools for that turn. Publication exposes only built-in Dashboard read/update tools, derives the UID from that state, verifies the stored Dashboard still has the Preview tag first, removes that tag, and does not rerun queries, Python, or panel selection. Missing or stale lifecycle state fails closed.

## Isolation, retention, and provenance

The PoC stores authorized artifacts under `.analysis-artifacts/runs/<run_id>/`; paths are never exposed to the model. Retention cleanup removes expired frames, plans, code, executions, provenance, and generated assets.

Sandbox provenance records source hash, input ref, image digest, runtime class, seed, limits, validity filtering, and control-plane config hash. The image includes the pinned tabular/ML stack and Noto CJK fonts. Dependency versions, sources, licenses, and notices are recorded in `docs/third-party-reuse-manifest.json`, `NOTICE`, `docs/sbom.json`, and `uv.lock`.

Production requires a digest-pinned image, authenticated OpenSandbox, and gVisor, Kata, or Firecracker. Plain `runc` remains an explicit local-development opt-in only.
