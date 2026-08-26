# Ontology-assisted ML accuracy program

Status: implementation design (approved A/B/C/D; F/G designed 2026-08-24 after Palantir ontology study)

Scope: uploaded datasets and general ML analysis paths

This document extends [`ontology-assisted-analysis.md`](./ontology-assisted-analysis.md) from the single `random_forest_shap` U1 slice to all ML analyses. It records four approved workstreams (A–D), two further workstreams (F semantic hidden fields, G autoresearch) designed against Palantir Foundry's Ontology patterns, and an honest impact assessment.

## Problem statement

Today the ontology gate covers only one contract kind (`random_forest_shap`) over manually approved snapshots. Uploaded datasets bypass ontology entirely: the sandbox-generated code selects its own features and preprocessing. Observed consequence in the Adult benchmark run (2026-08-24): generated code used the ID-like `fnlwgt` column as a feature and hit a pandas nullable-boolean crash — both classes of failure a semantic layer prevents.

## Workstream A — auto-snapshot for uploaded datasets

Upload becomes the ontology entry point.

1. On successful upload (`PUT /uploads` in Grafana Query service), run `import-csv-ontology`-equivalent inference (header + ≤1000-row sample → candidate IR with inferred scalar types).
2. Store as non-authoritative candidate snapshot bound to the upload session/user identity.
3. The agent may then call `classify_fields` (semantic kind + analysis role: identifier / time / target-candidate / feature) and `resolve_concepts`; approval promotes candidate → approved snapshot.
4. Approved snapshots unlock the existing deterministic plan gate for that dataset.

Trust boundaries unchanged: candidates are declarations only; promotion to approved requires explicit review; Ontology MCP never reads rows.

## Workstream B — widen analysis contracts

1. Replace `kind: const "random_forest_shap"` with enum: `random_forest_shap`, `gradient_boosting`, `logistic_regression`. Each kind maps to a fixed sandbox execution template.
2. `preprocessing_fit_scope: training_only` becomes enforced at execution: the sandbox runner rejects code whose preprocessing cannot be proven fit-on-train-only (template-based execution makes this checkable by construction rather than by code review).
3. Contract validation stays deterministic in Data Query Planner using the same immutable snapshot (no new trusted component).

## Workstream C — skill thresholds become ontology quality policy

The ml-method-selection skill's data-reality checks move from advisory prose to snapshot-declared policy enforced at plan time:

| Policy | Threshold | Plan-time action |
| --- | --- | --- |
| imbalance_ratio | positive rate < 5% | require declared handling strategy or reject |
| missing_rate | > 40% per feature | force into limitations list |
| identifier_columns | key-like / constant columns | excluded from feature allowlist |
| leakage_candidates | post-outcome timestamps, aggregates of target | rejected from features |

Policies are declared in the approved snapshot (not hardcoded in the planner), so domain datasets can override defaults.

## Workstream D — deterministic preprocessing library in the sandbox image

A small module (pandas/sklearn only) inside the pinned image:

- missing-value strategies (median/most-frequent/flag) with fit-on-train discipline
- high-cardinality encoding via fold-internal target encoding
- collinearity pruning (VIF > 10 groups)
- nullable-dtype-safe boolean masks (eliminates the observed `boolean value of NA is ambiguous` class)

Generated analysis code calls the library instead of hand-rolling preprocessing. This reduces both accuracy variance and generated-code failure modes.

## Deferred workstream E — superseded by G

Model-level performance work (CV, tuning) is subsumed by Workstream G.

## Workstream F — semantic hidden fields via relation paths

Borrowed from Palantir's ontology pattern: a flat analysis frame under-uses information that is semantically reachable through object relations. In Foundry terms these are derived properties computed over linked objects; here they become ontology-declared, plan-gated synthetic features.

Mechanism:

