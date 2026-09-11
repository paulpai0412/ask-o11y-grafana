# Report question and evidence experience

Date: 2026-09-08. Source/offline validation only; no deployment, authenticated browser acceptance, or independent review.

## Contract

- The report dashboard intro now displays the host-retained `manifest.purpose` as the business question. It is rendered as escaped text and marked `host_retained_report_purpose`; the report compositor does not infer a question from metrics, chart titles, or LLM prose.
- A partial report explicitly says that business-question binding and analytical completion are separate. Partial delivery is not completion, approval, deployment, or a retry grant.
- The existing evidence-bound thesis, section, panel, limitation, next-step, Plotly/image mode, and fact citation contracts remain unchanged. Numeric claims remain evidence facts rather than free text.
- The dashboard contract rejects a report intro without the host-retained question marker. This keeps a beginner-facing report from silently losing the question that gave the evidence its scope.

## Verification

Passed:

```sh
.venv/bin/python scripts/check-ml-dashboard-compositor.py
.venv/bin/python scripts/check-ml-dashboard-contract.py
.venv/bin/python scripts/check-ml-dashboard-write-gate.py
.venv/bin/python scripts/check-ml-report-synthesis.py
.venv/bin/python scripts/check-analysis-coverage.py
```

The checks cover question rendering, partial notices, dynamic sections, evidence citations, Plotly/image boundaries, and rejection of malformed dashboard shapes.

## Limits

The LLM-authored prose is structurally and evidence-bound validated, not a substitute for human comprehension testing. No claim is made that correlation, SHAP, a chart, or a report narrative is causal or operationally safe. Actual Grafana rendering, browser interaction, and authenticated save/readback remain unverified.
