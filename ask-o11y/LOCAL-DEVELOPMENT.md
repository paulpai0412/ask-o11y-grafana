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

From the repository root, run `.venv/bin/python scripts/check-minimal-python-runtime.py` for the isolated MCP/figure/settings regression (no generated Python runs on the host). `scripts/configure-ask-o11y-workflow-tools.py --local-defaults --self-check` writes only a local candidate for three MCP servers and gated writes. Do not apply it or deploy until the outstanding cancellation/unknown-operation safeguards and the required authorization are complete.
