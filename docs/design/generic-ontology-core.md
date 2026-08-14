# Generic Ontology Core

Status: implemented vertical slice

## Purpose

The Ontology Core publishes small, immutable, read-only semantic snapshots for heterogeneous datasets. It models declarations about physical assets, fields, keys, relations, metrics, and events; it neither stores rows nor executes queries or actions.

This design deliberately borrows only the declarative part of Palantir-style ontology modeling:

| Generic core | Similar Palantir concept | Current limit |
| --- | --- | --- |
| dataset / physical asset | object type backing source | no object instances |
| field | property | no access policy |
| relation | link type | proposed relations cannot authorize joins |
| metric/event semantic kind | metric/event modeling | declarations only |
| — | action type | not implemented |

## Explicit non-goals

- No access policy, property-level authorization, row/cell security, or caller-specific projection in this phase.
- No `hidden` field behavior. If visibility metadata is added later, it is display/discovery metadata and is not a security control.
- No action definitions, submission criteria, action execution, mutation, or writeback.
- No raw rows, datasource credentials, arbitrary SQL/SPARQL/Cypher, graph dump, or unbounded traversal.
- No automatic promotion from observed/proposed evidence to approved business meaning.

## Deep modules and seams

```text
Git registry YAML
  → snapshot compiler
  → canonical immutable snapshot + SHA-256
  → catalog resolver
  → bounded Ontology MCP projection
  → consumer-specific Planner validator
  → Grafana Query (sole datasource executor)
```

### Snapshot compiler

Interface:

```text
build-ontology-snapshot.py --registry <registry.yaml> [--output <snapshot.json>] --check
```

The compiler selects the legacy U1 ML schema or the generic schema based on registry shape, validates references and relation executability, emits canonical JSON, and proves byte-identical repeated builds. A relation may be executable only when its status is `approved`.

### Catalog resolver

`semantic/catalog.json` maps namespace/dataset/snapshot identity to an immutable path and SHA-256. The WFERP entry indexes all physical table IDs plus the aggregate `wferp` dataset alias. `ontology_contract.load_snapshot()` verifies:

1. one unambiguous catalog entry;
2. path remains under `semantic/snapshots`;
3. snapshot canonical hash;
4. catalog hash and snapshot ID agreement;
5. registry approval state.

The default entry exists only for backward-compatible U1 callers. Dataset-aware callers resolve by `dataset_id`; no Ontology MCP tool is hardcoded to U1.

### Bounded Ontology MCP

Tools:

1. `list_snapshots(namespace?, dataset_id?)`
2. `get_relation_paths(dataset_ids, max_hops<=3, limit<=50, include_proposed=false, snapshot_ref?)`
3. `resolve_concepts(terms, namespace?, snapshot_ref?)`
4. `get_semantic_context(dataset_id, intent, fields?, snapshot_ref?)`
5. `classify_fields(dataset_id, fields, snapshot_ref?)`
6. `validate_analysis_contract(contract, snapshot_ref?)`

The sixth tool remains the U1 ML advisory validator for backward compatibility. It is a consumer policy adapter, not a generic ontology capability; non-ML snapshots fail closed. Its implementation remains temporarily colocated in `ontology_contract.py` to preserve the verified U1 vertical slice, while Planner independently reloads the dataset-selected snapshot and remains the final gate. Extraction into a Planner ML validator is deferred until a second executable analysis policy exists.

## Candidate import and promotion

`scripts/import-csv-ontology.py` reads any UTF-8 CSV header and at most 1,000 rows for primitive physical type inference, then emits observed-only Candidate IR without keys, units, roles, metrics, relations, or approval. `scripts/import-wferp-ontology.py` reads the existing offline WFERP schema bundle, primary-key map, and heuristic relationship edges. It never contacts the datasource and emits `semantic/candidates/wferp.json` under `candidate-ir.schema.json`.

Candidate IR schemas permit only `observed` or `proposed`; relationship candidates are structurally fixed to `status: proposed` and `executable: false`. `scripts/promote-ontology-relations.py` accepts any Candidate IR plus an explicit reviewed approval manifest, rejects conflicting or unknown endpoints, validates the resulting registry, then emits an immutable catalogued snapshot. Snapshot-level `approved` means the artifact is approved for read-only discovery, not that imported fields or joins are authorized.

