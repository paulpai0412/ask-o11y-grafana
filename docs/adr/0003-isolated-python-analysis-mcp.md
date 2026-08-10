# ADR 0003: Replace Domain Analysis MCPs with Isolated Python

Status: Accepted

## Context

ADR 0002 introduced deterministic Engineering and Finance Analysis MCPs. Their fixed high-level method contracts cannot satisfy open-ended requests where Ask O11y's LLM must compose new Python analysis and arbitrary notebook visualizations. Adding more method, focus, grouping or panel options would recreate a fixed workflow.

Running generated Python inside Ask O11y or an existing infrastructure MCP would expose its filesystem, environment and credentials.

## Decision

Remove the Engineering and Finance Analysis MCPs and their shared `analysis_core`. Add one `sandbox-analysis` MCP. Ask O11y remains the only LLM planner and generates Python after preview and confirmation.

`execute_python_analysis` accepts one authorized opaque `grafana-frame` reference, generated Python and a seed. It validates ownership, transfers the bounded native Grafana columnar frame plus query-plan validity rules as JSON, builds the filtered DataFrame in a fresh OpenSandbox Code Interpreter, captures bounded rich outputs, persists them behind authorized opaque refs, and always kills the sandbox. List/inspect/revise capabilities recover revisions across Ask O11y conversations without persistent kernels.

The sandbox has deny-all egress, fixed CPU/memory/lifetime/output bounds, no host volumes and no injected credentials. Production requires a digest-pinned image and gVisor or Kata. Plain `runc` is local-development only.

The four runtime seams are Planner, Grafana Query, Sandbox Analysis and Grafana Renderer. They are independently registered capabilities, not a prescribed path: Ask O11y dynamically selects a compact tool subset and chooses query-only, dashboard, Sandbox, or combined calls. Pure analysis returns a chat Result Preview. A request for Grafana output instead produces an approval-gated, expiring Grafana Preview URL after execution; formal publication requires a later explicit confirmation and another Ask O11y host approval.

Renderer remains the approval-gated Grafana writer. It discovers installed panel plugins and can build dynamic native query panels from an authorized plan plus schema-declared field/display specs, or bind named bounded CSV Sandbox outputs to native inline Infinity queries. Its generic artifact path still converts authorized PNG, sanitized HTML, text, CSV or JSON outputs without exposing raw payloads to the model. `prepare_dashboard_write` performs no mutation and binds the exact payload to a short-lived one-time capability. The separately host-approved `create_temporary_dashboard_preview` consumes that capability to write a preview-tagged dashboard and return its URL. After explicit confirmation, `create_dashboard_from_artifacts` promotes the exact payload at the same UID by removing preview status. Sandbox execution alone must not be described as a dashboard.

This ADR supersedes ADR 0002's deterministic Engineering/Finance analysis topology. It preserves ADR 0002's Grafana datasource boundary, opaque artifact authorization, adaptive Ask O11y planning and approval-gated Renderer boundary.

## Consequences

- There is one dynamic analysis MCP instead of parallel deterministic and sandbox paths.
- Fixed method contracts and method-specific regressions are deleted.
- Generated analysis can use a pinned tabular/ML stack including SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn and Optuna, and can emit named rich outputs without method or chart enums.
- OpenSandbox becomes a locked Apache-2.0 runtime dependency and separately operated service.
- Authorized query plans and supported Sandbox outputs can optionally become native or artifact Grafana panels through one generic, approval-gated Renderer; panel choice/order remains LLM-driven from live capabilities.

Detailed contract and acceptance criteria are in `docs/design/sandbox-analysis-mcp.md`.
