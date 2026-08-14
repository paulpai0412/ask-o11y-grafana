# WFERP Ontology import verification

Date: 2026-08-13

## Reproduce

The executable verification uses generated SQL Server FK catalog/relation evidence under `.scratch/poc/` plus the explicitly reviewed fixture manifest `semantic/approvals/wferp-test-relations.yaml`. The manifest is data consumed by the generic compiler; no relation name, table, or column is embedded in compiler or validator code. Production promotion must supply its own reviewed manifest.

```bash
uv run python scripts/import-csv-ontology.py --csv data/poc/u1_operating_daily.csv --dataset-id u1-operating-daily-observed --namespace analysis.u1.observed --output .scratch/poc/u1-operating-daily-candidate.json --check
uv run python scripts/import-wferp-ontology.py --check
uv run python scripts/import-sqlserver-relations.py --catalog <sqlserver-catalog.json> --owner <owner> --effective-from <date> --output <relation-evidence.json> --check
uv run python scripts/merge-ontology-relation-evidence.py --candidate semantic/candidates/wferp.json --relations <relation-evidence.json> --output <merged-candidate.json> --check
uv run python scripts/promote-ontology-relations.py --candidate <merged-candidate.json> --approvals semantic/approvals/wferp-test-relations.yaml --snapshot-id <id> --output <snapshot.json> --check
uv run python scripts/verify-wferp-ontology.py
uv run python ontology-mcp/server.py --self-check
uv run python data-query-planner-mcp/server.py --self-check
```

## Verified result

- Generic CSV importer: U1 produced 21 observed fields and zero approved candidates without an Ontology Core code change
- Candidate IR: `semantic/candidates/wferp.json`
- Immutable discovery snapshot: `semantic/snapshots/wferp-v0.1.0.json`
- Snapshot SHA-256 after generic evidence merge and reviewed verification-scope relation promotion: `f8ef905cd553d868781638aa3ba96fab3cd5e112d83522dc7f9869c15bfef2a5`
- Imported tables: 1,369
- Imported fields: 32,022
- Imported heuristic relationships: 1,178 proposed/non-executable
- Separately reviewed SQL Server FK relations: 2 approved/executable in the isolated verification scope
- Automatically approved candidates: 0
- Candidate IR and snapshot repeated builds: byte-identical
- Generic SQLGlot ontology validator (`ontology_sql_validator.py`): no WFERP/table/column hardcode
- SQLGlot T-SQL single-table check: accepted
- `ACPTA` → `ACPTB` heuristic JOIN: rejected with `JOIN_RELATION_NOT_APPROVED`
- Live five-endpoint orchestration guardrail suite: passed
- Python LSP and pi-lens diagnostics: zero findings

The snapshot is `approved` as an immutable read-only artifact. Its datasets and fields remain `observed`; the original 1,178 heuristic relationships remain `proposed` and `executable: false`. Only two exact checked-FK relations were separately reviewed and promoted for the isolated executable verification scope.

## JOIN and complex SQL execution

```bash
uv run python scripts/verify-wferp-complex-sql.py
```

The fixture file, not Python code, supplies SQL and expected results:

`data-query-planner-mcp/metadata/wferp/complex-sql-fixtures.json`

Verified through Data Query Planner and real Grafana `/api/ds/query` against the SQL Server test datasource:

- two-table JOIN: accepted and returned one expected row;
- three-table JOIN + correlated subquery + `CASE` + `SUM` + `GROUP BY` + `HAVING`: accepted;
- complex result: `BGT001`, `工程預算`, `2026`, `HIGH`, `700000`, `187000`, `513000`;
- wrong approved-relation predicate: `JOIN_PREDICATE_MISMATCH`, Grafana calls `0`;
- heuristic-only relation: `JOIN_RELATION_NOT_APPROVED`, Grafana calls `0`.

## Bounded MCP verification

```text
list_snapshots(dataset_id="wferp")
get_relation_paths(dataset_ids=["ACTMK"], max_hops=2)
get_semantic_context(dataset_id="ACPTA", fields=["TA001", "TA002", "TA003"])
```

The first returns a bounded manifest (`dataset_count=1370`, IDs truncated). The second expands `ACTMK → ACTMJ → ACTMI` through approved ontology relations and returns exact key mappings/cardinality. The third resolves physical fields without returning datasource rows. Catalog resolution also accepts any indexed physical WFERP table ID.

## U1 regression evidence

Planner self-check retained the U1 snapshot hash:

```text
81304bc7daf0b6c87711c76a3cd3ac45f162dd6ffd0e91c5997a88d89484aa01
```

Target-as-feature, unknown feature, target proxy, and random split still fail before query. A real Ask O11y run also produced:

- Grafana frame: `.analysis-artifacts/runs/run_858fcc6957024fc79defa094aad07e3c/`
- Sandbox SHAP execution: `.analysis-artifacts/runs/run_a068ba34ede042b4bf7bc749e6624062/`
- 365 input rows, 172 valid rows, 193 excluded rows
- pinned ontology, plan, frame, and code provenance
- `image/png` SHAP output

The outer E2E script reached successful query, Sandbox SHAP, Preview, publication, and XY flow, but exited non-zero because it asserted that the model-visible Planner result text itself contained the ontology hash. The persisted trusted plan/sandbox provenance contains that hash. This is a test-observation mismatch, not a datasource or SHAP execution failure; it remains reported rather than weakened with a fallback.