1. The approved snapshot declares object types and bounded relation paths (existing `get_relation_paths` tooling). Synthetic-feature generation enumerates candidates deterministically from the snapshot — never from row data at runtime:
   - **L1 aggregation over one-to-many links**: count / mean / max / time-since-last of child records within a declared window (e.g., machine → work orders: `wo_count_30d`, `hours_since_last_fault`).
   - **L2 parent inheritance across many-to-one links**: parent properties joined onto child rows (e.g., batch → supplier region), capped at 2 hops.
2. Every synthetic candidate carries:
   - generation lineage (path, aggregator, window) recorded in the snapshot;
   - an **as-of eligibility rule** — only events strictly before the prediction timestamp may contribute. This is point-in-time correctness made explicit; Palantir does not automate this and we should.
3. Candidates enter a review queue like any other snapshot change; approval adds them to the feature allowlist. The plan gate then treats them identically to physical columns.

Bounds: ≤ 2 hops, ≤ 32 synthetic candidates per dataset, windows/timestamps must be declared fields. Honest scope note: single-table uploads gain nothing from F; its value concentrates on WFERP-style multi-dataset analyses.

## Workstream G — autoresearch: budgeted auto-tuning with generalization guards

Borrowed from Palantir modeling objectives: centralized evaluation with quality checks gating acceptance. Implemented entirely inside the existing sandbox (network-denied, pinned stack — no Optuna; scikit-learn search utilities only).

Triggered when the user asks for tuning/best-model, or when the planner contract sets `autotune: true`.

Search protocol:

1. Search space per contract kind, declared in the execution template (not invented by generated code):
   - `gradient_boosting`: learning_rate, num_leaves/depth, min_child_samples, subsample/colsample, n_estimators
   - `logistic_regression`: C, penalty, class_weight strategy
2. Cross-validation strategy inherited from the snapshot split policy (`TimeSeriesSplit` for temporal, `GroupKFold` by lot/entity otherwise, stratified k-fold fallback) — never random folds on temporal data.
3. Optimizer: `RandomizedSearchCV` or `HalvingRandomSearchCV`, `n_iter ≤ 40`, seed fixed, wall-clock within sandbox limits.

Generalization guards (all reported, first three gate acceptance):

| Guard | Rule |
| --- | --- |
| untouched holdout | final test set is never seen during search; reported metrics come from it only |
| generalization gap | \|best CV score − holdout score\| > 0.05 → verdict "overfit", config rejected |
| importance stability | top-K feature set Jaccard across CV folds < 0.6 → flagged unstable |
| drift check | PSI > 0.25 between train/test feature distributions → flagged |

Acceptance follows Palantir's objective-check pattern: the contract may declare minimum metric thresholds; results below threshold are labelled "below objective" rather than silently returned.

Output extends the fixed report template: trials table (top 5), chosen configuration, guard verdicts, and the same limitations section. All artifacts keep run provenance (code sha256, seeds, trial table).

## Impact assessment (honest)

Measured baseline: Adult dataset, stratified split seed=42 — LogReg 0.8055 / LightGBM(scale_pos_weight) 0.8346 accuracy vs published tuned GBM ≈ 0.87.

| Workstream | Accuracy effect | Primary effect |
| --- | --- | --- |
| A auto-snapshot | ~0 to +0.5% (removes noise features like fnlwgt) | correctness, auditability, no wrong-column failures |
| B widened contracts | ~0 directly | enforcement reach; enables C/D for all models |
| C quality policy | 0 on clean datasets; prevents catastrophic errors on dirty ones | floor-raising, not ceiling-raising |
| D preprocessing library | ~0 to +1% (consistent encoding quality) | stability; removes generated-code crashes |
| F hidden fields | dataset-dependent; large on relational data (WFERP), zero on single tables | unlocks semantically reachable signal with leakage control |
| G autoresearch | **+2–4%** on Adult-like problems (0.835 → ≈0.87) plus generalization guarantees | the main numerical lever, with overfit detection built in |

