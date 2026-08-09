#!/usr/bin/env python3
"""Deterministic Finance Analysis MCP contract implementation.

Finance live Grafana/Ask O11y E2E is deliberately deferred. Tools consume only
authorized frame artifacts and never run an LLM, skill, shell, or datasource.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pandas as pd  # pyright: ignore[reportMissingImports]

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from analysis_core import AnalysisCoreError, dataframe_from_columnar_frame, deterministic_method_source, numeric_series, paired_statistics, visualization_spec  # noqa: E402  # pyright: ignore[reportMissingImports]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workflow_node = load_module("workflow_node", ROOT / "workflow_node.py")
artifact_store = load_module("artifact_store", ROOT / "artifact_store.py")
mcp_security = load_module("mcp_security", ROOT / "mcp_security.py")
ArtifactAuthError = artifact_store.ArtifactAuthError
authenticate_headers = mcp_security.authenticate_headers
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
ArtifactStore = artifact_store.ArtifactStore
WorkflowContractError = workflow_node.WorkflowContractError
parse_artifact_ref = workflow_node.parse_artifact_ref
success_response = workflow_node.success_response
error_response = workflow_node.error_response

try:
    PORT = int(os.environ.get("FINANCE_ANALYSIS_MCP_PORT", "8776"))
except ValueError:
    PORT = 8776
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
SERVER_INFO = {"name": "finance-analysis-mcp", "version": "0.1.0", "finance_real_e2e": False}
PROTOCOL = "2025-03-26"
FORBIDDEN_INPUT_KEYS = {
    "frame",
    "frames",
    "rows",
    "data",
    "datasource",
    "datasource_uid",
    "url",
    "sql",
    "rawSql",
    "query",
    "token",
    "password",
    "code",
    "script",
    "skill",
    "prompt",
    "mock",
    "canned_output",
}


def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    if org and user:
        return {"org_id": str(org), "user_id": str(user)}
    return None


def inject_header_context(msg: dict[str, Any], headers) -> dict[str, Any]:
    if msg.get("method") != "tools/call":
        return msg
    params = msg.setdefault("params", {})
    if not isinstance(params, dict):
        return msg
    args = params.setdefault("arguments", {})
    if not isinstance(args, dict):
        return msg
    args.pop("context", None)
    args.pop("_server_context", None)
    context = context_from_headers(headers)
    if context is not None:
        args["_server_context"] = context
    return msg


def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    raw = args.get("_server_context")
    if isinstance(raw, dict) and raw.get("org_id") and raw.get("user_id"):
        return {"org_id": str(raw["org_id"]), "user_id": str(raw["user_id"])}
    raise WorkflowContractError("verified artifact context is required")


def forbidden_input_error(args: dict[str, Any]) -> str | None:
    present = sorted(key for key in FORBIDDEN_INPUT_KEYS if key in args)
    if present:
        return "finance tools accept artifact refs and explicit finance options only; forbidden keys: " + ", ".join(present)
    return None


def read_frame(context: dict[str, str], frame_ref: str) -> tuple[str, dict[str, Any]]:
    run_id, parts = parse_artifact_ref(frame_ref)
    if parts != ("grafana-frame",):
        raise WorkflowContractError("frame_ref must reference a grafana-frame artifact")
    frames = ARTIFACTS.read_json(context, frame_ref)
    if not isinstance(frames, list) or not frames or not isinstance(frames[0], dict):
        raise WorkflowContractError("grafana-frame artifact must contain a non-empty frame list")
    return run_id, frames[0]


def validate_currency(value: Any) -> str:
    currency = str(value or "")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise WorkflowContractError("currency must be an uppercase ISO-style three-letter code")
    return currency


def scalar_float(value: Any, label: str) -> float:
    if isinstance(value, pd.Series):
        raise WorkflowContractError(f"{label} must be a scalar")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError(f"{label} must be a finite numeric scalar") from exc


def analyze_cost_drivers(args: dict[str, Any]) -> dict[str, Any]:
    step = "analyze_cost_drivers"
    forbidden = forbidden_input_error(args)
    if forbidden:
        return error_response(step=step, error=forbidden, recoverable=False, instruction="Stop; pass only frame_ref and explicit finance fields/options.")
    frame_ref, target, drivers, period_field = args.get("frame_ref"), args.get("target"), args.get("drivers"), args.get("fiscal_period_field")
    try:
        minimum_rows = int(args.get("minimum_rows", 12))
    except (TypeError, ValueError):
        minimum_rows = 0
    if not isinstance(frame_ref, str) or not isinstance(target, str) or not target:
        return error_response(step=step, error="frame_ref and target are required", recoverable=False, instruction="Stop; select an authorized frame and finance target.")
    if not isinstance(drivers, list) or not drivers or any(not isinstance(field, str) or not field for field in drivers):
        return error_response(step=step, error="drivers must contain at least one field", recoverable=False, instruction="Stop; select finance driver fields.")
    if target in drivers or len(set(drivers)) != len(drivers):
        return error_response(step=step, error="target and drivers must be unique", recoverable=False, instruction="Stop; correct the finance field selection.")
    if not isinstance(period_field, str) or not period_field or minimum_rows < 3:
        return error_response(step=step, error="fiscal_period_field is required and minimum_rows must be at least 3", recoverable=False, instruction="Stop; provide valid finance options.")
    try:
        currency = validate_currency(args.get("currency"))
        context = context_from_args(args)
        run_id, frame = read_frame(context, frame_ref)
        data = dataframe_from_columnar_frame(frame)
        if period_field not in data.columns:
            raise WorkflowContractError(f"unknown fiscal period field: {period_field}")
        target_values = numeric_series(data, target, minimum_rows)
        rows = []
        for driver in drivers:
            driver_values = numeric_series(data, driver, minimum_rows)
            stats = paired_statistics(target_values, driver_values, minimum_rows=minimum_rows, method="pearson")
            correlation = scalar_float(stats["correlation"], f"correlation for {driver}")
            sensitivity = scalar_float(stats["covariance"], f"covariance for {driver}") / scalar_float(stats["right_variance"], f"driver variance for {driver}")
            rows.append({"driver": driver, "correlation": correlation, "sensitivity": sensitivity, "paired_rows": int(stats["rows"])})
        rows.sort(key=lambda item: abs(item["correlation"]), reverse=True)
        source = deterministic_method_source(implementation="finance-analysis-mcp.v1", method="cost_driver_analysis", algorithm="pandas.pearson_correlation_and_univariate_sensitivity", packages=["pandas"], extra={"finance_real_e2e": False})
        method_result = {
            "method": "cost_driver_analysis",
            "parameters": {"target": target, "drivers": drivers, "fiscal_period_field": period_field, "currency": currency, "minimum_rows": minimum_rows},
            "metrics": {"input_rows": len(data), "target_non_null": int(target_values.count()), "driver_count": len(drivers)},
            "drivers": rows,
            "validation": {"ok": True, "checks": ["authorized frame_ref", "currency format", "fiscal period field", "numeric target/drivers", "minimum paired rows", "non-zero driver variance"]},
            "assumptions": ["Univariate sensitivity is descriptive and does not control for correlated drivers."],
            "limitations": ["Correlation does not establish causality.", "Currency conversion is not performed.", "Fiscal ordering is supplied by the source artifact."],
            "method_source": source,
        }
        method_ref = ARTIFACTS.write_json(context, run_id, "method-finance-cost-drivers", method_result)
        summary = f"Evaluated {len(drivers)} cost drivers for {target} in {currency}; strongest absolute correlation is {rows[0]['driver']} ({rows[0]['correlation']:.3f})."
        analysis = {
            "analysis_type": "finance_cost_drivers",
            "title": str(args.get("title") or "Finance Cost Driver Analysis"),
            "summary": summary,
            "severity": "info",
            "time_range": {"from": str(data[period_field].iloc[0]), "to": str(data[period_field].iloc[-1])},
            "subject": {"domain": "finance", "target": target, "currency": currency},
            "findings": [{"level": "info", "message": summary, "evidence": {"top_driver": rows[0]}}],
            "data_frames": [{"name": "cost_drivers", "schema": {"fields": [{"name": "driver", "type": "string"}, {"name": "correlation", "type": "number"}, {"name": "sensitivity", "type": "number"}]}, "data": {"values": [[row["driver"] for row in rows], [row["correlation"] for row in rows], [row["sensitivity"] for row in rows]]}}],
            "recommended_panels": [visualization_spec("bar", title="Finance Cost Driver Evidence", data_frame="cost_drivers", fields={"x": "driver", "y": ["correlation"]})],
            "details": {"method_result_refs": {"cost_drivers": method_ref}, "method_source": source, "domain_validation": {"currency": currency, "fiscal_period_field": period_field}, "finance_real_e2e": False},
        }
        analysis_ref = ARTIFACTS.write_json(context, run_id, "analysis-finance-cost-drivers", analysis)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; artifact context mismatch.")
    except (WorkflowContractError, AnalysisCoreError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; finance cost-driver contract failed.")
    return success_response(step=step, run_id=run_id, refs={"frame_ref": frame_ref, "method_result_ref": method_ref, "analysis_result_ref": analysis_ref}, instruction="Use the compact Finance result and refs to decide the next high-level tool; there is no mandatory next step.", evidence={"datasource_access": "none; consumed authorized frame artifact only", "finance_real_e2e": False}, analysis_result_ref=analysis_ref, method_result_ref=method_ref, preview={"analysis_type": analysis["analysis_type"], "summary": summary, "top_drivers": rows[:5], "visualization": analysis["recommended_panels"][0], "finance_real_e2e": False})


def analyze_variance(args: dict[str, Any]) -> dict[str, Any]:
    step = "analyze_variance"
    forbidden = forbidden_input_error(args)
    if forbidden:
        return error_response(step=step, error=forbidden, recoverable=False, instruction="Stop; pass only frame_ref and explicit finance fields/options.")
    frame_ref, actual_field, baseline_field, period_field = (args.get(key) for key in ("frame_ref", "actual_field", "baseline_field", "fiscal_period_field"))
    if not all(isinstance(value, str) and value for value in (frame_ref, actual_field, baseline_field, period_field)):
        return error_response(step=step, error="frame_ref, actual_field, baseline_field, and fiscal_period_field are required", recoverable=False, instruction="Stop; provide the finance variance fields.")
    if actual_field == baseline_field:
        return error_response(step=step, error="actual_field and baseline_field must differ", recoverable=False, instruction="Stop; correct the variance field selection.")
    try:
        currency = validate_currency(args.get("currency"))
        context = context_from_args(args)
        run_id, frame = read_frame(context, str(frame_ref))
        data = dataframe_from_columnar_frame(frame)
        if period_field not in data.columns:
            raise WorkflowContractError(f"unknown fiscal period field: {period_field}")
        actual = numeric_series(data, str(actual_field), 3)
        baseline = numeric_series(data, str(baseline_field), 3)
        valid = pd.DataFrame({"period": data[period_field], "actual": actual, "baseline": baseline}).dropna()
        if len(valid) < 3:
            raise WorkflowContractError("insufficient paired rows for variance analysis")
        valid["variance"] = valid["actual"] - valid["baseline"]
        valid["variance_pct"] = valid["variance"] / valid["baseline"].replace(0, pd.NA)
        source = deterministic_method_source(implementation="finance-analysis-mcp.v1", method="variance_analysis", algorithm="pandas.actual_minus_baseline", packages=["pandas"], extra={"finance_real_e2e": False})
        method_result = {"method": "variance_analysis", "parameters": {"actual_field": actual_field, "baseline_field": baseline_field, "fiscal_period_field": period_field, "currency": currency}, "metrics": {"paired_rows": len(valid), "total_actual": scalar_float(valid.loc[:, "actual"].sum(), "total actual"), "total_baseline": scalar_float(valid.loc[:, "baseline"].sum(), "total baseline"), "total_variance": scalar_float(valid.loc[:, "variance"].sum(), "total variance")}, "validation": {"ok": True, "checks": ["authorized frame_ref", "currency format", "paired numeric rows"]}, "assumptions": ["Actual and baseline fields use the same currency and accounting basis."], "limitations": ["No currency conversion or inflation adjustment is performed."], "method_source": source}
        method_ref = ARTIFACTS.write_json(context, run_id, "method-finance-variance", method_result)
        rows = valid.to_dict(orient="records")
        summary = f"Computed {len(rows)} fiscal-period variances in {currency}; total variance is {method_result['metrics']['total_variance']:.2f}."
        analysis = {"analysis_type": "finance_variance", "title": str(args.get("title") or "Finance Variance Analysis"), "summary": summary, "severity": "info", "time_range": {"from": str(rows[0]["period"]), "to": str(rows[-1]["period"])}, "subject": {"domain": "finance", "currency": currency}, "findings": [{"level": "info", "message": summary}], "data_frames": [{"name": "finance_variance", "schema": {"fields": [{"name": "period", "type": "string"}, {"name": "actual", "type": "number"}, {"name": "baseline", "type": "number"}, {"name": "variance", "type": "number"}, {"name": "variance_pct", "type": "number"}]}, "data": {"values": [[row["period"] for row in rows], [row["actual"] for row in rows], [row["baseline"] for row in rows], [row["variance"] for row in rows], [row["variance_pct"] for row in rows]]}}], "recommended_panels": [visualization_spec("bar", title="Actual vs Baseline", data_frame="finance_variance", fields={"x": "period", "y": ["actual", "baseline"]}), visualization_spec("table", title="Variance Detail", data_frame="finance_variance", fields={})], "details": {"method_result_refs": {"variance": method_ref}, "method_source": source, "domain_validation": {"currency": currency, "fiscal_period_field": period_field}, "finance_real_e2e": False}}
        analysis_ref = ARTIFACTS.write_json(context, run_id, "analysis-finance-variance", analysis)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; artifact context mismatch.")
    except (WorkflowContractError, AnalysisCoreError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; finance variance contract failed.")
    return success_response(step=step, run_id=run_id, refs={"frame_ref": str(frame_ref), "method_result_ref": method_ref, "analysis_result_ref": analysis_ref}, instruction="Use the compact Finance result and refs to decide the next high-level tool; there is no mandatory next step.", evidence={"datasource_access": "none; consumed authorized frame artifact only", "finance_real_e2e": False}, analysis_result_ref=analysis_ref, method_result_ref=method_ref, preview={"analysis_type": analysis["analysis_type"], "summary": summary, "visualizations": analysis["recommended_panels"], "finance_real_e2e": False})


TOOLS = [
    {"name": "analyze_cost_drivers", "description": "Deterministically evaluate explicit Finance target/driver fields from an authorized frame artifact using correlations and univariate sensitivities. Use for cost/revenue driver screening. Does not query data, train an arbitrary model, forecast, or execute trades.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "target": {"type": "string"}, "drivers": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "fiscal_period_field": {"type": "string"}, "currency": {"type": "string", "pattern": "^[A-Z]{3}$"}, "minimum_rows": {"type": "integer", "minimum": 3}, "title": {"type": "string"}}, "required": ["frame_ref", "target", "drivers", "fiscal_period_field", "currency"]}},
    {"name": "analyze_variance", "description": "Deterministically compare explicit actual and baseline Finance fields by fiscal period from an authorized frame artifact. Does not query data, forecast, optimize a portfolio, or execute trades.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "actual_field": {"type": "string"}, "baseline_field": {"type": "string"}, "fiscal_period_field": {"type": "string"}, "currency": {"type": "string", "pattern": "^[A-Z]{3}$"}, "title": {"type": "string"}}, "required": ["frame_ref", "actual_field", "baseline_field", "fiscal_period_field", "currency"]}},
]


def rpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_rpc(msg: dict[str, Any]):
    method, rid = msg.get("method", ""), msg.get("id")
    if method == "initialize":
        return rpc_result(rid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return rpc_result(rid, {})
    if method == "tools/list":
        return rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        name, args = params.get("name", ""), params.get("arguments", {}) or {}
        if name not in {tool["name"] for tool in TOOLS}:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        if not isinstance(args, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        tool = next(tool for tool in TOOLS if tool["name"] == name)
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context"}
        unexpected = sorted(set(args) - allowed)
        if unexpected:
            out = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only arguments declared by this tool schema.")
        elif name == "analyze_cost_drivers":
            out = analyze_cost_drivers(args)
        else:
            out = analyze_variance(args)
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": not out.get("ok", False)})
    if rid is None:
        return None
    return rpc_error(rid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: Any = None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self._send(405 if self.path.rstrip("/") == "/mcp" else 404, {"error": "POST JSON-RPC to /mcp"})

    def do_DELETE(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            return self._send(404, {"error": "not found"})
        if authenticate_headers(self.headers) is None:
            return self._send(401, {"error": "authenticated MCP service identity is required"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except Exception:
            return self._send(400, rpc_error(None, -32700, "parse error"))
        messages = payload if isinstance(payload, list) else [payload]
        replies = [reply for message in messages if (reply := handle_rpc(inject_header_context(message, self.headers))) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):  # noqa: A002
        sys.stderr.write("finance-analysis-mcp " + format % args + "\n")


def self_check() -> None:
    global ARTIFACTS
    old_store = ARTIFACTS
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        context = {"org_id": "1", "user_id": "finance-self-check"}
        periods = [f"2026-{month:02d}" for month in range(1, 13)]
        baseline = [1000 + month * 20 for month in range(12)]
        actual = [value + (-40 if month % 3 == 0 else 80) for month, value in enumerate(baseline)]
        volume = [100 + month * 3 for month in range(12)]
        labor = [400 + month * 8 for month in range(12)]
        frame = {"schema": {"fields": [{"name": "fiscal_period"}, {"name": "actual_cost"}, {"name": "budget_cost"}, {"name": "volume"}, {"name": "labor_cost"}]}, "data": {"values": [periods, actual, baseline, volume, labor]}}
        run_id = ARTIFACTS.create_run(context, "run_finance01")
        frame_ref = ARTIFACTS.write_json(context, run_id, "grafana-frame", [frame])
        drivers = analyze_cost_drivers({"frame_ref": frame_ref, "target": "actual_cost", "drivers": ["volume", "labor_cost"], "fiscal_period_field": "fiscal_period", "currency": "USD", "_server_context": context})
        variance = analyze_variance({"frame_ref": frame_ref, "actual_field": "actual_cost", "baseline_field": "budget_cost", "fiscal_period_field": "fiscal_period", "currency": "USD", "_server_context": context})
        invalid_currency = analyze_cost_drivers({"frame_ref": frame_ref, "target": "actual_cost", "drivers": ["volume"], "fiscal_period_field": "fiscal_period", "currency": "usd", "_server_context": context})
        direct_datasource = analyze_variance({"frame_ref": frame_ref, "actual_field": "actual_cost", "baseline_field": "budget_cost", "fiscal_period_field": "fiscal_period", "currency": "USD", "datasource_uid": "forbidden", "_server_context": context})
        old_org, old_user = os.environ.pop("ANALYSIS_CONTEXT_ORG_ID", None), os.environ.pop("ANALYSIS_CONTEXT_USER_ID", None)
        try:
            unauthorized = analyze_variance({"frame_ref": frame_ref, "actual_field": "actual_cost", "baseline_field": "budget_cost", "fiscal_period_field": "fiscal_period", "currency": "USD", "_server_context": {"org_id": "1", "user_id": "other"}})
        finally:
            if old_org is not None:
                os.environ["ANALYSIS_CONTEXT_ORG_ID"] = old_org
            if old_user is not None:
                os.environ["ANALYSIS_CONTEXT_USER_ID"] = old_user
        for name, result in {"drivers": drivers, "variance": variance}.items():
            if not result.get("ok"):
                raise RuntimeError(f"{name} self-check failed: {result}")
        for name, result in {"invalid_currency": invalid_currency, "direct_datasource": direct_datasource, "unauthorized": unauthorized}.items():
            if result.get("ok"):
                raise RuntimeError(f"negative should fail: {name} {result}")
        method = ARTIFACTS.read_json(context, drivers["method_result_ref"])
        flags = [method["method_source"].get(key) for key in ["runtime_agent", "runtime_llm", "runtime_skill"]]
        if not all(isinstance(flag, bool) and not flag for flag in flags):
            raise RuntimeError("Finance runtime provenance is invalid")
        print(json.dumps({"ok": True, "tools": ["analyze_cost_drivers", "analyze_variance"], "finance_real_e2e": False, "runtime_agent": False, "negative_checks": ["invalid_currency", "direct_datasource", "unauthorized"]}, ensure_ascii=False, indent=2))
    ARTIFACTS = old_store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    require_runtime_token()
    require_service_identity()
    bind_host = runtime_bind_host()
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} on {bind_host}:{PORT}", file=sys.stderr)
    ThreadingHTTPServer((bind_host, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
