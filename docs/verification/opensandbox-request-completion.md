# OpenSandbox request completion repair

## Scope and outcome

User-authorized, main-agent-only repair of premature Python completion. No ML rerun, original-operation retry, Dashboard write, dependency upgrade, new protocol, retry framework, or changes to other services. The user separately approved switching execd and restarting only OpenSandbox and sandbox-analysis-mcp, followed by data-free live checks.

The configured execd v1.0.21 accepted any kernel idle message without correlating its parent request, then emitted completion before receiving the execution reply. The local MCP compensated for an open stream by closing it ten seconds after that event. A 20-second diagnostic proved that this could return while Python was still running; missing audit.json then caused an indeterminate result and Sandbox cleanup.

## Changes

- `patches/opensandbox-execd-completion.patch`: patch upstream commit `34653f7a70da87029abb40624f1de8dfee3f1576` (docker/execd/v1.0.21). Filter stream messages by the submitted request ID and expected shell/IOPub channel. Complete only after that request's execute_reply and idle, in either order. Preserve failure in the final result. Replace the early notification/polling goroutine with event-driven completion in the existing execution state; ignore messages after its terminal result.
- `sandbox-analysis-mcp/server.py`: delete the ten-second stream wrapper, its private SDK interception and obsolete wrapper-only self-test. Use the unmodified native SDK stream lifecycle.
- `scripts/build-opensandbox-execd.sh`: source-checksummed local Linux rebuild of that same upstream version plus patch; no automatic deployment. Only `/execd` is replaced in the existing base image.
- `scripts/check-opensandbox-completion.py`: synthetic live regression for short, 20-second and failing Python. Observes but does not terminate/filter native SSE, verifies the actual Sandbox execd binary digest, and uses existing resource/network policy. No real user data or producer receipts.
- `config/opensandbox.local.toml`: pin the verified local execd image.

## Verification and rollout

Evidence directory: `.scratch/opensandbox-completion-fix/`.

- Original failing WebSocket regression: `red.log` — foreign errors leaked, completion preceded matching reply+idle, and a send-on-closed-channel panic was observed.
- Fixed request tests and runtime/controller tests: `go-tests.log`, `rebuild.log` — all three packages PASS. Existing stream-output coverage remains; obsolete polling tests are replaced by protocol-order/correlation tests.
- Fresh extraction + formal patch rebuild produces exactly the same binary SHA as the tested source build.
- Base image: `opensandbox/execd@sha256:1dc98c7de10b9a73450ac75aa0f200ad7972f2c40f5225f6a8998e166b45d6dd`.
- Deployed image: `sha256:40820c279764f7b82621d87fa725b645138a2f4c0ac4868399b4ffb29aaee212`.
- Tested/rebuilt/actual Sandbox execd SHA: `3ea6a2502e20d9a515a5e31d809de1f524ac50e0cfa5bcfd6169268012d38510`.
- `live-check.log`: short completed with audit/results; 20-second body emitted execution_complete at 27.419 seconds including startup, returned at 27.973 seconds with audit/results; failing body preserved ValueError/traceback/audit and emitted no success completion. Native SDK reached stream EOF without the local wrapper. All probe Sandboxes cleaned up.
- `report-regression.log`: existing native report RPC/store/inspect/compose/resolve fixture PASS.
- No active Grafana runs before restart (`sessions-before.json`, 11 sessions). Only the approved two services restarted; both active at 2026-09-12 00:55:57 +08:00.
- `retained-before.sha256` verifies the original indeterminate operation identity remains unchanged. `deployed-source.sha256` verifies deployed source/config and binary unchanged after checks.

## Explicit limits / preserved failures

- The original user ML operation remains indeterminate and was not resumed or rewritten. These are real execution-layer checks, not a fresh Ask O11y ML/Dashboard E2E result.
- Existing MCP `--self-check` has a stale Plotly expectation that rejects a figure where current report behavior preserves a partial report. Both HEAD baseline and modified source fail the same expectation (`mcp-self-check-baseline.log`, `mcp-self-check.log`). It is not reported as passing or fixed here; the current native report regression passes.
- Go race instrumentation was unavailable: first CGO disabled, then no gcc (`execute-race*.log`). No compiler installed; ordinary Go tests and actual native execution checks passed.
- Four changed runtime/check/config primary LSP checks and six-file scoped lens cache are clean. Unscoped session cache still reports seven errors in untouched `wferp/_Source/1_mssql_to_json.py` plus unrelated warnings; this is not a whole-repository clean claim.
- Three unrelated string-identity scanner findings were source-verified membership comparisons, marked false-positive; `stale-diagnostic-check.json` confirms no string-identity comparisons in either file. Do not change those report assertions to satisfy stale warnings.
- Initial Docker build using a bare config ID in FROM failed resolution; corrected to the existing repository digest and preserved both logs. No service change occurred on that failed build.

## Rebuild / rollback

`GO=/absolute/path/to/go bash scripts/build-opensandbox-execd.sh`

The build prints its new image ID; pin that ID only during an approved deployment. For rollback, restore both `.scratch/opensandbox-completion-fix/config-before.toml` and `mcp-before.py`, then restart the same two services after checking for active work. Never roll back just one half or retry the original operation to test recovery. No commit/push was performed in this repair.
