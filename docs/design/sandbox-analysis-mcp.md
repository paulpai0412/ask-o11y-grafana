# Sandboxed Python Analysis MCP

Status: implementation design

Branch: `feature/sandbox-analysis-mcp`

Runtime decision: OpenSandbox (`opensandbox==0.1.15`, Apache-2.0)

## Decision

Replace the deterministic Engineering and Finance Analysis MCPs with one `sandbox-analysis` MCP. Do not run both paths in parallel. Remove their endpoint registrations, server code, `analysis_core`, fixed method contracts, and method-specific regressions.

Ask O11y remains the only runtime LLM planner. It dynamically selects and composes registered query, Sandbox and Grafana dashboard tools from the prompt, schemas and intermediate results. Query-only, query-plus-dashboard, query-plus-Sandbox, and query-plus-Sandbox-plus-dashboard are all optional compositions; no sequence is mandatory. No central router, fixed DAG, method enum, target workflow, chart enum, panel template, or hardcoded tool path is introduced.

The runtime exposes four independent trust seams:

1. Data Query Planner: plan only.
2. Grafana Query: sole datasource metadata/read executor; returns an opaque authorized frame ref.
3. Sandbox Analysis: executes generated Python over that frame in isolation; returns opaque Jupyter output artifacts.
4. Grafana Renderer: sole approval-gated Grafana writer. Support for publishing Sandbox outputs is a separate follow-up.

## Why OpenSandbox

OpenSandbox supplies sandbox lifecycle, resource controls, Code Interpreter/Jupyter execution, and MIME result capture. The MCP is only an authenticated client; it does not host a container runtime or execute generated source itself.

Local integration may use Docker `runc`. Production execution of untrusted generated code requires an administrator-configured gVisor or Kata runtime. The analysis image must be pinned by digest and preinstall the approved Python packages because runtime network access is denied.

## Topology

```text
Ask O11y (only LLM planner)
  ├─ Data Query Planner MCP
  ├─ Grafana Query MCP ── authorized frame_ref
  ├─ Sandbox Analysis MCP
  │    └─ OpenSandbox service
  │         └─ fresh Code Interpreter sandbox
  │              ├─ /tmp/input-frame.json
  │              ├─ generated Python
  │              ├─ deny-all egress
  │              └─ bounded Jupyter MIME/text/error output
  └─ Grafana Renderer MCP
```

The Sandbox Analysis endpoint binds `127.0.0.1:8777` by default. It reuses the existing service bearer and server-bound org/user identity. Caller-provided identity is discarded and replaced with authenticated HTTP header context.

## MCP contract

The endpoint exposes four independent capabilities:

- `execute_python_analysis`: start a new revision from an authorized `frame_ref`.
- `list_python_analyses`: rediscover the authenticated user's recent revision refs.
- `inspect_python_analysis`: return prior generated source plus compact fields/output metadata, never frame rows.
- `revise_python_analysis`: execute complete replacement source against a prior revision's authorized frame.

### `execute_python_analysis`

```json
{
  "frame_ref": "artifact://run_…/grafana-frame",
  "python_code": "display(df.describe())",
  "seed": 42
}
```

Contract:

- `frame_ref` must resolve to exactly one `grafana-frame` artifact owned by the authenticated org/user.
- The originating query-plan validity rules travel in the authorized input bundle and are applied by the trusted Sandbox bootstrap before generated code receives `df`. This preserves required `heat_rate_valid` filtering without trusting generated code to remember it.
- `python_code` is required, UTF-8, and limited to 32 KiB. The MCP hashes and transfers it but never interprets or executes it on the host.
- `seed` is an optional unsigned 32-bit integer. Python and NumPy are seeded before user code.
- The sandbox receives `df`, `pd`, `np`, `display(value)`, and `emit(value, name=None)`. Generated code uses those helpers or a final expression for tables, Matplotlib figures, Plotly JSON, HTML, or text; optional output names never control filesystem paths.
- Raw frames, serialized frame bytes, datasource queries, physical paths, credentials, and complete MIME payloads are not echoed in model-visible execution results. `inspect_python_analysis` intentionally returns only previously generated source so Ask O11y can revise it across conversations.

