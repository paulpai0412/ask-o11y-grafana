# Ontology-assisted analysis

Status: implementation design

Scope: `u1-operating-daily` CSV Random Forest/SHAP vertical slice

The generic snapshot/catalog layer is specified in [`generic-ontology-core.md`](./generic-ontology-core.md). This document now describes the U1 ML policy adapter retained on top of that core.

## Purpose

Ontology supplies approved semantic declarations before a query plan is executable. It does not read datasource rows, infer business truth at runtime, or enforce policy. The Data Query Planner uses trusted deterministic code and the same immutable snapshot to enforce the final pre-query gate.

The first slice proves one narrow outcome: a SHAP analysis of `heat_rate` uses only approved, prediction-time-eligible fields and a chronological holdout, while unsafe plans stop before Grafana Query.

## Runtime topology and trust boundaries

```text
Ask O11y LLM
  ├─ Ontology MCP (read-only declarations; no credentials)
  ├─ Data Query Planner MCP (plan-only; final semantic gate)
  ├─ Grafana Query MCP (only datasource executor)
  ├─ Sandbox Analysis MCP (isolated Python; no credentials)
  ├─ hidden Artifact Bridge MCP (opaque binding only)
  └─ built-in mcp-grafana_update_dashboard (only Dashboard writer)
```

All external MCP endpoints bind loopback and use the existing shared bearer plus server-configured org/user identity. Ontology is model-visible. Artifact Bridge remains hidden.

| Component | Trusted inputs | Responsibility | Forbidden |
| --- | --- | --- | --- |
| Git semantic registry | reviewed YAML and evidence | Human-readable semantic decisions | Runtime mutation |
| Snapshot builder | registry and JSON Schema | Validate, canonicalize, hash, release immutable JSON | Domain inference |
| Ontology MCP | approved snapshot | Bounded resolution, context, classification, advisory validation | Raw data, credentials, mutation, arbitrary traversal/query |
| Planner | trusted snapshot path and ontology refs | Deterministic final gate and immutable plan | Datasource execution, LLM-based approval |
| Grafana Query | authorized plan ref | Execute the exact bounded query and validate frame shape | Semantic guessing |
| Sandbox | authorized frame and analysis contract | Isolated deterministic analysis and bounded artifacts | Datasource access, host execution fallback |
| Artifact Bridge | authorized opaque refs | Resolve bindings | Dashboard design or write |
| Built-in Grafana MCP | host-approved Dashboard JSON | Sole Dashboard write | Resolving untrusted raw artifact paths |

Compromise of Ontology must not authorize a query: Planner reloads the trusted local snapshot, verifies its SHA-256 and approval state, and re-evaluates every semantic rule. Compromise of Planner still cannot obtain datasource credentials; Grafana Query accepts only an authorized opaque plan ref.

## Registry and snapshot contract

The v1 registry is Git-versioned YAML validated by a hand-authored JSON Schema. A release script emits canonical JSON and computes SHA-256 over the canonical bytes. No RDF store, graph database, LinkML, SHACL runtime, Semantica dependency, or curation service is required.

The U1 dataset declaration contains:

- dataset canonical ID and physical dataset ID;
- grain (`one row per unit per operating day`);
- entity key (`unit_id`) and time identity (`date`);
- field physical name, type, unit, aliases, description, and evidence;
- `semantic_kind`, `analysis_role`, approval `status`, and availability;
- target and quality policies;
- derived lineage/proxy status where known;
- owner, effective interval, source provenance, snapshot ID, and hash.

Allowed status values are `observed`, `proposed`, `approved`, `rejected`, and `deprecated`. Only `approved` fields and policies may authorize an executable plan. Missing evidence must remain `proposed` or `unknown`; confidence is never approval.

For this slice, `raw_coal_consumption_g` remains a potential target proxy until formula lineage or steward evidence proves otherwise. It must not enter the approved feature allowlist.

## Bounded Ontology MCP tools

The service exposes exactly four tools:

1. `resolve_concepts(terms, snapshot_ref?)`
   - at most 16 terms;
   - returns canonical candidates, aliases, definitions, status, and evidence refs;
   - returns ambiguity rather than choosing a candidate.
2. `get_semantic_context(dataset_id, intent, target?, fields?, snapshot_ref?)`
   - one dataset, at most 200 fields;
   - returns grain, identity, target, approved feature allowlist, exclusions, quality, availability, split policy, and snapshot identity;
   - never returns rows or an unbounded registry dump.
3. `classify_fields(dataset_id, fields, snapshot_ref?)`
   - at most 200 exact physical or canonical field IDs;
   - returns role, kind, unit, availability, lineage policy, status, and evidence.
4. `validate_analysis_contract(contract, snapshot_ref?)`
   - side-effect-free advisory dry run;
   - returns `conforms`, bounded rejection codes, failed rules, and snapshot identity;
   - cannot make a plan executable.

Unknown arguments fail closed. Tool schemas do not contain SQL, SPARQL, Cypher, datasource URI, credential, mutation, arbitrary depth, offset, or graph-dump inputs. Responses have fixed item and byte limits.

## Deterministic Planner gate

Planner accepts an ontology-assisted request only when it contains an exact dataset, target, feature list, analysis method, as-of/cutoff, split policy, and snapshot ID/hash. Before writing `query-plan`, trusted code verifies:

