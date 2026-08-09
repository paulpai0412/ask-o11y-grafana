# Sandboxed Python Analysis MCP

Status: proposed for implementation on `feature/sandbox-analysis-mcp`  
Runtime decision: OpenSandbox (`opensandbox==0.1.15`, Apache-2.0)  
Research: `.scratch/research/github-sandboxed-notebook-runtime.md`

## Decision

Add a sixth, separately authenticated `sandbox-analysis` MCP endpoint. Keep the existing deterministic Engineering and Finance Analysis MCPs unchanged and enabled. Ask O11y remains the only runtime planner and chooses either deterministic domain tools or sandboxed Python from the registered schemas and the confirmed user intent; no router or fixed analysis DAG is added.

The new endpoint executes LLM-generated Python in a fresh OpenSandbox Code Interpreter sandbox. It receives only an authorized opaque Grafana `frame_ref` plus Python source. It has no Grafana client, datasource credentials, renderer credentials, host shell, host filesystem mount, agent, skill runtime, or nested LLM.

This supersedes ADR 0002 only where ADR 0002 rejects all arbitrary-code tools. It does not change the existing datasource, artifact-authorization, deterministic-analysis, or approval-gated Grafana write boundaries.

## Why OpenSandbox

OpenSandbox already provides the lifecycle, resource controls, Code Interpreter/Jupyter execution contexts, streamed execution results, and MIME result capture that this feature would otherwise have to build. The local PoC may use Docker `runc` only for integration testing. Production execution of untrusted model-generated code requires an administrator-configured gVisor or Kata runtime; OpenSandbox validates that runtime at server startup.

No OpenSandbox server or privileged container runs inside the MCP process. The MCP is a narrow authenticated client of a separately operated OpenSandbox service.

## Topology

```text
Ask O11y (only LLM planner)
  ├─ Data Query Planner MCP          plan only
  ├─ Grafana Query MCP               sole datasource reader → opaque frame_ref
  ├─ Engineering Analysis MCP       existing deterministic path
  ├─ Finance Analysis MCP           existing deterministic path
  ├─ Sandbox Analysis MCP           dynamic Python over authorized frame_ref
  └─ Grafana Renderer MCP           sole approval-gated Grafana writer

Sandbox Analysis MCP
  ├─ ArtifactStore                   verifies org/user ownership
  └─ OpenSandbox service
       └─ ephemeral Code Interpreter sandbox
            ├─ /tmp/input.csv        only authorized frame data
            ├─ generated Python      no injected secrets
            ├─ deny-all egress
            └─ bounded Jupyter MIME/text/error results
```

The endpoint binds `127.0.0.1:8777` by default and reuses the existing high-entropy bearer plus server-bound org/user authentication. Caller-supplied `context` is removed and replaced by authenticated HTTP header context exactly as in the existing MCPs.

## MCP contract

One tool is sufficient for the first vertical slice:

### `execute_python_analysis`

Input:

```json
{
  "frame_ref": "artifact://run_…/grafana-frame",
  "python_code": "…",
  "seed": 42
}
```

Rules:

- `frame_ref` must resolve to exactly one authorized `grafana-frame` artifact owned by the authenticated org/user.
- `python_code` is required, UTF-8, and at most 32 KiB. It is never interpreted or executed by the MCP host.
- `seed` is an optional integer. The wrapper seeds Python and NumPy before user code.
- The sandbox receives `df`, `pd`, and `np`; `df` is loaded from the injected CSV.
- The code should use Jupyter `display()` or a final expression for tables and plots. There is no method enum, chart enum, target schema, panel template, or fixed sequence.
- Raw frames, CSV bytes, physical paths, sandbox API credentials, and complete MIME payloads are never returned in model-visible tool text.

Successful model-visible response:

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
    "result_count": 4,
    "mime_types": ["image/png", "text/html", "text/plain"],
    "stdout_lines": 2
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

The full execution artifact contains bounded stdout/stderr, structured error data, and Jupyter result items (`text` plus MIME-keyed properties). The source is stored separately as a server-side provenance artifact. Only opaque refs, hashes, counts, MIME names, and bounded error summaries enter model context.