Success returns only opaque refs and compact evidence:

```json
{
  "ok": true,
  "step": "execute_python_analysis",
  "run_id": "run_…",
  "refs": {
    "execution_ref": "artifact://run_…/sandbox-execution",
    "provenance_ref": "artifact://run_…/sandbox-provenance"
  },
  "output_summary": {
    "result_count": 3,
    "mime_types": ["image/png", "text/html", "text/plain"],
    "stdout_lines": 1
  },
  "provenance": {
    "runtime": "opensandbox",
    "code_sha256": "…",
    "input_frame_ref": "artifact://run_…/grafana-frame",
    "seed": 42,
    "network": "deny"
  }
}
```

The execution artifact contains bounded stdout/stderr, structured error information, and Jupyter result items (`text` and MIME-keyed properties). A separate server-side artifact retains source plus hash for audit. Artifact reads remain org/user authorized.

A sandbox error returns `ok=false` with a bounded error summary and opaque diagnostic refs. Ask O11y stops that run; it must not silently generate replacement code or fall back to host execution.

## Data transfer decision

Grafana query responses use Grafana DataFrames encoded as columnar JSON (`schema.fields` plus `data.values`). The Sandbox MCP preserves that representation in a bounded `/tmp/input-frame.json` bundle together with server-authorized validity rules. The trusted in-image bootstrap constructs `df` and applies validity filtering inside the sandbox before generated code runs.

Do not convert the frame to CSV on the MCP host. CSV adds formatting/parsing work and loses type information. Do not create an intermediate SQLite database: Grafana `/api/ds/query` does not return a SQLite file, so the Query or Sandbox MCP would still have to decode every frame value and insert it, while also owning database schema mapping, file lifecycle, locking and authorization. Letting Sandbox open a datasource SQLite file directly would also bypass the Grafana-only query boundary.

If measured frame sizes later make JSON transfer material, adopt Arrow IPC or Parquet as an artifact encoding. That is a measured optimization, not a workflow change; Grafana remains the query executor and Ask O11y still chooses tools dynamically.

## Cross-conversation revision

The original authorized `grafana-frame` artifact, generated Python, execution outputs and provenance are retained server-side for the configured retention period. The ephemeral input file and sandbox are deleted after each call.

`list_python_analyses`, `inspect_python_analysis`, and `revise_python_analysis` let a later Ask O11y conversation rediscover a prior revision, retrieve its generated source and compact metadata without frame rows, then execute replacement code against the same authorized frame. No persistent kernel or Ask O11y source modification is required.

## Isolation policy

Every call creates one fresh sandbox and kills it in `finally`. Cross-turn kernel persistence is deliberately excluded because Ask O11y 0.3.2 does not reliably reconstruct prior tool results.

| Control | Limit |
| --- | ---: |
| sandbox lifetime | 120 seconds |
| CPU | 1 |
| memory | 1 GiB |
| source | 32 KiB |
| injected columnar frame bundle | 16 MiB |
| captured execution JSON | 5 MiB |
| egress | deny all |
| sandbox credentials | none |
| host volumes | none |

Additional rules:

- Production refuses an unpinned image and a runtime class other than gVisor/Kata/Firecracker.
- The MCP validates `SANDBOX_RUNTIME_CLASS` against the OpenSandbox server TOML used by the control plane, requires `dns+nft` plus disabled IPv6, and records that config hash in provenance. `runc` additionally requires explicit local-development opt-in.
- The repository, artifact store, Docker socket, home directory, Grafana credentials, MCP bearer, and process environment are never mounted or injected. Trusted bootstrap unlinks the unfiltered input bundle before generated code runs and rewrites the validity audit afterward.
- MCP HTTP request bodies are bounded before reading; execution exception values remain only in authorized artifacts and are redacted from model-visible errors.
- Do not add regex/import blacklists. Generated Python may invoke subprocesses inside the sandbox; containment belongs to the sandbox boundary.
- Provenance records code hash, input ref, image, runtime class, seed, limits, validity filtering and execution metadata.

