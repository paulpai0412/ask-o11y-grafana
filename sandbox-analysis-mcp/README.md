# Sandbox Analysis MCP

Executes Ask O11y-generated Python against one authorized Grafana columnar frame in a fresh OpenSandbox Code Interpreter. It exposes no datasource client and never executes generated source on the MCP host.

## Tools

- `execute_python_analysis(frame_ref, python_code, seed?)`
- `list_python_analyses()`
- `inspect_python_analysis(provenance_ref)`
- `revise_python_analysis(provenance_ref, python_code, seed?)`

The query frame and validity rules are transferred as bounded JSON. Trusted `capture.py`, baked into the image, creates filtered `df` and captures named tables, bounded CSV outputs, JSON results, Matplotlib PNG, Plotly JSON, HTML, text, errors, and validity audit. Explicit text/JSON results are returned inline up to 32 KiB total; CSV outputs receive retention-bound signed download URLs. Display names never control physical paths. No CSV or SQLite input intermediate is created.

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

Run checks:

```bash
uv run python sandbox-analysis-mcp/server.py --self-check
uv run python scripts/run-sandbox-analysis-real-spike.py
MCP_SHARED_TOKEN='<same-token>' uv run python scripts/run-sandbox-analysis-http-revision-e2e.py
```

## Serving authorized image assets

`GET /assets/<signed-token>` validates the token and artifact retention before streaming stored bytes with their trusted MIME type; CSV responses use attachment disposition. It does not generate charts, HTML, or panel JSON. Tokens are created from authorized execution refs: the hidden Artifact Bridge creates PNG bindings, while Sandbox Analysis returns CSV download URLs.

For local Grafana use, the bridge defaults `ARTIFACT_PUBLIC_BASE` to `http://127.0.0.1:8777`. A non-local deployment must set it to the authenticated/TLS asset-gateway URL reachable by the Grafana user's browser.

## Production

- Configure OpenSandbox with gVisor or Kata; do not enable `SANDBOX_ALLOW_RUNC`.
- Pin and publish the custom image by digest.
- Enable OpenSandbox API authentication and set `SANDBOX_API_KEY` only in the MCP process; it is never injected into a sandbox.
- Keep deny-all egress, empty sandbox environment, no volumes, 1 CPU, 1 GiB memory, 10-minute lifetime, and bounded input/output.
- Put the signed asset endpoint behind TLS and set `ARTIFACT_PUBLIC_BASE` to its browser-reachable URL; do not use loopback outside local development.
