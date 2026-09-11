# Y5 minimal team repair — source-slice acceptance (partial overall)

## Scope and source

Mission `64fb625b-c548-44f6-b323-3e6f03718480`; HEAD `2273c97e984ed510bd2ac6b529ac944c459d2fd8` with existing dirty changes preserved. No staging, commit, install, restart, live analysis or Grafana write.

Exact source fingerprints: `.scratch/y5-minimal-team/slice-final-source-freeze.json`. Original baseline: `source-baseline.json` in the same directory. Parent compared final files to independent-review candidate `fix2-source-freeze.json`: **only `scripts/check-analysis-coverage.py` changed after review**, to add missing regression cases. Production source is unchanged from the reviewed candidate.

## Observed results

- Bounded real installed GO/Express bar/scatter producer JSON passes sanitizer/report/bridge; typed numeric arrays decode without unsafe integer rounding. Arbitrary templates reject; only the exact installed default fingerprint is normalized.
- Audited bounded successful executions retain evidence on presentation/report failure. Generic Python is not promoted to trusted ML or allowed through trusted re-export.
- Trusted retained re-export uses authenticated single-hop parent lineage, digest/status verification and immutable originals. The public returned refs pass prepare/inspect/compose with verified retained comparison facts. This is a trusted retained fixture integration, **not live ML execution or historical Y5 recovery**.
- Host question and not-assessed/partial notices persist for tested reporting paths. These do not establish scientific completion, operational advice, chat completion or deployment readiness.

## Mechanical evidence

Parent ran six checks with disposable `ANALYSIS_ARTIFACT_ROOT`, all exit 0:
`check-y5-minimal-repair.py`, `check-analysis-coverage.py`, Sandbox `--self-check`, `check-ml-dashboard-compositor.py`, `check-ml-plotly-contract.py`, `check-artifact-bridge-plotly.py`.
Logs: `.scratch/y5-minimal-team/host-checks/fix2/`.
After the test-only addition, parent reran `check-analysis-coverage.py`, exit 0: `coverage-parent-negatives.log`. Seven cases cover missing/mispaired/foreign parent references, source/fresh identities and authoritative source/fresh report facts. Each invalidates composition using the previously verified context and prevents fresh preparation from claiming `evidence_available`. Original temporary files and report context are restored; restored control still composes.

Test-design corrections are not product fixes: preparation refreshes the mutable report context, so stale-context composition is tested **before** preparing again; fresh fact mutation targets the authoritative `report-source.json`, not the unused retained legacy presentation copy. A fresh not-assessed descriptive report remains permitted by the contract; these tests do not establish a blanket ban on rendering all unverified descriptions.

Primary LSP clean; final lens session error scan reports no errors across 21 dispatched files (not whole-project proof). `git diff --check` and no-staged check pass.

## Independent evidence and parent disposition

Native fresh correctness/security reviews inspected production source at final repair2:

- workflow `4e226e47-76e5-4bfe-833e-98d403e93e6c`
- correctness `158af12f-1ce6-4871-8fb1-e416b0d8dd46`
- security `5fd92f37-c94c-481d-b91d-71bec957482d`

Both confirm prior production blockers resolved and identify the missing negative test set. Parent closes that test-evidence finding with the runnable seven-case regression and logs; no additional production fix or blanket re-review needed. Their original failed verdicts are retained, not rewritten as passes.

Correctness additionally calls absent legacy statuses mandatory compatibility. **Parent does not accept that spec interpretation**: the repair contract permits compatibility only if supported by concrete evidence; it does not require acceptance of absent statuses. Current strict rejection is a documented compatibility limit, not permission to backfill statuses. Explicit null/unknown/malformed states remain rejected.

Two evidence-driven implementation repair rounds were used. No third production repair was performed. Parent-only test completion closes the source-slice evidence gap, not the whole task.

## Overall original mandate remains PARTIAL

1. Plotly source compatibility: verified for the bounded installed producers, not all arbitrary Plotly formats.
2. Preserving/re-exporting eligible trusted retained output: source verified. **Historical failed Y5 executions have insufficient provenance and remain blocked. Generic retained-output repair capability is not implemented.**
3. Original question/status presentation: source verified. **Chat/Go runtime completion enforcement remains unimplemented/unverified.**
4. Deployment: **not authorized/performed in this batch**. No installed-binary fingerprint, live readback or browser acceptance. Prior Preview and unrelated trusted E2E evidence cannot substitute.

The full TODO/mission must remain open. Next work is a separately bounded generic-result repair and chat/runtime integration slice within the user's minimal-combination mandate; any required authority/permission change must be surfaced before implementation. Do not offer localhost as updated or describe the original heat-rate question as answered.
