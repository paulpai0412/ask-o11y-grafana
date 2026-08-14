# ADR 0003: Isolated Python analysis and skill-authored Grafana dashboards

Status: Accepted

## Context

ADR 0002 introduced deterministic Engineering and Finance Analysis MCPs. Their fixed method contracts could not support open-ended analysis without recreating a method router and prescribed workflow. Running generated Python in Ask O11y or an infrastructure MCP would also expose that process's filesystem, environment, and credentials.

The retired external Grafana Renderer had the same problem on the visualization side: accepting visualization specs made it a second chart planner and duplicated Grafana Dashboard knowledge already available through Agent Skills and the built-in Grafana MCP.

The first CSV SHAP slice also showed that physical metadata is insufficient for safe analysis. A column name and type cannot establish whether a field is a target, quality flag, identifier, treatment candidate, post-outcome value, or target proxy; nor can it establish when the value became available. In particular, `raw_coal_consumption_g` may share lineage with `heat_rate`, while the existing planner cannot enforce chronological holdout even when the preview requests it.

## Decision

Remove Engineering Analysis, Finance Analysis, `analysis_core`, and the external Grafana Renderer.

Add one independent, read-only `ontology` MCP and retain the `sandbox-analysis` MCP. Ask O11y remains the only runtime LLM planner and generates Python only after the user confirms an Analysis Preview.

Ontology reads only approved, immutable semantic snapshots. It resolves concepts, returns bounded dataset context and field classifications, and performs side-effect-free advisory validation. It has no datasource credentials, raw-data access, mutation tool, arbitrary graph query, or policy-enforcement authority. The Data Query Planner remains the trusted deterministic pre-query enforcement point: it independently pins and verifies the snapshot/hash, rejects unsafe field roles, availability, lineage, quality, target/feature, and split contracts, and writes those decisions into the immutable query plan.

`execute_python_analysis` accepts an authorized `grafana-frame` ref, complete Python source, and a seed. It transfers bounded native Grafana columnar JSON plus trusted validity rules and the pinned analysis contract into a fresh OpenSandbox Code Interpreter, captures bounded named outputs, persists them behind authorized opaque refs, and destroys the sandbox. List, inspect, and revise recover retained analyses without persistent kernels.

The external runtime endpoints are:

- Ontology: bounded read-only semantic declarations and advisory validation.
- Data Query Planner: plan only and trusted deterministic semantic enforcement.
- Grafana Query: sole datasource executor for analysis frames.
- Sandbox Analysis: isolated artifact-only computation.
- Artifact Bridge: hidden opaque-binding resolver with no Grafana write tool.

Ask O11y's built-in `mcp-grafana_update_dashboard` is the sole Dashboard writer. The same Ask O11y LLM dynamically selects the embedded Grafana dashboarding Skill and authors complete Dashboard JSON. The bridge resolves query bindings for dashboards that did not use Sandbox, or PNG asset bindings for analysis dashboards; it never selects panels, layouts, methods, or chart options.

A requested Grafana visualization creates a real host-approved Dashboard tagged `ask-o11y-preview`. The host binds the returned UID to the org/user/session and disables further tools for that turn. After confirmation, it exposes only built-in Dashboard read/update capabilities, derives and verifies that reviewed UID/tag, then removes the Preview tag. Missing or mismatched lifecycle state fails closed; query execution, Python, and panel selection are not repeated.

Image outputs use opaque panel asset bindings. The bridge creates a signed URL from the authorized execution ref and output index. Sandbox's generic asset endpoint validates the token and streams the stored MIME bytes unchanged. Neither service generates HTML or panel JSON.

The sandbox has deny-all egress, fixed CPU/memory/lifetime/input/output bounds, no host volumes, and no credentials. Production requires an authenticated OpenSandbox control plane, a digest-pinned image, and gVisor, Kata, or Firecracker. `runc` is local-development only.

Before confirmation, Ontology and Planner may read declarations and produce a preview, but Grafana Query, Python, and Dashboard writes are forbidden. Confirmation binds the exact ontology snapshot, feature set, cutoff, split policy, assumptions, and plan hash. Any change creates a new plan and requires confirmation again. Unknown or unapproved semantics, snapshot/hash mismatch, and contract drift fail closed without an LLM guess or alternate executor.

This ADR supersedes ADR 0002's deterministic analysis topology and the earlier Renderer portion of this ADR. It retains Grafana's datasource boundary, opaque artifact authorization, adaptive LLM planning, and host-approved mutation.

## Consequences

- One dynamic analysis MCP replaces deterministic domain method APIs.
- The runtime has five external endpoints; Ontology is model-visible and read-only, while Artifact Bridge remains hidden.
- Semantic decisions are Git-reviewed and released as immutable snapshots; observed/proposed evidence never silently becomes approved truth.
- Ontology explains declarations, but Planner code makes the final allow/reject decision before query execution.
- One LLM owns both analysis planning and Dashboard authoring; Skills supply advisory Grafana knowledge.
- Live Grafana tool schemas outrank embedded Skill documentation.
- The hidden bridge remains a narrow trust boundary rather than a visualization service.
- Analysis dashboards are static image reports: Sandbox PNG assets are delivered without model-visible bytes or signed URLs, and may not become native Grafana data targets.
- Preview/publication lifecycle is host-enforced safety state, not a general workflow DAG.
- The pinned analysis image can provide NumPy, SciPy, pandas, Matplotlib, Seaborn, Plotly, scikit-learn, statsmodels, SHAP, CPU XGBoost, LightGBM, imbalanced-learn, Optuna, and CJK fonts without runtime installation.

Detailed contracts and acceptance criteria are in [the sandbox design](../design/sandbox-analysis-mcp.md) and [the ontology-assisted analysis design](../design/ontology-assisted-analysis.md).