## Dynamic tool composition and rendering

No component prescribes a query→analysis→render path. Ask O11y's built-in Grafana MCP remains enabled, so it may dynamically query, return data, or call native dashboard tools when no Sandbox computation is needed. External Planner/Grafana Query remain available when an authorized opaque `frame_ref` is required for Sandbox. Ask O11y selects among these schemas; Sandbox is not a mandatory intermediary.

Sandbox execution is not itself a Grafana write. An image/HTML/Plotly MIME result is not a Grafana dashboard, and Ask O11y must not claim otherwise. The generic Renderer may consume an authorized opaque `sandbox-execution` ref when the user requested Grafana output. Its preview tool binds the exact title and output indexes to a short-lived, one-time capability; its write tool also remains subject to Ask O11y host approval.

Renderer supports bounded Matplotlib/SHAP PNG, allowlist-sanitized HTML, plain text, and JSON as Grafana text panels. It never accepts raw MIME bodies from the model. Plotly JSON remains an analysis artifact unless a compatible Grafana panel is installed; Ask O11y should request Matplotlib PNG when a dashboard-compatible plot is required. This is one optional capability selected from schemas, not a hardcoded orchestrator step or SHAP-specific path.

## Repository migration

Remove:

- `engineering-analysis-mcp/`
- `finance-analysis-mcp/`
- `analysis_core/`
- Engineering/Finance capability entries and environment requirements
- fixed analysis method/E2E scripts that have no Sandbox equivalent

Add or change:

- `sandbox-analysis-mcp/server.py` and its trusted in-image DataFrame/output bootstrap
- the `sandbox-analysis` capability entry
- Ask O11y instructions for generated Python and sandbox failure behavior
- OpenSandbox dependency pins plus the pinned tabular/ML image set: NumPy, SciPy, pandas, Matplotlib, Seaborn, Plotly, scikit-learn, statsmodels, SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn, and Optuna
- lockfile, SBOM, NOTICE, and reuse manifest
- artifact cleanup entries for Sandbox code, execution, and provenance
- focused Sandbox authentication, authorization, limits, MIME, validity, and cleanup checks

Planner, Grafana Query, ArtifactStore authorization, and Renderer approval remain intact.

## Acceptance criteria

1. Runtime capability config contains exactly Planner, Grafana Query, Sandbox Analysis, and Renderer.
2. No Engineering/Finance MCP server, tool registration, `analysis_core`, or active method-specific test remains.
3. Sandbox `tools/list` exposes only `execute_python_analysis`, `list_python_analyses`, `inspect_python_analysis`, and `revise_python_analysis`.
4. Missing/forged identity, foreign ref, raw-frame arguments and oversized code fail before sandbox creation.
5. The authorized Grafana columnar frame and validity rules are injected as bounded JSON; the trusted bootstrap constructs filtered `df`, and raw data is absent from the model-visible result.
6. Captured table/image MIME results are stored behind opaque authorized refs with bounded provenance.
7. Sandbox policy has deny-all egress, fixed CPU/memory/lifetime, empty environment and no volumes; cleanup is unconditional.
8. If OpenSandbox is unavailable, the MCP fails closed and never executes Python locally.
9. Later conversations can list, inspect and revise authorized analyses without persistent sandboxes or raw frame disclosure.
10. The generic Renderer accepts only authorized Sandbox refs, sanitizes/validates supported MIME outputs, and requires both its exact one-time capability and Ask O11y host approval before mutation.
11. Grafana dashboard tools remain optional capabilities selected by Ask O11y, not hardcoded next steps.

## Deferred

- Multiple input frames.
- Pre-warmed pools.
- Cross-turn notebooks.
- Inline Ask O11y Jupyter display.
- Production gVisor/Kata installation and image attestation.
