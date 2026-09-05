# Uploaded dataset context — test plans

Status: V2 attachment exists; end-to-end isolation and release acceptance remain pending under [NLAP-02/03/12](natural-language-analysis-platform.md).

## Original problem

The original upload flow placed the exact `dataset_id` only in the input prompt. Replacing that text removed the dataset identity, causing `inspect_dataset` to fail closed with `dataset_id is required`. V2 addresses this attachment problem, not every downstream authorization boundary.

No test or implementation may invent an ID or infer one from filenames.

## Versions

### V1 — UI prompt-preservation tracer

**Seam:** upload callback → input message composer.

- Compose user instructions by appending to the upload-generated message.
- Assert the exact `upload_<32 hex>` ID and row/column metadata remain unchanged.
- Assert arbitrary analysis instructions are preserved.
- Fast unit test; known ceiling: users can still delete/replace the text manually.

### V2 — structured session attachment (implemented)

**Seam:** upload session → `runAgentDetached` → backend agent run.

- The upload proxy stores the validated `dataset_id` on the owned chat session; the in-memory and Redis stores persist the attachment.
- The backend reads that attachment into `LoopRequest`, and the agent binds it to `inspect_dataset` without relying on free-text wording.
- A supplied foreign `dataset_id` is rejected; upload/delete clear or replace the attachment only for the owned session.
- The current host binds the attachment specifically to `inspect_dataset` and forwards the trusted session header on the Grafana Query path. This is not proof that Sandbox or artifact access is session-isolated.

The attachment no longer depends on prompt wording. The current implementation is tool-specific; full immutable actor/session propagation is pending NLAP-02. ArtifactStore currently authorizes org/user scope, and cross-conversation reuse must be distinguished from session-private access.

### V3 — instrumented end-to-end acceptance

**Seam:** browser upload → custom prompt replacement → `inspect_dataset` → planner → query.

- Instrument the MCP boundary and assert the exact authorized ID, org, user, and session are present.
- Run one success case with full metadata and one fail-closed case without metadata.
- Verify no ID is hardcoded or inferred from filename.
- Run after V2; slower and reserved for release verification.
- Cover Sandbox, artifact/report reads and writer, not only inspect/planner/query. Reject foreign/stale sessions; permit cross-conversation reuse only through an explicit trusted grant.
- Force concurrent A/B reconnect interleavings and prove no shared MCP connection changes actor/session identity.
- Reject individually authorized but mismatched plan/frame refs before execution (NLAP-03).
- Preserve original run/session/tool events; do not merge continuation summaries into a fresh-session acceptance claim (NLAP-01).

## TDD order

1. V1 red test, then minimal green composer.
2. V2 red test for structured attachment propagation, then implement the smallest session metadata path.
3. V3 acceptance verification remains release-only; the live Vestas E2E still exercises the authorized session path.

## Acceptance criteria

- `inspect_dataset` receives the uploaded dataset identity after the user replaces the visible prompt text.
- Authorization remains bound to the current org/user/session.
- Missing or stale metadata fails closed.
- No hardcoded dataset IDs, filename lookup, or tool-specific guessing.
