# Report writer and binding gates

Date: 2026-09-08. Source/offline validation only; no install, deployment, live Grafana write, browser acceptance, or independent review.

## Gates

- `prepare_ml_report` accepts only a host-owned `report_manifest_ref`. Legacy execution indexes are not a Bridge input.
- `reexport_trusted_report` is a bounded compatibility path for an existing successful `profile_dataset` or `execute_ml_contract` execution. It requires authenticated server provenance, copies no generic/untrusted report source, runs no Python, writes fresh execution/provenance/manifest refs, and is idempotent through the artifact effect receipt.
- Raw report dashboards and manually supplied `$report_manifest_ref` bindings are rejected. Composed dashboards must travel as an opaque `$dashboard_ref`; the Bridge validates the stored evidence-bound dashboard before resolving assets.
- The built-in Go writer rejects report-tagged full JSON without an opaque composed ref. The installer patch stack includes this writer gate as `ask-o11y-writer-gates.patch`.
- Existing dashboards without server-owned provenance remain a legacy recovery gap; chat history cannot establish ownership or approval.

## Verification

Passed:

```sh
.venv/bin/python sandbox-analysis-mcp/server.py --self-check
.venv/bin/python scripts/check-artifact-bridge-plotly.py
.venv/bin/python scripts/check-artifact-bridge-report-synthesis.py
.venv/bin/python scripts/check-ml-dashboard-write-gate.py
.venv/bin/python scripts/check-ask-o11y-patch-stack.py
.venv/bin/python scripts/check-ask-o11y-autonomous-analyst.py
PATH="$PWD/.scratch/go/bin:$PATH" GOPROXY=off go test ./pkg/agent -run 'TestDashboardWriter' -count=1
```

The checks cover fresh re-export/ref pairing, replay, generic-source rejection, manual binding rejection, opaque dashboard resolution, writer UID/provenance behavior, and reconstructed installer bytes.
