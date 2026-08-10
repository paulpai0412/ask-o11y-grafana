# ADR 0003: Isolated Python analysis and skill-authored Grafana dashboards

Status: Accepted

## Context

ADR 0002 introduced deterministic Engineering and Finance Analysis MCPs. Their fixed method contracts could not support open-ended analysis without recreating a method router and prescribed workflow. Running generated Python in Ask O11y or an infrastructure MCP would also expose that process's filesystem, environment, and credentials.

The retired external Grafana Renderer had the same problem on the visualization side: accepting visualization specs made it a second chart planner and duplicated Grafana Dashboard knowledge already available through Agent Skills and the built-in Grafana MCP.

## Decision

Remove Engineering Analysis, Finance Analysis, `analysis_core`, and the external Grafana Renderer.

Add one `sandbox-analysis` MCP. Ask O11y remains the only runtime LLM planner and generates Python only after the user confirms an Analysis Preview. `execute_python_analysis` accepts an authorized `grafana-frame` ref, complete Python source, and a seed. It transfers bounded native Grafana columnar JSON plus trusted validity rules into a fresh OpenSandbox Code Interpreter, captures bounded named outputs, persists them behind authorized opaque refs, and destroys the sandbox. List, inspect, and revise recover retained analyses without persistent kernels.

The external runtime endpoints are:

- Data Query Planner: plan only.
- Grafana Query: sole datasource executor for analysis frames.
- Sandbox Analysis: isolated artifact-only computation.
- Artifact Bridge: hidden opaque-binding resolver with no Grafana write tool.

Ask O11y's built-in `mcp-grafana_update_dashboard` is the sole Dashboard writer. The same Ask O11y LLM dynamically selects the embedded Grafana dashboarding Skill and authors complete Dashboard JSON. The bridge resolves only authorized query, CSV, and asset bindings immediately before dispatch; it never selects panels, layouts, methods, or chart options.

A requested Grafana visualization creates a real host-approved Dashboard tagged `ask-o11y-preview`. The host binds the returned UID to the org/user/session and disables further tools for that turn. After confirmation, it exposes only built-in Dashboard read/update capabilities, derives and verifies that reviewed UID/tag, then removes the Preview tag. Missing or mismatched lifecycle state fails closed; query execution, Python, and panel selection are not repeated.

Image outputs use opaque panel asset bindings. The bridge creates a signed URL from the authorized execution ref and output index. Sandbox's generic asset endpoint validates the token and streams the stored MIME bytes unchanged. Neither service generates HTML or panel JSON.

The sandbox has deny-all egress, fixed CPU/memory/lifetime/input/output bounds, no host volumes, and no credentials. Production requires an authenticated OpenSandbox control plane, a digest-pinned image, and gVisor, Kata, or Firecracker. `runc` is local-development only.

This ADR supersedes ADR 0002's deterministic analysis topology and the earlier Renderer portion of this ADR. It retains Grafana's datasource boundary, opaque artifact authorization, adaptive LLM planning, and host-approved mutation.

## Consequences

- One dynamic analysis MCP replaces deterministic domain method APIs.
- One LLM owns both analysis planning and Dashboard authoring; Skills supply advisory Grafana knowledge.
- Live Grafana tool schemas outrank embedded Skill documentation.
- The hidden bridge remains a narrow trust boundary rather than a visualization service.
- Named `.csv` outputs provide bounded field metadata for native Grafana targets; PNG and other supported assets are delivered without model-visible bytes or signed URLs.
- Preview/publication lifecycle is host-enforced safety state, not a general workflow DAG.
- The pinned analysis image can provide NumPy, SciPy, pandas, Matplotlib, Seaborn, Plotly, scikit-learn, statsmodels, SHAP, CPU XGBoost, LightGBM, imbalanced-learn, Optuna, and CJK fonts without runtime installation.

Detailed contracts and acceptance criteria are in [the implementation design](../design/sandbox-analysis-mcp.md).