- snapshot exists, is approved, and its canonical hash matches;
- dataset, target, and fields exist in that snapshot;
- target is approved as target and is absent from features;
- every feature is approved, allowlisted, and eligible at the declared as-of;
- identifiers and quality-only fields are excluded from model features;
- post-outcome, unknown, proposed, forbidden, and unresolved proxy/lineage fields are excluded;
- grain, unit, validity, minimum-row, and chronological split policies match;
- assumptions are explicitly approved.

The query plan carries:

```json
{
  "ontology": {"snapshot_id": "...", "sha256": "..."},
  "analysis_contract": {
    "kind": "random_forest_shap",
    "target": "heat_rate",
    "features": ["..."],
    "excluded_fields": [{"field": "...", "reason": "..."}],
    "as_of": "...",
    "split": {"kind": "chronological_holdout", "time_field": "date", "test_fraction": 0.25},
    "preprocessing": {"fit_scope": "training_only"},
    "seed": 42,
    "interpretation": "predictive_association_not_causation"
  },
  "plan_sha256": "..."
}
```

A plan hash covers the query, ontology identity, feature decisions, cutoff, split, preprocessing, seed, assumptions, and analysis contract. Changing any covered value requires a new preview and confirmation.

Minimum rejection codes are:

- `SNAPSHOT_NOT_APPROVED`
- `SNAPSHOT_HASH_MISMATCH`
- `UNKNOWN_DATASET`
- `UNKNOWN_FIELD`
- `FIELD_NOT_APPROVED`
- `FIELD_ROLE_FORBIDDEN`
- `TARGET_USED_AS_FEATURE`
- `QUALITY_FIELD_USED_AS_FEATURE`
- `TARGET_PROXY_UNRESOLVED`
- `AVAILABILITY_UNKNOWN`
- `FEATURE_AFTER_AS_OF`
- `SPLIT_POLICY_VIOLATION`
- `CONTRACT_HASH_MISMATCH`

Any rejection before query means Grafana Query, Sandbox, and Dashboard write call counts remain zero. There is no fallback to LLM guessing, direct DB access, host Python, or another executor.

## Confirmed SHAP sequence

```text
1. Inspect approved U1 ontology snapshot (no datasource query).
2. Resolve heat-rate intent and request bounded semantic context.
3. Build an exact analysis contract and run advisory validation.
4. Planner runs the deterministic gate and emits preview + plan hash.
5. User confirms that exact preview.
6. Grafana Query executes the opaque plan and validates the frame.
7. Sandbox sorts valid rows by date and creates a chronological holdout.
8. Preprocessing fits on training rows only; Random Forest uses a fixed seed.
9. SHAP explains holdout predictions for approved features only.
10. Sandbox emits metrics, inclusion/exclusion reasons, non-causal warning,
    provenance, and PNG.
11. The hidden bridge resolves the opaque PNG binding.
12. The built-in Grafana MCP writes a static-image/text Preview Dashboard.
13. Publication removes the Preview tag on the same UID without rerunning.
```

The U1 safe contract uses `heat_rate_valid = true`, sorts by `date`, reserves the latest 25% as holdout, fits median imputation on training rows only, and fixes seed `42`. It reports RMSE, MAE, R², valid/excluded row counts, feature reasons, and snapshot/plan/frame/code hashes. SHAP is explicitly described as model association, not causal effect.

Analysis dashboards contain no Grafana data targets. A PNG content type/magic check, opaque host-side binding resolution, and the existing Preview/publication lifecycle remain mandatory.

## Failure behavior

- Ontology unavailable: no new ontology-assisted plan; report a bounded service failure.
- Snapshot missing, stale, unapproved, or hash-mismatched: reject before query.
- Unknown field, role, availability, lineage, cutoff, or split: reject or ask for clarification; never downgrade to a warning.
- Frame/contract drift: Grafana Query withholds `frame_ref`.
- Sandbox contract/hash/audit drift: do not create or trust analysis output.
- Artifact or Preview lifecycle mismatch: do not write or publish the Dashboard.

## Acceptance criteria

1. Registry YAML passes JSON Schema and repeated snapshot builds produce identical bytes and SHA-256.
2. Ontology tools return bounded approved context and reject mutation, raw query, credentials, unknown arguments, and unbounded requests.
3. Planner rejects target-as-feature, unknown/unapproved feature, unresolved target proxy, and non-chronological split before query; all downstream call counts are zero.
4. A safe plan pins snapshot/hash and carries the complete analysis contract and machine-readable exclusion reasons.
5. Real U1 E2E proves chronological holdout, training-only preprocessing, fixed seed, valid-row audit, approved feature allowlist, PNG bytes/content type, static-image-only Grafana Preview, and complete provenance.
6. Existing non-ontology CSV/Grafana behavior remains covered and passes without unexplained regression.

## Out of scope

Access policy, property-level authorization, hidden-field security behavior, action definitions/execution, production curation UI, causal inference, enterprise knowledge graphs, DataHub/OpenMetadata/MetricFlow/Cube, LinkML/SHACL production runtime, and Semantica production dependency are deferred. WFERP and observability metadata are generic-core discovery fixtures only; WFERP proposed relations still cannot authorize joins.