Candidate discovery starts with datasource metadata lexical seeds, then calls the generic `ontology_graph.expand_datasets(snapshot, seeds, max_hops, limit)`. By default only approved executable relations participate. Expansion adds adjacent/multi-hop datasets before remaining lexical candidates and returns exact relation paths; proposed edges are kept in a separate non-authorizing list. There are no domain keyword/table boosts in graph expansion.

SQL relation validation is implemented in the datasource-neutral `ontology_sql_validator.py`. It accepts SQL, a pinned snapshot, and a SQLGlot dialect; it knows no WFERP table or column names. The WFERP adapter supplies `tsql` and performs its existing SQL Server 2000 policy checks. Each AST scope, including correlated subqueries, is validated independently, and every JOIN predicate must exactly cover one approved executable relation.

Datasource evidence import is likewise generic: `scripts/export-sqlserver-fk-catalog.sql` exports checked SQL Server FK catalog rows, `scripts/import-sqlserver-relations.py` converts any such catalog into proposed relation evidence, `scripts/merge-ontology-relation-evidence.py` merges any proposed evidence into any Candidate IR, and `scripts/promote-ontology-relations.py` combines the merged Candidate IR with an explicit reviewed approval manifest. No importer or compiler automatically approves FK evidence.

## Generic registry contract

A bounded context has:

```yaml
snapshot_id: wferp-payables-v0.1.0
namespace: erp.payables
status: approved
provenance: {...}
datasets:
  - canonical_id: dataset.wferp.acpta
    physical_id: ACPTA
    asset_kind: sql_table
    status: observed
    grain: one row per voucher header
    entity_key: [TA001, TA002]
    time_identity: TA003
    fields: [...]
    relations: [...]
```

Supported asset kinds are `tabular_file`, `sql_table`, `timeseries`, `log_stream`, `event_topic`, and `api_resource`. Fields carry physical/canonical identity, physical type, optional unit, semantic kind, evidence status, and reason. Relations carry endpoints, key fields, cardinality, status, executability, and reason.

Registry approval means the immutable snapshot is approved for discovery; individual datasets, fields, and relations retain their own `observed/proposed/approved/rejected/deprecated` evidence state. It does not silently approve importer findings.

## Verification fixtures

The same catalog loader and MCP implementation serve:

| Fixture | Shape | Required proof |
| --- | --- | --- |
| `u1-operating-daily` | CSV/tabular ML dataset | Existing SHAP roles, allowlist, snapshot hash, and negative gates remain unchanged. |
| Full WFERP metadata | 1,369 SQL tables, 32,022 fields, 1,178 candidate relations | Tables/fields are observed; every medium-confidence heuristic relation remains proposed and `executable: false`. |
| `http-server-request` | observability event dataset | OpenTelemetry-named event attributes and duration metric resolve through the same bounded context tool. |

Self-check must prove all three snapshots load by dataset ID, tool output remains bounded/read-only, and the WFERP proposed relation cannot be mistaken for an approved join.

## Acceptance criteria

1. Three registries repeatedly compile to byte-identical canonical snapshots.
2. Catalog JSON passes its Draft 2020-12 schema.
3. `ontology-mcp/server.py --self-check` covers tabular, relational, and observability fixtures.
4. Existing Planner U1 positive and negative semantic checks pass without snapshot hash drift.
5. Unknown snapshot/dataset, raw SQL argument, mutation, and graph-dump paths fail closed.
6. No access-policy or action-execution interface exists.
7. `scripts/import-csv-ontology.py --check` imports a new CSV without changing Ontology Core and emits zero approved candidates.
8. `scripts/import-wferp-ontology.py --check` produces a schema-valid, byte-identical Candidate IR containing all 1,369 tables, 32,022 fields, and 1,178 relations with zero approved candidates.
9. SQLGlot parses WFERP T-SQL into an AST; a single-table query may pass, while any JOIN backed only by a proposed relation fails with `JOIN_RELATION_NOT_APPROVED` before Grafana Query.
