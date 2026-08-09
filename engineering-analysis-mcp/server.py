#!/usr/bin/env python3
"""Deterministic Engineering Analysis MCP.

Tools consume authorized Grafana frame artifacts and explicit options. They do
not query datasources, run an LLM/skill, execute code, or choose a fixed flow.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from analysis_core import AnalysisCoreError, dataframe_from_columnar_frame, deterministic_method_source, pairwise_correlation  # noqa: E402  # pyright: ignore[reportMissingImports]
from analysis_core.correlation import CorrelationMethod  # noqa: E402  # pyright: ignore[reportMissingImports]


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
engineering_methods = load_module("engineering_methods", HERE / "methods.py")
authenticate_headers = mcp_security.authenticate_headers
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
ArtifactAuthError = artifact_store.ArtifactAuthError
ArtifactStore = artifact_store.ArtifactStore
WorkflowContractError = workflow_node.WorkflowContractError
parse_artifact_ref = workflow_node.parse_artifact_ref
success_response = workflow_node.success_response
error_response = workflow_node.error_response

try:
    PORT = int(os.environ.get("ENGINEERING_ANALYSIS_MCP_PORT", "8775"))
except ValueError:
    PORT = 8775
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
SERVER_INFO = {"name": "engineering-analysis-mcp", "version": "0.1.0"}
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
    "fixture",
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
        return "engineering tools accept artifact refs and explicit analysis options only; forbidden keys: " + ", ".join(present)
    return None


def read_frame(context: dict[str, str], frame_ref: str) -> tuple[str, dict[str, Any]]:
    run_id, parts = parse_artifact_ref(frame_ref)
    if parts != ("grafana-frame",):
        raise WorkflowContractError("frame_ref must reference a grafana-frame artifact")
    frames = ARTIFACTS.read_json(context, frame_ref)
    if not isinstance(frames, list) or not frames or not isinstance(frames[0], dict):
        raise WorkflowContractError("grafana-frame artifact must contain a non-empty frame list")
    return run_id, frames[0]


def apply_run_validity(context: dict[str, str], run_id: str, data: Any, args: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    try:
        plan = ARTIFACTS.read_json(context, f"artifact://{run_id}/query-plan")
    except WorkflowContractError:
        plan = {}
    contract = plan.get("analysis_input_contract") if isinstance(plan, dict) else {}
    rules = contract.get("validity_rules", []) if isinstance(contract, dict) else []
    return engineering_methods.apply_validity_rules(data, args, rules)


def correlation_analysis(args: dict[str, Any]) -> dict[str, Any]:
    step = "analyze_correlation"
    forbidden = forbidden_input_error(args)
    if forbidden:
        return error_response(step=step, error=forbidden, recoverable=False, instruction="Stop; pass only frame_ref, fields, and explicit correlation options.")
    frame_ref = args.get("frame_ref")
    fields = args.get("fields")
    method = str(args.get("method") or "pearson")
    try:
        min_rows = int(args.get("minimum_rows", 20))
    except (TypeError, ValueError):
        min_rows = 0
    if not isinstance(frame_ref, str):
        return error_response(step=step, error="frame_ref is required", recoverable=False, instruction="Stop; call Grafana Query first.")
    if not isinstance(fields, list) or len(fields) < 2 or any(not isinstance(field, str) or not field for field in fields):
        return error_response(step=step, error="fields must contain at least two field names", recoverable=False, instruction="Stop; select numeric fields from authorized datasource metadata.")
    if len(set(fields)) != len(fields):
        return error_response(step=step, error="fields must be unique", recoverable=False, instruction="Stop; remove duplicate fields.")
    if method not in {"pearson", "spearman"}:
        return error_response(step=step, error="method must be pearson or spearman", recoverable=False, instruction="Stop; choose a supported deterministic correlation method.")
    if min_rows < 3:
        return error_response(step=step, error="minimum_rows must be at least 3", recoverable=False, instruction="Stop; choose a valid minimum_rows value.")
    try:
        context = context_from_args(args)
        run_id, frame = read_frame(context, frame_ref)
        data = dataframe_from_columnar_frame(frame)
        data, validity = apply_run_validity(context, run_id, data, args)
        correlation = pairwise_correlation(data, fields, method=cast(CorrelationMethod, method), minimum_rows=min_rows)
        cells = correlation["cells"]
        pairs = correlation["pairs"]
        non_null = correlation["non_null_rows"]
        source = deterministic_method_source(implementation="engineering-analysis-mcp.v1", method="pairwise_correlation", algorithm=f"pandas.paired_correlation.{method}", packages=["pandas"])
        method_result = {
            "method": "pairwise_correlation",
            "parameters": {"fields": fields, "method": method, "minimum_rows": min_rows},
            "metrics": {"input_rows": len(data), "field_count": len(fields), "pair_count": len(pairs), "non_null_rows": non_null},
            "matrix": {"fields": fields, "values": correlation["matrix"]},
            "pairs": pairs,
            "validation": {"ok": True, "checks": ["authorized frame_ref", "unique selected fields", "numeric coercion", "minimum pairwise rows", "finite correlation matrix"]},
            "assumptions": [f"{method.title()} correlation is appropriate for the requested relationship screening."],
            "limitations": ["Correlation does not establish causality.", "Missing values are handled pairwise by pandas.", "Operational regimes and time dependence can confound coefficients."],
            "method_source": source,
            "validity": validity,
        }
        method_ref = ARTIFACTS.write_json(context, run_id, "method-engineering-correlation", method_result)
        strongest = pairs[: min(5, len(pairs))]
        summary = f"Computed a {len(fields)}×{len(fields)} {method} correlation matrix from {len(data)} Grafana rows. 相關性不是因果。"
        frame_result = {
            "name": "correlation_matrix",
            "schema": {"fields": [{"name": "source", "type": "string"}, {"name": "target", "type": "string"}, {"name": "correlation", "type": "number"}]},
            "data": {"values": [[cell["source"] for cell in cells], [cell["target"] for cell in cells], [cell["correlation"] for cell in cells]]},
        }
        analysis = {
            "analysis_type": "engineering_correlation",
            "title": str(args.get("title") or "Engineering Parameter Correlation"),
            "summary": summary,
            "severity": "info",
            "time_range": {"from": None, "to": None},
            "subject": {"domain": "engineering", "fields": fields},
            "findings": [{"level": "info", "message": summary, "evidence": {"strongest_pairs": strongest}}],
            "data_frames": [frame_result],
            "recommended_panels": [{"title": str(args.get("visualization_title") or "Parameter Correlation Heatmap"), "type": "heatmap", "plugin_id": "esnet-matrix-panel", "data_frame": "correlation_matrix", "source": "source", "target": "target", "value": "correlation"}],
            "details": {"method_result_refs": {"correlation": method_ref}, "method_source": source, "strongest_pairs": strongest, "validity": validity, "domain_validation": {"selected_fields": fields, "non_null_rows": non_null}},
        }
        analysis_ref = ARTIFACTS.write_json(context, run_id, "analysis-engineering-correlation", analysis)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; artifact context mismatch.")
    except (WorkflowContractError, AnalysisCoreError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; engineering correlation contract failed.")
    return success_response(
        step=step,
        run_id=run_id,
        refs={"frame_ref": frame_ref, "method_result_ref": method_ref, "analysis_result_ref": analysis_ref},
        instruction="Use the compact preview and artifact refs to decide the next high-level tool; there is no mandatory next step.",
        evidence={"method_result_ref": method_ref, "analysis_result_ref": analysis_ref, "datasource_access": "none; consumed authorized Grafana frame artifact only"},
        analysis_result_ref=analysis_ref,
        method_result_ref=method_ref,
        preview={"analysis_type": analysis["analysis_type"], "summary": summary, "strongest_pairs": strongest, "validity": validity, "visualization": analysis["recommended_panels"][0]},
    )


def high_level_analysis(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    forbidden = forbidden_input_error(args)
    if forbidden:
        return error_response(step=tool_name, error=forbidden, recoverable=False, instruction="Stop; pass only an authorized frame_ref and explicit analysis options.")
    frame_ref = args.get("frame_ref")
    if not isinstance(frame_ref, str):
        return error_response(step=tool_name, error="frame_ref is required", recoverable=False, instruction="Stop; call Grafana Query first.")
    try:
        context = context_from_args(args)
        run_id, frame = read_frame(context, frame_ref)
        data = dataframe_from_columnar_frame(frame)
        data, validity = apply_run_validity(context, run_id, data, args)
        output = engineering_methods.run(tool_name, data, args)
        artifact_name = str(output["artifact_name"])
        method_result = cast(dict[str, Any], output["method_result"])
        analysis = cast(dict[str, Any], output["analysis"])
        method_result["validity"] = validity
        preview = output.get("preview")
        if isinstance(preview, dict):
            preview["validity"] = validity
        method_ref = ARTIFACTS.write_json(context, run_id, f"method-engineering-{artifact_name}", method_result)
        details = analysis.get("details")
        if not isinstance(details, dict):
            raise WorkflowContractError("analysis details are required")
        details["method_result_refs"] = {artifact_name: method_ref}
        details["validity"] = validity
        analysis_ref = ARTIFACTS.write_json(context, run_id, f"analysis-engineering-{artifact_name}", analysis)
    except ArtifactAuthError as exc:
        return error_response(step=tool_name, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; artifact context mismatch.")
    except (WorkflowContractError, AnalysisCoreError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=tool_name, error=str(exc), recoverable=False, instruction="Stop; engineering analysis contract failed.")
    return success_response(step=tool_name, run_id=run_id, refs={"frame_ref": frame_ref, "method_result_ref": method_ref, "analysis_result_ref": analysis_ref}, instruction="Use only this selected deterministic result to decide whether rendering or a revised preview is appropriate; there is no mandatory next step.", evidence={"datasource_access": "none; consumed authorized Grafana frame artifact only", "selected_method": method_result["method"]}, analysis_result_ref=analysis_ref, method_result_ref=method_ref, preview=output["preview"])


TOOLS = [
    {
        "name": "analyze_profile",
        "description": "Compute deterministic Engineering EDA/profile summaries for explicitly selected fields from an authorized Grafana frame. Returns row counts, missingness, cardinality, inferred numeric/categorical kind, descriptive statistics, limitations, and a table visualization; it does not train a model.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "title": {"type": "string"}}, "required": ["frame_ref", "fields"]},
    },
    {
        "name": "analyze_correlation",
        "description": "Compute a deterministic pairwise Pearson or Spearman correlation matrix for explicitly selected engineering fields from an authorized Grafana frame artifact. Use for relationship screening or correlation heatmaps; it does not forecast, detect anomalies, or train a model.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}, "minItems": 2, "uniqueItems": True}, "method": {"type": "string", "enum": ["pearson", "spearman"]}, "minimum_rows": {"type": "integer", "minimum": 3}, "title": {"type": "string"}, "visualization_title": {"type": "string"}}, "required": ["frame_ref", "fields"]},
    },
    {
        "name": "analyze_predictive",
        "description": "Fit and evaluate one explicitly requested deterministic engineering regression or classification model on an authorized Grafana frame. Returns held-out metrics, predictions, feature importance, limitations, and bar/scatter/table specs. Use only after preview approval; no datasource access or dashboard write.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "target": {"type": "string"}, "features": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "task": {"type": "string", "enum": ["regression", "classification"]}, "model_family": {"type": "string", "enum": ["linear", "random_forest"]}, "test_fraction": {"type": "number", "minimum": 0.1, "maximum": 0.4}, "seed": {"type": "integer"}, "time_field": {"type": "string"}, "title": {"type": "string"}}, "required": ["frame_ref", "target", "features", "task", "model_family"]},
    },
    {
        "name": "analyze_patterns",
        "description": "Run only the selected deterministic clustering and/or anomaly operations over explicit engineering features from an authorized Grafana frame. Returns assignments/scores and limitations; it does not forecast or train a supervised target model.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "features": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "operations": {"type": "array", "items": {"type": "string", "enum": ["clustering", "anomaly"]}, "minItems": 1, "uniqueItems": True}, "clusters": {"type": "integer", "minimum": 2, "maximum": 10}, "contamination": {"type": "number", "minimum": 0.001, "maximum": 0.25}, "seed": {"type": "integer"}, "title": {"type": "string"}}, "required": ["frame_ref", "features", "operations"]},
    },
    {
        "name": "analyze_timeseries",
        "description": "Run only selected deterministic trend, forecast, and/or anomaly operations for an authorized engineering time series. Trend renders explicit field groups against time without fitting a model; forecast uses chronological holdout evaluation; anomaly uses explicit fields and a deterministic seed. Returns dynamic timeseries specs and does not invoke unselected methods. target is required only for forecast; trend_groups is required only for trend.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"frame_ref": {"type": "string"}, "target": {"type": "string"}, "time_field": {"type": "string"}, "operations": {"type": "array", "items": {"type": "string", "enum": ["trend", "forecast", "anomaly"]}, "minItems": 1, "uniqueItems": True}, "trend_groups": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "object", "additionalProperties": False, "properties": {"fields": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 10, "uniqueItems": True}, "title": {"type": "string"}}, "required": ["fields"]}}, "anomaly_features": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "horizon": {"type": "integer", "minimum": 1, "maximum": 365}, "contamination": {"type": "number", "minimum": 0.001, "maximum": 0.25}, "seed": {"type": "integer"}, "title": {"type": "string"}}, "required": ["frame_ref", "time_field", "operations"]},
    },
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
        name = params.get("name", "")
        if name not in {tool["name"] for tool in TOOLS}:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        tool = next(tool for tool in TOOLS if tool["name"] == name)
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context"}
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            out = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only arguments declared by this tool schema.")
        else:
            out = correlation_analysis(arguments) if name == "analyze_correlation" else high_level_analysis(name, arguments)
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
        sys.stderr.write("engineering-analysis-mcp " + format % args + "\n")


def self_check() -> None:
    global ARTIFACTS
    old_store = ARTIFACTS
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        context = {"org_id": "1", "user_id": "engineering-self-check"}
        run_id = ARTIFACTS.create_run(context, "run_engineering01")
        frame = {
            "schema": {"fields": [{"name": "date"}, {"name": "pressure"}, {"name": "temperature"}, {"name": "flow"}, {"name": "state"}]},
            "data": {"values": [[f"2026-01-{value:02d}" for value in range(1, 31)], list(range(1, 31)), [value * 2 for value in range(1, 31)], [60 - value for value in range(1, 31)], ["high" if value > 15 else "low" for value in range(1, 31)]]},
        }
        frame_ref = ARTIFACTS.write_json(context, run_id, "grafana-frame", [frame])
        good = correlation_analysis({"frame_ref": frame_ref, "fields": ["pressure", "temperature", "flow"], "_server_context": context})
        profile = high_level_analysis("analyze_profile", {"frame_ref": frame_ref, "fields": ["pressure", "temperature", "state"], "_server_context": context})
        predictive = high_level_analysis("analyze_predictive", {"frame_ref": frame_ref, "target": "pressure", "features": ["temperature", "flow"], "task": "regression", "model_family": "linear", "time_field": "date", "_server_context": context})
        patterns = high_level_analysis("analyze_patterns", {"frame_ref": frame_ref, "features": ["pressure", "temperature", "flow"], "operations": ["anomaly"], "_server_context": context})
        timeseries = high_level_analysis("analyze_timeseries", {"frame_ref": frame_ref, "target": "pressure", "time_field": "date", "operations": ["forecast"], "horizon": 5, "_server_context": context})
        trends = high_level_analysis("analyze_timeseries", {"frame_ref": frame_ref, "time_field": "date", "operations": ["trend"], "trend_groups": [{"fields": ["pressure", "temperature"]}, {"fields": ["pressure", "flow"], "title": "Pressure and Flow"}], "_server_context": context})
        raw = correlation_analysis({"frame_ref": frame_ref, "fields": ["pressure", "flow"], "frames": [frame], "_server_context": context})
        bad_field = correlation_analysis({"frame_ref": frame_ref, "fields": ["pressure", "missing"], "_server_context": context})
        direct_datasource = correlation_analysis({"frame_ref": frame_ref, "fields": ["pressure", "flow"], "datasource_uid": "forbidden", "_server_context": context})
        old_org, old_user = os.environ.pop("ANALYSIS_CONTEXT_ORG_ID", None), os.environ.pop("ANALYSIS_CONTEXT_USER_ID", None)
        try:
            unauthorized = correlation_analysis({"frame_ref": frame_ref, "fields": ["pressure", "flow"], "_server_context": {"org_id": "1", "user_id": "other"}})
        finally:
            if old_org is not None:
                os.environ["ANALYSIS_CONTEXT_ORG_ID"] = old_org
            if old_user is not None:
                os.environ["ANALYSIS_CONTEXT_USER_ID"] = old_user
        for name, result in {"profile": profile, "correlation": good, "predictive": predictive, "patterns": patterns, "timeseries": timeseries, "trends": trends}.items():
            if not result.get("ok"):
                raise RuntimeError(f"{name} self-check failed: {result}")
        for name, result in {"raw": raw, "bad_field": bad_field, "direct_datasource": direct_datasource, "unauthorized": unauthorized}.items():
            if result.get("ok"):
                raise RuntimeError(f"negative should fail: {name} {result}")
        if profile["preview"].get("profile", {}).get("columns") != 3 or profile["preview"].get("visualizations", [{}])[0].get("type") != "table":
            raise RuntimeError(f"profile capability missing: {profile}")
        source = good["preview"]["visualization"]
        if source.get("type") != "heatmap":
            raise RuntimeError(f"heatmap visualization missing: {source}")
        method = ARTIFACTS.read_json(context, good["method_result_ref"])
        runtime_agent = method.get("method_source", {}).get("runtime_agent")
        if not isinstance(runtime_agent, bool) or runtime_agent:
            raise RuntimeError("runtime_agent provenance must be false")
        if patterns["preview"].get("operations") != ["anomaly"] or timeseries["preview"].get("operations") != ["forecast"] or trends["preview"].get("operations") != ["trend"] or len(trends["preview"].get("visualizations", [])) != 2:
            raise RuntimeError("selected time-series operations or dynamic trend panels are incorrect")
        print(json.dumps({"ok": True, "tools": [tool["name"] for tool in TOOLS], "analysis_result_ref": good["analysis_result_ref"], "selected_operations": {"patterns": patterns["preview"]["operations"], "timeseries": timeseries["preview"]["operations"], "trends": trends["preview"]["operations"]}, "trend_panel_count": len(trends["preview"]["visualizations"]), "negative_checks": ["raw_frame", "invalid_field", "direct_datasource", "unauthorized"], "runtime_agent": False}, ensure_ascii=False, indent=2))
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
