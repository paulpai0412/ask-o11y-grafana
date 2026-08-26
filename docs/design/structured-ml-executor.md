# Structured ML executor

Status: implementation design

Scope: Sandbox Analysis MCP standard ML execution path

Related: [`ontology-ml-accuracy.md`](./ontology-ml-accuracy.md), [`ml-grafana-presentation.md`](./ml-grafana-presentation.md), [ADR 0003](../adr/0003-isolated-python-analysis-mcp.md).

## Problem

Standard supervised ML currently executes model-authored Python. Every observed execution failure in the Adult and Telco acceptance runs traces to this freedom: nullable-dtype boolean crashes, wrong library call signatures, invented payload shapes, and Matplotlib style changes that reset the CJK font. Advisory skills reduce but cannot eliminate these failures, and results are not reproducible byte-for-byte.

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
2. End-to-end: fresh Ask O11y session, natural language only, produces a Grafana Preview through `execute_ml_contract` with zero generated training code.
