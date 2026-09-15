# SalesDatamart Ask O11y preview repair

## Scope and diagnosis

The reported `WFERP dataset is not authorized` is not a Grafana or SQL password failure. The DataFlow SalesDatamart datasource health check and existing dashboard queries succeed. The running Grafana Query MCP advertises `search_wferp_schema`, and Ask O11y's saved tool selections still enable it, although source commit `9c4dffb` removed that integration. Current source also lacks an MSSQL dataset path; removing a stale tool alone cannot restore SalesDatamart querying.

The earlier screenshot of `dataflow-sales-v1` was an existing dashboard, not a newly generated preview. Ask O11y's preview is a saved, separately identified Grafana resource; `get_panel_image` is not its creation mechanism. The earlier operator-added “do not save” prompt conflicted with that mechanism.

The user approved:

- generic read-only MSSQL support within the existing Grafana Query MCP, currently authorizing only DataFlow SalesDatamart;
- local service/settings application, without changing credentials, SQL grants, restoring WFERP or introducing a new MCP;
- a new Product Category preview in the existing DataFlow Discovery folder, inheriting its permissions, without overwriting the existing dashboard or enabling public sharing.

## Source changes

- `grafana-query-mcp/mssql.py`: live schema query and a bounded single-SELECT path using the installed sqlglot T-SQL parser. Tables/views must be schema-qualified and observed in live authorized metadata. Writes, external/database references, user functions and unbounded TOP modifiers are rejected. An overflow sentinel row avoids silently reporting a truncated complete frame. The datasource's read-only SQL login remains the database enforcement boundary.
- `grafana-query-mcp/server.py`: reuse discover/inspect/query tools and Grafana `/api/ds/query`; verify configured database and organization; expose live fields and a bounded result preview alongside retained frames. Restore the uploaded CSV branch's `elif` lost during WFERP removal so restarting current code does not reject uploads.
- `config/authorized-grafana-datasets.json`: register only org 1 `dataflow-salesdatamart`, database `SalesDatamart`, schema `reporting`. Live metadata exposes the reporting view; no source tables or database credentials are copied here.

There is no product/category-specific query, fixed chart layout, new orchestration layer or direct SQL connection. Ask O11y must author its SQL and dashboard using returned metadata and results.

## Verification status

Local regression command: `.venv/bin/python grafana-query-mcp/test_mssql.py -v`.
Tests cover aggregation/CTEs, schema discovery, Grafana query routing, rejected effects/external references, organization/database authorization, row overflow and upload preservation. These are offline unit/integration-seam checks, not UI/LLM acceptance.

## Verified result — 2026-09-15

- All 13 offline test methods pass, including after applying the complete patch (new files included) to a clean HEAD reconstruction. Scoped Python LSP reports no errors; `py_compile` and `git diff --check` pass.
- Independent source review has no remaining blocker. Initial concerns about explicit `TOP N` and a tracked-only diff were resolved: explicit TOP is intentional SQL result scope, not system truncation; the complete patch includes new files and passes clean application. Review runs: `6ae847c2-f962-408f-a64a-0d9b2b6d31d5`, clarification `6f504278-6bab-4f80-b05c-c159d4b621d5`.
- A real Grafana-executed permission query confirms the reporting SQL user has SELECT on the view and none of the enumerated INSERT/UPDATE/DELETE/ALTER, database CONTROL/CREATE TABLE/EXECUTE, sysadmin, db_owner or db_datawriter permissions. No write probe or permission change was performed.
- Restarted only `grafana-mcp@grafana-query-mcp.service`; new PID `385617`, start `2026-09-15 09:30:34 CST`. Removed only `search_wferp_schema` and `grafana-query_search_wferp_schema` from saved selections. Kept other settings, credentials, prompts and approval policy unchanged. The live tool catalog now contains discover/inspect/query and no WFERP tools.
- Fresh Ask O11y UI request completed seven tool calls without tool errors: dataset discovery, folder/dashboard discovery, live schema inspection, model-authored category SQL, one approval-gated dashboard creation, and a deeplink. No hardcoded SQL or dashboard JSON was submitted by the operator.
- Native creation receipt: UID `ask-o11y-preview-cat-sales-a7f3`, version 1, folder `dataflow-discovery`, created `2026-09-15T01:34:22Z`; tags include `ask-o11y-preview` and `ask-o11y-report`. The original `dataflow-sales-v1` UID/version/title are unchanged. No dashboard-specific ACL entries were added; folder Viewer/View and Editor/Edit permissions remain in effect. Public-dashboard lookup returns `publicdashboards.notFound` (404).
- Browser verification covers the actual Ask O11y inline iframe, all six data panels on the new dashboard, and successful restoration of the same chat/preview after reload. Category totals match the model's Grafana query result (Bikes 62.137M, Components 8.211M, Clothing 1.332M, Accessories 0.739M); verified date coverage is 2011-05-31 through 2014-06-30. No browser errors were reported. The earlier image-renderer HTTP 500 was not repaired or used as preview evidence.

Ask O11y session: `jPGSJ-hC5PMefJEVC3_eNEzG5aybZWWDb7UFBpX7p1o`.
Agent run: `ifcnycdkK5tQj6K2Vu_HqsTbOgieNd56nEVRLLPzf7k`, terminal `completed`.

Evidence: `.scratch/salesdart-preview-repair/` (ignored), including `live-run-final.json`, `new-dashboard.json`, `resource-readback.json`, `datasource-permissions.json`, `live-tools-after.json`, `clean-apply.log`, `ask-o11y-preview.png`, and `new-dashboard-detail.png`.

Operational rollback must not silently restore the retired WFERP runtime. If this MSSQL path needs withdrawal, remove its new catalog entry and restart only the query service; preserve retained results and the new dashboard until separately authorized to remove them. No commits or pushes were made.

## Retrospective

The causal fix was completing the missing datasource read path and updating the stale running service, not changing passwords or suppressing one error. A visible existing dashboard proves connectivity but does not prove new-preview delivery; the native new-UID receipt plus the restored Ask O11y iframe now supplies that missing evidence.
