# Generic retained-report repair: source acceptance

Goal `mttxt1el-kc9aq6`, task `generic-repair`; mission `64fb625b-c548-44f6-b323-3e6f03718480`.

## Parent verdict

Task criterion met for **same-session, authenticated, safe retained generic output only**. `repair_generic_report(execution_ref)` creates fresh report refs without compute/query/profile redispatch or trusted-ML promotion. Unsafe, missing, foreign, indeterminate and tampered evidence remains rejected. Original question/data/order/provenance are retained. Historical Y5 artifacts without required provenance/digests are still blocked.

## Exact evidence

- Host input: `.scratch/y5-goal/generic-host-input.json`.
- Host receipt: `.scratch/y5-goal/generic-round3-host-receipt.json`; 36-file source digest `6438419f9eac1679460956465e0d5218212b07b470e02399da0872123fb66232`.
- Parent `verify` confirmed receipt/source/log freshness again after both final reviews; no tests repeated merely to refresh unchanged evidence.
- Seven isolated checks pass: focused Y5 repair, analysis coverage, Sandbox self-check, compositor, Plotly contract, Artifact Bridge Plotly, report-source evidence. LSP changed files clean; diff/no-staged checks pass.
- Final security `3b2f1956-0989-4e20-bb3f-104b1ea6c016` and correctness `83cc7b27-a3d7-467e-a94d-dd30544e9389` independently pass the same source. Native goal-step recoveries settled both reports; prior blocked findings remain preserved.

The tests cover public repair→prepare/inspect/compose, immutable original receipts, replay without compute, source audit mutation, missing-error states, correlated fresh-source/provenance/manifest tampering, and late provenance-write failure. That late failure returns an opaque operation/source receipt; replay does not repeat effects, and reconciliation reports indeterminate with redispatch disabled. The test's two compute calls are two distinct original requests, not repair redispatch. Reconciliation is exercised at ArtifactStore and its public wrapper reviewed as authenticated direct delegation, not claimed as an independently executed RPC scenario.

## Boundaries and next step

No installation, service restart, live analysis, Grafana write or browser check. This task does not establish scientific completeness or chat/runtime final-state enforcement. Continue `delivery-state`, followed by full integration/build, independent final gates and manual-test/deployment-approval handoff. Three total production repair rounds are now consumed; do not reset them on new task/phase. New scoped implementation is permitted, but any further failed-production repair needs the user's decision.

## Bounded retrospective

- Compare fresh derived report content to original authenticated host evidence, not merely a matching fresh hash.
- Fault-inject late persistence failures as well as the first write; an honest correlated indeterminate result is preferable to blind retry.
- Refresh a role's recovery to the newly verified candidate before review dispatch; preserve old verdicts and reuse still-valid host checks.
