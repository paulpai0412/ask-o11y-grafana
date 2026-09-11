# Plotly readability and outcome claims — 2026-09-07

## Scope

No fixed analytical workflow, chart quota, slot template or mandatory coordinates. Preserve the user's confirmed analytical scope and all figure series. Existing unrelated dirty changes remain untouched.

## Changes

- `grafana-panels/asko11y-plotly-panel/src/module.tsx`: plot height follows Grafana panel height (320px minimum with scrolling); narrow panels can shrink below the previous 360px view-column minimum; per-view and cross-view evidence narratives remain accessible in native keyboard-operable disclosures rather than reserving a fraction of the chart area.
- `patches/ask-o11y-nlap-authority-and-effects.patch` and `scripts/configure-ask-o11y-workflow-tools.py`: outcome-based instructions distinguish persistence, saved-JSON comparison and actual rendering evidence. Missing rendering evidence must be disclosed, not called verified. Chart representation remains a dynamic choice; no silent series removal, sampling, normalization or aggregation.
- `scripts/check-ml-plotly-readability.cjs`: exercises the actual React component through SSR at different panel heights, checks disclosure and immutable input evidence, and checks both instruction sources. Uses existing panel dependencies and `.scratch/ask-o11y-release-build` React dependencies.

## Verification

Passed:

```sh
node scripts/check-ml-plotly-readability.cjs
python3 scripts/check-ml-plotly-panel-plugin.py
python3 scripts/check-ask-o11y-patch-stack.py
python3 scripts/check-ml-no-hardcoded-report.py
git diff --check
```

Primary LSP checks passed for the three edited code files. Panel build retains existing 4.31 MiB webpack size warnings.

## Not verified / not performed

- No authenticated Grafana save/readback or actual browser visual acceptance. Existing Playwright expects Chromium at `/home/timmypai/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome`, which is absent. No browser installation attempted.
- No claim that this fixes density arising from the user's particular data/figure; no current problem dashboard or screenshot was supplied. The reproduced source defect is fixed-size plot space and competing narrative layout, not a proven data-specific cause.
- Guidance is not a runtime completion gate and does not itself prove model compliance.
- No live plugin installation, service restart, dashboard mutation, commit or push.
- Independent child review unavailable: role preflight failed on existing `team.docs` skill mismatch. No team configuration changed to bypass it.

Next: install through the approved local build/install path when authorized, then inspect the affected authenticated Dashboard at its actual viewport, including narrative disclosure and Plotly interaction. Report each outcome separately; SSR/build success is not visual acceptance.
