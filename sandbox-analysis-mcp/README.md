# Sandbox Analysis MCP

Executes Ask O11y-generated Python against one authorized Grafana columnar frame in a fresh OpenSandbox Code Interpreter. It exposes no datasource client and never executes generated source on the MCP host.

## Tools

- `execute_python_analysis(frame_ref, python_code, seed?)`
- `execute_python_preprocessing(document_ref, python_code, seed?)`
- `list_python_analyses()`
- `inspect_python_analysis(provenance_ref)`
- `revise_python_analysis(provenance_ref, python_code, seed?)`
- `reconcile_operation(operation_id?)` — inspect an owned receipt, or list recent session-owned operation statuses; never executes Python

An authorized query frame is transferred as bounded JSON; no analysis plan, fixed model template, validity rule, or report contract is required. Original uploaded CSV/XLSX documents use an owner/session-bound `document_ref`, without exposing a host path. `capture.py` creates `df` and captures the LLM-generated Python's original outputs and input audit. Native Plotly figures are referenced by `execution_ref` and `output_index`; the host resolves them for an approved Dashboard write without asking the model to copy their arrays.

Text/JSON results are returned inline up to 32 KiB total; CSV outputs receive retention-bound signed download URLs. `emit_frame(df)` returns a validated `derived_frame_ref`; document preprocessing also registers that output as a derived session dataset for later discovery/query. Display names never control physical paths. No CSV or SQLite frame-input intermediate is created. Only the current execution/provenance format is supported. Legacy report readers and fixed ML/profile/report generators are removed from the source and image recipes; analysis methods and code belong to the LLM. This source change neither migrates nor deletes stored artifacts, and does not change an already running image.

MCP calls carry the authenticated actor/application session and an initialized MCP session. Cancellation targets the owned call's sandbox. A cancellation request is not termination evidence: an unconfirmed outcome stays indeterminate and blocks replacement computation in that session, even with changed code. The local cancellation wiring still requires real UI/OpenSandbox acceptance.

The image pins NumPy, SciPy, pandas, Matplotlib, Seaborn, Plotly, scikit-learn, statsmodels, SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn, and Optuna. PyTorch and TensorFlow are intentionally omitted because their image and runtime cost is disproportionate for this bounded tabular-analysis service.

## Local integration

Local `runc` is for development only.

```bash
uvx opensandbox-server==0.2.2 --config config/opensandbox.local.toml

docker build -t ask-o11y-sandbox-analysis:dev sandbox-analysis-mcp
docker image inspect ask-o11y-sandbox-analysis:dev --format '{{index .RepoDigests 0}}'

export SANDBOX_IMAGE='ask-o11y-sandbox-analysis@sha256:<local-digest>'
export SANDBOX_RUNTIME_CLASS=runc
export SANDBOX_ALLOW_RUNC=1
export SANDBOX_SERVER_CONFIG="$PWD/config/opensandbox.local.toml"
export SANDBOX_DOMAIN=localhost:8080
export MCP_SHARED_TOKEN='<at-least-32-characters>'
export ANALYSIS_SERVICE_ORG_ID=1
export ANALYSIS_SERVICE_USER_ID=ask-o11y
uv run python sandbox-analysis-mcp/server.py
```

Source-only check (does not execute Python analysis or contact services):

```bash
.venv/bin/python -m py_compile sandbox-analysis-mcp/server.py artifact_store.py
```

The old `--self-check` fixture path is retired. Historical scripted spikes contain predefined data/code and are not acceptance for the minimal runtime. Acceptance requires the real Ask O11y UI, an authorized data source, actual LLM-generated code and real OpenSandbox results. Deployment, settings changes and live testing require separate authorization; see `../docs/design/ask-o11y-minimal-source.md`.

## Serving authorized image assets

`GET /assets/<signed-token>` validates the token and artifact retention before streaming stored bytes with their trusted MIME type; CSV responses use attachment disposition. It does not generate charts, HTML, or panel JSON. Tokens are created from authorized execution refs: the hidden Artifact Bridge creates PNG bindings, while Sandbox Analysis returns CSV download URLs.

For local Grafana use, the bridge defaults `ARTIFACT_PUBLIC_BASE` to `http://127.0.0.1:8777`. A non-local deployment must set it to the authenticated/TLS asset-gateway URL reachable by the Grafana user's browser.

## Production

- Configure OpenSandbox with gVisor or Kata; do not enable `SANDBOX_ALLOW_RUNC`.
- Pin and publish the custom image by digest.
- Enable OpenSandbox API authentication and set `SANDBOX_API_KEY` only in the MCP process; it is never injected into a sandbox.
- Keep deny-all egress, empty sandbox environment, no volumes and bounded input/output. Current source limits are 4 CPUs, 4 GiB memory and a one-hour lifetime.
- Put the signed asset endpoint behind TLS and set `ARTIFACT_PUBLIC_BASE` to its browser-reachable URL; do not use loopback outside local development.
