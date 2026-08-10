# Sandbox Analysis MCP

Executes Ask O11y-generated Python against one authorized Grafana columnar frame in a fresh OpenSandbox Code Interpreter. It exposes no datasource client and never executes generated source on the MCP host.

## Tools

- `execute_python_analysis(frame_ref, python_code, seed?)`
- `list_python_analyses()`
- `inspect_python_analysis(provenance_ref)`
- `revise_python_analysis(provenance_ref, python_code, seed?)`

The query frame and validity rules are transferred as bounded JSON. Trusted `capture.py`, baked into the image, creates filtered `df` and captures tables, Matplotlib PNG, Plotly JSON, HTML, text, errors, and validity audit. No CSV or SQLite intermediate is created.

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
```

## Production

- Configure OpenSandbox with gVisor or Kata; do not enable `SANDBOX_ALLOW_RUNC`.
- Pin and publish the custom image by digest.
- Enable OpenSandbox API authentication and set `SANDBOX_API_KEY` only in the MCP process; it is never injected into a sandbox.
- Keep deny-all egress, empty sandbox environment, no volumes, 1 CPU, 1 GiB memory, 120-second lifetime, and bounded input/output.