Conclusion: A–D raise the floor and make results trustworthy and reproducible. F widens the ceiling on relational datasets. G closes the gap to published leaderboards while guaranteeing that reported numbers generalize.

## Measured validation — Adult dataset (2026-08-24)

Ask O11y ran the complete path:

```text
session-owned upload
→ candidate ontology classify_fields
→ feature allowlist (fnlwgt excluded as sampling-weight/identifier)
→ upload quality-policy plan gate
→ 32,561-row Grafana frame
→ untouched stratified 80/20 holdout
→ 40-trial × 5-fold autoresearch
```

| Run | Accuracy | PR-AUC | ROC-AUC |
| --- | ---: | ---: | ---: |
| pre-program LightGBM (`scale_pos_weight`, fixed params) | 0.8346 | 0.8250 | 0.9243 |
| ontology + autoresearch (accuracy objective) | **0.8701** | 0.8221 | 0.9236 |
| published LightGBM reference | 0.8707 | protocol not comparable | protocol not comparable |

Measured accuracy gain: **+3.55 percentage points**; classification error fell from 16.54% to 12.99% (≈21.5% relative error reduction). The result is 0.06 percentage points below the published LightGBM accuracy reference.

Generalization guards all passed: CV-holdout gap 0.00374, top-feature stability 0.7758, max PSI 0.00221, verdict `accepted`.

Attribution caveat: the gain is the combined A–D+G path and especially the changed optimization objective (accuracy) plus tuning; it is not evidence that ontology alone added 3.55 points. PR-AUC fell 0.0029 and ROC-AUC fell 0.0007 versus the weighted baseline, demonstrating the expected trade-off when optimizing accuracy rather than positive-class ranking.

## Production hardening backlog

The current implementation is a validated PoC, not yet a production authority. Priority order:

1. **Explicit candidate promotion** — user confirmation must create an immutable approval artifact; an observed upload candidate must not silently become approved.
2. **Semantic-content hash** — snapshot identity must hash source SHA + candidate ontology + roles + quality policy. The current upload source SHA alone cannot detect sidecar semantic changes.
3. **One shared verifier** — Planner, Grafana Query, and Sandbox must call one `verify_semantic_plan` implementation instead of duplicating security-sensitive upload verification.
4. **Structured ML executor** — standard ML uses `execute_ml_contract(frame_ref, contract_ref)` and trusted templates, not free-form model-authored Python. Exploratory Python remains a separate capability.
5. **Atomic semantic state** — write upload metadata/candidate/hints using temporary files + rename and expose `semantic_status=pending|ready|failed` to remove upload/inspect races.
6. **Sealed evaluation** — final holdout is versioned per semantic snapshot and has a bounded evaluation budget. Repeated tuning creates a new model/snapshot version instead of adapting to the same holdout.
7. **Real generalization splits** — manufacturing defaults to out-of-time or grouped-by-machine/lot validation. Random stratified split remains only a generic tabular fallback.
8. **Decision objective** — model selection takes false-negative/false-positive cost, minimum recall, calibration, and threshold policy; accuracy alone is never the manufacturing default.
9. **Role confidence** — inferred target/identifier/weight/sensitive roles carry confidence + evidence; low-confidence roles remain unknown until explicit approval.
10. **MCP lifecycle** — refresh tool schemas on `tools/listChanged`/TTL and start Ask O11y only after every configured MCP is healthy; avoid stale 71-vs-76-tool catalogs.
11. **Grouped explainability** — feature stability and importance aggregate one-hot outputs back to ontology properties.
12. **Sensitive-field policy** — protected attributes require explicit inclusion approval and subgroup performance reporting.
13. **Ablation benchmark** — separately measure ontology selection, deterministic preprocessing, objective change, and autotuning so gains are not incorrectly attributed.
14. **Relation-derived features** — implement Workstream F with point-in-time-safe lineage before claiming ontology-driven accuracy gain on relational manufacturing datasets.