Execution failure returns `ok=false`, one bounded error summary, and no claim that analysis or visualization exists. Ask O11y must stop that run rather than trying alternate generated code automatically.

## Sandbox policy

Every call creates one fresh sandbox and kills it in `finally`; cross-turn kernel persistence is deliberately excluded because Ask O11y 0.3.2 does not reliably reconstruct prior tool results.

Required limits:

| Control | PoC default | Hard ceiling |
| --- | ---: | ---: |
| sandbox lifetime | 60 s | 120 s |
| CPU | 1 | 1 |
| memory | 1 GiB | 1 GiB |
| Python source | 32 KiB | 32 KiB |
| injected CSV | 16 MiB | 16 MiB |
| captured execution JSON | 5 MiB | 5 MiB |
| egress | deny all | deny all |
| credentials injected | none | none |
| host volumes | none | none |

The image is operator-configured and must be pinned by digest outside local development. It preinstalls Python, pandas, NumPy, scikit-learn, statsmodels, Matplotlib, Seaborn and Plotly. Runtime package installation is impossible because egress is denied.

Do not add regex/import blacklists. Generated Python can invoke subprocesses inside the sandbox; the isolation boundary, not source inspection, must contain it.

Production admission checks:

- Refuse startup unless the configured OpenSandbox server is trusted and the image is digest-pinned.
- Require OpenSandbox to use gVisor or Kata. `runc` is explicitly development-only.
- Do not mount the repository, Docker socket, artifact root, home directory, or cloud credentials.
- Do not pass the MCP process environment into the sandbox.
- Preserve code hash, image reference, input ref, seed, limits, runtime and execution duration in provenance.

## Output and rendering seam

This first slice proves isolated dynamic computation and arbitrary Jupyter output capture. It does **not** teach the existing Renderer to interpret notebook MIME and does not weaken Renderer approval.

A later, separate publisher change may accept `sandbox-execution` and convert validated table/Plotly/image artifacts into Grafana panels. That write must still use Renderer-issued, short-lived, one-time `approval_ref` before any CSV/file/Grafana side effect. Until then, Ask O11y may report sandbox analysis evidence but must not claim a Grafana dashboard exists.

## Changes in the implementation branch

- Add `sandbox-analysis-mcp/server.py` and a focused self-check.
- Add the sixth capability entry to `config/adaptive-mcp-capabilities.json`.
- Generalize Ask O11y capability configuration validation from the fixed five-ID assertion to the configured six trust seams, while keeping config-driven tool registration.
- Amend the system prompt so sandbox Python is optional after preview/confirmation and existing deterministic tools remain available.
- Add OpenSandbox SDK pins and license/provenance records.
- Add artifact retention names for sandbox code, execution and provenance.
- Add a negative-contract check covering forged artifact context, raw-frame injection, oversized code/output, deny-all network policy, resource bounds and sandbox cleanup.

No existing Engineering/Finance Analysis tool or `analysis_core` API is removed or redirected.

## Acceptance criteria

1. Existing Engineering and Finance MCP self-checks still pass unchanged.
2. `tools/list` exposes only `execute_python_analysis` on the new MCP.
3. Missing/forged identity, foreign `frame_ref`, raw frame arguments and code over 32 KiB fail before sandbox creation.
4. A fake OpenSandbox execution proves authorized CSV injection, deterministic seed wrapper, MIME capture, bounded opaque artifacts and unconditional cleanup.
5. A real local-development spike emits at least one table and one plot from an authorized frame when OpenSandbox is available; absence of the external service is reported as unavailable, never replaced with host execution.
6. The sandbox receives no datasource/Grafana/MCP credentials, no host volumes and deny-all egress.
7. Capability configuration remains schema/config driven; no orchestrator method or chart routing is added.
8. Existing Renderer still requires its server-issued one-time approval capability for all Grafana writes.

## Deferred

- Notebook MIME → generic Grafana publisher.
- Cross-turn kernel persistence.
- Multiple input frames.
- Pre-warmed sandbox pools.
- Inline Ask O11y Jupyter rendering.
- Production installation of gVisor/Kata and image supply-chain attestation.

These are added only after the isolated execution vertical is measured and accepted.
