# Adaptive Ask O11y ML MCP Topology

Accepted.

Ask O11y is the only runtime LLM planner. It previews an advisory plan, waits for confirmation, then chooses high-level tools from registered schemas and may revise the plan from intermediate results. The orchestrator contains no fixed DAG, domain keyword router, target, method, or panel sequence.

Five MCP endpoints follow deployment and trust seams:

1. Query Planner plans and validates; it never executes a datasource query.
2. Grafana Query is the only datasource read/query executor and writes bounded opaque QueryArtifacts.
3. Engineering Analysis consumes artifacts and performs deterministic engineering analysis.
4. Finance Analysis consumes artifacts and performs deterministic finance analysis; live Finance E2E is deferred in this phase.
5. Grafana Renderer consumes AnalysisResult/VisualizationSpec artifacts and is an approval-gated write boundary.

Only Engineering and Finance are analysis MCPs. They contain no LLM, agent, skill runtime, shell, arbitrary code execution, datasource client, or credentials. Skills are development references only.

## Local service trust boundary

The five PoC MCPs bind loopback and require the same high-entropy bearer service token. Grafana stores the token and service org/user headers in encrypted plugin secure settings; each server also binds that token to its configured service org/user and rejects even a valid bearer carrying different identity headers. Callers cannot select identity through tool arguments or headers, and the servers do not use process-global analysis context as request identity. Artifacts remain bound to the authenticated org/user plus run ID.

Dashboard mutation has two independent gates. After the user confirms a preview containing a dashboard, Ask O11y's host approval UI pauses Renderer calls before dispatch. Separately, Renderer `prepare_dashboard_write` validates the authorized AnalysisResult and issues a random opaque `approval_ref` stored server-side with authenticated org/user ownership, the exact analysis ref, a 600-second expiry, and pending status. `create_dashboard_from_analysis` verifies the same context/run/analysis binding and atomically consumes the capability before any chart CSV or Grafana write; missing, forged, mismatched, expired, or replayed refs fail with no write side effect. The caller-controlled `approval_confirmed` boolean was removed from the schema and implementation. Production multi-tenant identity remains outside this PoC scope, but the implemented server-side capability requires no Ask O11y fork.

Planner query artifacts carry a metadata-derived bounded time range and explicit maximum rows, fields, and response bytes. Grafana Query accepts no caller time override, caps the HTTP body read, validates all bounds, and persists neither the raw response nor frame after a bound failure. Metadata-declared validity companions are retained in the plan without becoming model features; Engineering applies those domain validity rules before analysis/evaluation and records input, retained, and excluded row counts. Numeric Grafana timestamps are parsed explicitly as Unix seconds or milliseconds rather than relying on pandas' nanosecond default.

## Shared analysis seam

`analysis_core` is an in-process Python library, not an MCP or service. Do not design it upfront as a universal framework. First implement the Engineering correlation vertical and the Finance contract. Extract only pure mechanics proven identical at both call sites: frame validation, deterministic pandas/scikit-learn/statsmodels computation, evaluation, generic visualization specs, and provenance. Engineering units/equipment rules and Finance fiscal/currency/accounting rules stay in their domain MCPs. MCP transport, artifact IO, authorization, Grafana access, and flow decisions stay outside `analysis_core`.

## GitHub reuse decision

Prefer released dependencies over vendored servers and evaluate the official MCP Python SDK before keeping local transport code. Preserve upstream URL, immutable release/commit, license, lock/SBOM, and local patch metadata. Do not run K-Dense skills in production. Reject OpenBB/direct database MCPs, AGPL/unclear-license code, and arbitrary-code tools.

The official MCP Python SDK v2.0.0 was installed and inspected in a real compatibility spike, then removed. It supports Streamable HTTP, but preserving the existing explicit schemas and bearer-bound Grafana org/user identity still requires a low-level ASGI middleware adapter, while the dependency adds 28 transitive packages for OAuth/JWT, SSE, ASGI, HTTP clients, cryptography, Pydantic, and telemetry that this local PoC does not use. The current stdlib surface is limited to initialize/ping/tools-list/tools-call and has live protocol, strict-schema, and valid-token-forged-identity regressions. The SDK is therefore `evaluated-not-adopted-runtime`, not an unexamined candidate; revisit it when OAuth, SSE/resumption, multi-process ASGI deployment, or a broader MCP method surface enters scope. Evidence: `.scratch/poc/mcp-python-sdk-compatibility-spike.json`.

`grafana/mcp-grafana` v1.0.0 was tested against the real local Grafana. It discovers the Infinity datasource, but its panel query cannot extract the Infinity target, it returns model-visible JSON rather than opaque QueryArtifacts, and write mode exposes datasource plus dashboard mutation without artifact-aware approval. It therefore does not replace the current Query/Renderer seams. See `.scratch/poc/mcp-grafana-compatibility-spike.json`.

## Rejected options

- Fixed `next_step` chain or keyword router: rejected because distinct intents collapse to one result. The superseded Scientific Method, Thermal Power, mixed Grafana PoC, and standalone WFERP MCP server entrypoints and fixed-chain runners/design documents were deleted rather than merely unregistered; `workflow_node.py` no longer supports emitting or validating `next_step`.
- Central workflow DAG executor: rejected because Ask O11y must adapt from registered capabilities and intermediate results.
- One MCP per algorithm: rejected because methods are internal deterministic libraries behind two domain interfaces.
- One combined Grafana read/write MCP: rejected because datasource read and dashboard write have different credentials and approval requirements.
- A universal shared ML framework built first: rejected because a seam without two real callers is speculative.
