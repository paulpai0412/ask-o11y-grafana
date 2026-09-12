# Local Ask O11y source

This directory is maintained directly in the parent Grafana Git repository.

- Upstream: <https://github.com/Consensys/ask-o11y-plugin>
- Imported commit: `8395ae10c3e38beae56329e4174a14a9a6d4c680`
- License: MIT; original notices and LICENSE are retained.
- Local design, scope, rollback and TODO: `../docs/design/ask-o11y-minimal-source.md`.

Do not regenerate this directory from patches or a scratch checkout. Keep build outputs and dependencies ignored. Build scripts must not delete, reset, clone over, or deploy this source by default.

Build from the repository root with `bash scripts/build-install-ask-o11y.sh`. Despite its retained name, this entrypoint now only builds; deployment is separate. It uses this source without patches and does not install tools or dependencies. The local verification run was explicitly approved on the existing Node 24.18.0 (upstream documentation still specifies Node 22).

The original upstream documentation is retained for reference; local runtime changes and verification are recorded in the design/TODO above. No new plugin ID or service is introduced. The MCP migration is not yet complete; a local build is not deployment acceptance.

The analyst default is `pkg/plugin/analyst_prompt.md`, embedded in Go and reused by the settings generator. `pkg/plugin/skills/analysis/SKILL.md` is appended by the existing prompt registry as advisory context, not selected by a new runtime. Saved nonempty custom prompts remain unchanged. Before any authorized settings migration, explicitly review whether to keep or replace an old prompt that still mentions retired tools; applying settings does not silently reset it.

Source checks are the existing Go build/vet, project TypeScript/build commands, OpenAPI validation and Python `py_compile`. They do not prove runtime behavior. The minimal-runtime fixture regression and Sandbox `--self-check` path are retired; do not use predefined Python, synthetic frames, mocked services or direct host RPC as UI/LLM acceptance.

The settings generator's `--local-defaults` option creates a local candidate for three MCP servers and gated writes; it is not an apply authorization. Do not apply settings or deploy until the outstanding runtime safeguards and separate authorization are resolved. Real acceptance must exercise Ask O11y's own UI/LLM with authorized data, real OpenSandbox execution, native figures and actual cancellation/operation receipts.
