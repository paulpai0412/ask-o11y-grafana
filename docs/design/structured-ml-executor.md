# Structured ML executor

Status: trusted executor implemented; 2026-09-05 contract-integrity amendments pending implementation and release acceptance.

Current requirements: [Natural-language analysis platform](natural-language-analysis-platform.md), NLAP-03/04/05/06/09/10/11. Historical checks below do not establish compliance with these amendments.

Scope: Sandbox Analysis MCP standard ML execution path

Related: [`ontology-ml-accuracy.md`](./ontology-ml-accuracy.md), [`ml-grafana-presentation.md`](./ml-grafana-presentation.md), [ADR 0003](../adr/0003-isolated-python-analysis-mcp.md).

## Problem

The original standard-ML path used model-authored Python, exposing library-signature, dtype, payload and rendering errors. A trusted template now exists. The current gap is not merely generated-code freedom: the plan/frame linkage, actual split, study-level holdout use and trusted execution receipts must also be enforced.

## Design

One new Sandbox tool:

```text
execute_ml_contract(frame_ref, contract_ref, seed)
```

- `contract_ref` is the opaque `plan_ref` produced by Data Query Planner. The plan already carries the ontology-pinned `analysis_contract` (kind, target, features, split, autotune, objective, budget) and is verified by `verify_plan_for_context`.
- The host composes a deterministic Python template with contract values baked in as literals (`ast`-parse-checked in CI) and delegates to the existing authorized execution machinery (`execute_python_analysis`), so frame authorization, trusted validity audit, provenance, and artifact retention are unchanged.
- The template runs entirely inside the pinned image using the trusted modules:
  `ml_preprocessing` (training-only preprocessing) → `ml_autoresearch` (budgeted search + untouched holdout + guards) → `ml_presentation` (bounded manifest + plain-language assets, including the data-profile chart).
- The model never sees or authors the template. Free-form Python remains available via `execute_python_analysis` / `execute_python_preprocessing` for exploratory work; the planner-facing guidance points standard ML at `execute_ml_contract`.

## Mandatory amendments (pending)

- Verify contract_ref and frame provenance share the same plan digest, source, scope and approval revision; independently authorized refs are insufficient.
- Honor the declared outer/CV split. Unsupported combinations fail before compute, never silently become random stratified splits.
- Select model and feature set using train/CV only; lock the winner before final holdout. Holdout-derived verdicts cannot filter selection candidates.
- Separate metric direction from optional business optimization direction; pure prediction does not require target optimization.
- Account for global search trials across models/feature sets and expose actual adapter availability. Sequential execution is not described as parallel.
- Record source/query/eligible/train/test/explained rows and approved exclusions. Generic Python artifacts cannot self-assert verified-ML status.
- Recover compute/write transport failures through operation receipts; do not blindly rerun completed work.
- Route every generated image through the Plotly plugin in explicit image/plotly mode (NLAP-07).

## Contract additions

Optional `analysis_contract` fields, validated by the Planner gate and baked into the template:

| Field | Meaning |
| --- | --- |
| `positive_class` | Label treated as the positive class for string targets; numeric targets use `1` |
| `purpose` | Plain-language analysis purpose shown on the dashboard |
| `conclusion` | Plain-language conclusion shown on the dashboard |

## Supporting library changes

- `ml_autoresearch.run_classification_autoresearch` additionally returns holdout `probabilities` and original-column `top_features` (permutation importance on holdout), so the executor can emit confusion/ROC/PR/importance assets without a second model fit.
- Objective scoring map: `accuracy→accuracy`, `roc_auc→roc_auc`, `pr_auc→average_precision` (previously `pr_auc` was passed through as an invalid sklearn scoring string).

## Failure behavior

- Missing/unsupported kind, missing fit scope, invalid autotune bounds, or tampered plan hash → fail closed before execution (existing gates).
- Template failures surface as normal recoverable/non-recoverable Sandbox errors with redacted values; no LLM self-repair loop is entered for the structured path (the template is host-owned).

## Acceptance

1. Contract TDD: composed template parses, bakes budget/objective/positive class, emits manifest; missing contract, unsupported kind, and tampered hash fail closed.
2. Negative gates: mismatched valid refs, unsupported split, unapproved exclusions, holdout-dependent winner changes and budget overruns fail their corresponding checks.
3. End-to-end: fresh Ask O11y session, natural language only, produces a Grafana Preview through `execute_ml_contract` with zero model-authored training code; original event receipts and browser evidence establish lineage and plugin rendering. A baseline-blocked model is a valid platform outcome, not predictive success.
