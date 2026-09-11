# Report dashboard provenance — main-agent repair

Date: 2026-09-08. Source-only/offline work; no deployment, live dashboard write, dependency installation, commit, push, or Goal changes.

## Status

The user stopped the third implementation child and requested main-agent-only repair. The main agent reproduced additional defects in its surviving source, repaired them, and ran the checks below. This is **not independent review or end-to-end product acceptance**. The earlier required independent review remains outstanding; no replacement child was launched.

Production delivery is `patches/ask-o11y-report-dashboard-provenance.patch`, applied by `scripts/build-install-ask-o11y.sh` after the other 15 patches. Ignored build-source edits alone are not the deliverable. Preexisting dirty work was retained.

## Repairs

- All explicit org/UID mutations use the same cross-process file lock, spanning identity/provenance validation and external dispatch. Native-first and report-first interleavings are covered; completed publication remains available.
- Lock directories and lock inodes remain persistent. Unlinking a lock after closing it allowed an already-open contender and a new inode owner to both hold the same logical UID. Main reproduced this deterministically before repair. Pending state is removed and its directory synced **before** unlocking; lock files must not be cleaned up during operation.
- A report's pending identity is durably written immediately before actual external dispatch, not before cached receipt replay. Successful new provenance directories have their parent synced before provenance commit, including retries after a failed sync. Configured store ancestors are synced as well.
- Recovery requires the same org, actor, session, report ref, UID and exact operation hash, plus a successful completion receipt with the expected UID. It does not redispatch, and it retains the lock until processing is complete. Missing/invalid/ambiguous receipts stay fail-closed and need operator investigation.
- Replaying an older completed operation cannot roll provenance back after a newer revision. The current completed operation can still replay without a second writer call.
- Identity normalization rejects numeric-ID-only updates, mixed full-dashboard/patch forms, conflicting or non-string UIDs, and root/identity-changing patch paths. Full dashboards with a UID are sent without their redundant numeric ID. Resolver UID changes are rejected before writing.
- Patch paths must be descending paths beneath a named, non-identity top-level field. Dot notation, quoted names, numeric indices, descendant array wildcards and slash segments are supported. Parent traversal, filters and other complex path expressions are rejected; ordinary native dashboards can use full JSON for complex edits. This is an identity boundary, not a fixed panel/dataset/analysis workflow.

## Verification and evidence

All commands were run by the main agent with the local Go toolchain and `GOPROXY=off`:

- **RED:** lock-inode split and superseded-receipt replay tests both failed against surviving round-3 code: `.scratch/process-analysis-outcome/main-red.log`.
- **GREEN:** actual entrypoint tests with local fake MCP servers cover native/report concurrency in both directions, fresh revisions, same-org cross-session protection, other-org independence, missing/resolved/mismatched UIDs, numeric ID/patch identity rejection, publication, persistence failure and exact-input receipt recovery.
- A real subprocess acquires the UID lock; another process cannot acquire it. After killing the owner, its pending identity remains and the OS lock can be reacquired. This tests process termination, **not power loss**.
- An injected parent-sync failure verifies that provenance is not committed first, and an existing-directory retry still invokes the sync barrier. This is syscall ordering/error-propagation coverage, not hardware crash-durability certification.
- Selected boundary/lock/recovery tests repeated 20 times: `.scratch/process-analysis-outcome/main-repeat20.log`.
- Reconstructed all 16 installer patches in an isolated local clone, compared all changed source bytes, and ran `go test ./pkg/... -count=1`: `.scratch/process-analysis-outcome/main-reconstructed-tests.log`.
- `python3 scripts/check-ask-o11y-patch-stack.py`, primary Go LSP diagnostics, `git diff --check`, and no-staged-files checks passed.
- Rebuild/reproduction helper and original third-round patch backup: `.scratch/process-analysis-outcome/rebuild-main-patch.py`, `round3-before-main.patch`.

## Limits / remaining gates

- No new independent reviewer/security reviewer was launched under main-only instructions. Their previous failure report is not a review of this final source.
- No actual Grafana save/readback or authenticated browser/visual acceptance. External MCP/backend schema compatibility has not been revalidated against a live service; local tests enforce the host's bounded identity grammar.
- No `go test -race`: gcc is unavailable. Ordinary repeated and subprocess tests do not substitute for the race detector.
- File locking and fsync assume a persistent, trustworthy shared POSIX filesystem with working `flock`/directory sync semantics. Multi-host/NFS and Windows support were not established.
- Dashboards without preexisting server-owned provenance remain a legacy gap; editable chat history is never used to backfill ownership. External/manual Grafana writers are outside this host gate.
- A mismatched writer UID or absent completion evidence is an integrity/recovery error, not permission to redispatch. Automatic recovery requires the original exact request context.
- Trusted statistical-result evidence, actual process-analysis quality, and visual readability are separate unfinished work.
