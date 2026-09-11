# Delivery-state candidate rejected; awaiting user decision

Goal `mttxt1el-kc9aq6`, task `delivery-state`. Writer `63829ce7-8c4e-404c-a01c-99f34f2e62c1` / workflow `fdfb2c24-2cd1-49e1-aa37-94f7770611b4` is terminal reported; not accepted. No writer active. Prior tasks recover-scope and generic-repair remain accepted; overall goal2/6.

## Parent-confirmed blocker

`.scratch/ask-o11y-release-build/pkg/agent/delivery_state.go::isToolName` accepts any tool with a matching short-name suffix. A foreign tool `untrusted_prepare_ml_report` with self-asserted coverage can set authoritative delivery to `evidence_available` and replace the original question with `Different question`. This violates the explicitly required source-identified trusted-contract boundary.

Trusted offline regression lives in `.scratch/y5-goal/delivery-parent-check/delivery_identity_test.go`, injected via Go overlay only; no production/test checkout file was modified by parent. Command: from release build, `GOPROXY=off GOTOOLCHAIN=local ../go/bin/go test -overlay /home/timmypai/apps/grafana/.scratch/y5-goal/delivery-parent-check/overlay.json ./pkg/agent -run '^TestParentForeignToolCannotSetDelivery$' -count=1`. Actual compiled test exit1; raw `result-valid-probe.log` reports foreign tool promoted status and changed question. Earlier `result.log` was a parent scratch-test missing-brace setup error, not a product finding; preserved separately.

Source also stores planRef but does not use it to bind/compare coverage updates; treat cross-report/stale-evidence behavior as additional required inspection, not a separately reproduced failure yet. Writer's passing positive checks do not cover the confirmed hostile-name case.

## Stop and recovery

All three previously agreed production repair rounds consumed. Parent asks user before another repair; no new source edit/child launched, no deployment/restart/analysis/Grafana write. Reported Goal step remains unsettled until parent records native+host-backed rejection/recovery. Preserve source, patch, raw reports and failing regression. If approved, scope one bounded source-identity/report-binding fix plus regression/independent correctness/security gates; do not change models/providers/harness permissions or reset old counts. If not approved, retain partial source and hand off blocker.
