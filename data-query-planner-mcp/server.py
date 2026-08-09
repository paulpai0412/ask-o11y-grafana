#!/usr/bin/env python3
"""Data Query Planner MCP.

Plans datasource queries and validates metadata/profile boundaries. It never
executes datasource queries; Grafana Query owns execution.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


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
authenticate_headers = mcp_security.authenticate_headers
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
ArtifactAuthError = artifact_store.ArtifactAuthError
ArtifactStore = artifact_store.ArtifactStore
parse_artifact_ref = workflow_node.parse_artifact_ref
clarification_response = workflow_node.clarification_response
error_response = workflow_node.error_response
success_response = workflow_node.success_response

try:
    PORT = int(os.environ.get("DATA_QUERY_PLANNER_MCP_PORT", "8768"))
except ValueError:
    PORT = 8768
SERVER_INFO = {"name": "data-query-planner-mcp", "version": "0.2.0"}
PROTOCOL = "2025-03-26"
MAX_PLAN_ROWS = 5_000
MAX_PLAN_FIELDS = 50
MAX_PLAN_RESPONSE_BYTES = 4 * 1024 * 1024
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()



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
    # Never trust caller-supplied identity. Strip visible/spoofable context keys;
    # only server-side env or transport headers may establish artifact identity.
    args.pop("context", None)
    args.pop("_server_context", None)
    context = context_from_headers(headers)
    if context is not None:
        args["_server_context"] = context
    return msg

def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    raw_context = args.get("_server_context")
    if isinstance(raw_context, dict) and raw_context.get("org_id") and raw_context.get("user_id"):
        return {"org_id": str(raw_context["org_id"]), "user_id": str(raw_context["user_id"])}
    raise workflow_node.WorkflowContractError("verified artifact context is required")


def bounded_metadata_time_range(metadata: dict[str, Any]) -> dict[str, str]:
    raw = metadata.get("date_range")
    if not isinstance(raw, dict):
        raise workflow_node.WorkflowContractError("authorized metadata must include a bounded date_range")
    start, end = raw.get("all_from") or raw.get("valid_from"), raw.get("all_to") or raw.get("valid_to")
    if not isinstance(start, str) or not isinstance(end, str):
        raise workflow_node.WorkflowContractError("authorized metadata date_range is incomplete")
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise workflow_node.WorkflowContractError("authorized metadata date_range is invalid") from exc
    if end_date < start_date or (end_date - start_date).days > 366:
        raise workflow_node.WorkflowContractError("authorized metadata date_range exceeds one year")
    return {"from": start + "T00:00:00Z", "to": end + "T23:59:59Z"}


def tool_plan_query(args: dict[str, Any]) -> dict[str, Any]:
    step = "plan_query"
    unexpected = sorted(set(args) - {"dataset_metadata_ref", "selected_fields", "minimum_rows", "maximum_rows", "refId", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported planner arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; Planner accepts only opaque metadata refs and explicit projection options.")
    metadata_ref = args.get("dataset_metadata_ref")
    selected_fields = args.get("selected_fields")
    if not isinstance(metadata_ref, str):
        return error_response(step=step, error="dataset_metadata_ref is required", recoverable=False, instruction="Stop; inspect an authorized Grafana dataset before planning.")
    if not isinstance(selected_fields, list) or not selected_fields or any(not isinstance(field, str) or not field for field in selected_fields):
        return error_response(step=step, error="selected_fields must contain explicit field names", recoverable=False, instruction="Stop; choose fields from inspected dataset metadata.")
    selected_fields = list(selected_fields)
    if len(set(selected_fields)) != len(selected_fields):
        return error_response(step=step, error="selected_fields must be unique", recoverable=False, instruction="Stop; remove duplicate field selections.")
    if len(selected_fields) > MAX_PLAN_FIELDS:
        return error_response(step=step, error=f"selected_fields exceeds maximum {MAX_PLAN_FIELDS}", recoverable=False, instruction="Stop; reduce the explicit projection.")
    try:
        minimum_rows = int(args.get("minimum_rows", 1))
        maximum_rows = int(args.get("maximum_rows", MAX_PLAN_ROWS))
    except (TypeError, ValueError):
        minimum_rows, maximum_rows = 0, 0
    if minimum_rows < 1 or maximum_rows < minimum_rows or maximum_rows > MAX_PLAN_ROWS:
        return error_response(step=step, error=f"row bounds must satisfy 1 <= minimum_rows <= maximum_rows <= {MAX_PLAN_ROWS}", recoverable=False, instruction="Stop; provide bounded row validation requirements.")
    try:
        context = context_from_args(args)
        run_id, parts = parse_artifact_ref(metadata_ref)
        if parts != ("dataset-metadata",):
            raise workflow_node.WorkflowContractError("dataset_metadata_ref must reference dataset-metadata")
        metadata = ARTIFACTS.read_json(context, metadata_ref)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("query_template"), dict):
            raise workflow_node.WorkflowContractError("dataset metadata artifact is invalid")
        available = {str(field.get("name")): field for field in metadata.get("fields", []) if isinstance(field, dict) and field.get("name")}
        unknown = [field for field in selected_fields if field not in available]
        if unknown:
            raise workflow_node.WorkflowContractError("selected fields are not in authorized metadata: " + ", ".join(unknown))
        requested_fields = list(selected_fields)
        validity_rules = []
        for field_name, field in available.items():
            applies_to = field.get("validity_for")
            if not isinstance(applies_to, list) or not set(requested_fields).intersection(str(value) for value in applies_to):
                continue
            validity_rules.append({"field": field_name, "applies_to": [str(value) for value in applies_to], "accepted_values": field.get("accepted_values") or [True]})
            if field_name not in selected_fields:
                selected_fields.append(field_name)
        if len(selected_fields) > MAX_PLAN_FIELDS:
            raise workflow_node.WorkflowContractError(f"selected fields plus validity companions exceed maximum {MAX_PLAN_FIELDS}")
        query = json.loads(json.dumps(metadata["query_template"]))
        query["refId"] = str(args.get("refId") or "A")
        columns = {column.get("selector"): column for column in query.get("columns", []) if isinstance(column, dict) and column.get("selector")}
        query["columns"] = [columns[field] for field in selected_fields if field in columns]
        if len(query["columns"]) != len(selected_fields):
            raise workflow_node.WorkflowContractError("query template cannot project every selected field")
        time_range = bounded_metadata_time_range(metadata)
        plan = {
            "dataset_id": metadata.get("dataset_id"),
            "datasource_uid": metadata.get("datasource_uid"),
            "datasource_type": metadata.get("datasource_type"),
            "query_language": "csv",
            "selected_fields": selected_fields,
            "grafana_query": query,
            "time_range": time_range,
            "analysis_input_contract": {"required_fields": selected_fields, "optional_fields": [], "validity_rules": validity_rules, "minimum_rows": minimum_rows, "maximum_rows": maximum_rows, "maximum_fields": MAX_PLAN_FIELDS, "maximum_response_bytes": MAX_PLAN_RESPONSE_BYTES},
            "provenance": {"dataset_metadata_ref": metadata_ref, "dataset_id": metadata.get("dataset_id"), "datasource_uid": metadata.get("datasource_uid"), "requested_fields": requested_fields, "selected_fields": selected_fields, "time_range": time_range},
        }
        plan_ref = ARTIFACTS.write_json(context, run_id, "query-plan", plan)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; dataset metadata context mismatch.")
    except (workflow_node.WorkflowContractError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; query plan validation failed.")
    return success_response(step=step, run_id=run_id, refs={"dataset_metadata_ref": metadata_ref, "plan_ref": plan_ref}, instruction="The safe bounded query plan is ready. Execute it through Grafana Query only after the user confirms the analysis preview; there is no domain-analysis next step in this plan.", evidence={"datasource_query_executed": False, "selected_fields": selected_fields}, plan_ref=plan_ref, dataset_id=plan["dataset_id"], datasource_uid=plan["datasource_uid"], selected_fields=selected_fields, validation={"ok": True, "minimum_rows": minimum_rows, "maximum_rows": maximum_rows, "time_range": time_range})


def tool_validate_query(args: dict[str, Any]) -> dict[str, Any]:
    problems = []
    plan_ref = args.get("plan_ref")
    if isinstance(plan_ref, str):
        try:
            context = context_from_args(args)
            _, parts = parse_artifact_ref(plan_ref)
            if parts != ("query-plan",):
                raise workflow_node.WorkflowContractError("plan_ref must reference query-plan")
            plan = ARTIFACTS.read_json(context, plan_ref)
            q = plan.get("grafana_query") if isinstance(plan, dict) else None
            if not isinstance(q, dict):
                raise workflow_node.WorkflowContractError("query plan is invalid")
            if q.get("type") == "csv":
                if q.get("source") != "url" or q.get("parser") != "backend":
                    problems.append("Infinity CSV query must use URL source and backend parser")
                if q.get("datasource", {}).get("uid") != plan.get("datasource_uid"):
                    problems.append("query datasource does not match the authorized plan")
                selected = plan.get("selected_fields") or []
                selectors = [column.get("selector") for column in q.get("columns", []) if isinstance(column, dict)]
                if selectors != selected:
                    problems.append("query columns do not match selected_fields")
                if not isinstance(q.get("url"), str) or not q["url"].startswith(("http://", "https://")):
                    problems.append("Infinity URL must be an authorized HTTP(S) URL")
            else:
                problems.append("unsupported planned query type")
        except (ArtifactAuthError, workflow_node.WorkflowContractError, OSError, TypeError, KeyError) as exc:
            problems.append(str(exc))
    else:
        problems.append("plan_ref is required")
    return {"ok": not problems, "errors": problems}


TOOLS = [
    {"name": "plan_query", "description": "Compile and validate a safe bounded Grafana query plan from an opaque inspected dataset_metadata_ref plus explicit selected fields and row bounds. The authorized metadata supplies the bounded time range. This deterministic tool does not interpret natural language, select an analysis method/domain, execute a query, or prescribe a next workflow step.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_metadata_ref": {"type": "string"}, "selected_fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50, "uniqueItems": True}, "minimum_rows": {"type": "integer", "minimum": 1, "maximum": 5000}, "maximum_rows": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 5000}, "refId": {"type": "string", "default": "A"}}, "required": ["dataset_metadata_ref", "selected_fields"]}},
    {"name": "validate_query", "description": "Revalidate an authorized opaque query plan without executing it.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"plan_ref": {"type": "string"}}, "required": ["plan_ref"]}},
]

HANDLERS = {"plan_query": tool_plan_query, "validate_query": tool_validate_query}


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
        fn = HANDLERS.get(name)
        if fn is None:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        if not isinstance(args, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        tool = next(tool for tool in TOOLS if tool["name"] == name)
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context"}
        unexpected = sorted(set(args) - allowed)
        if unexpected:
            out = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only arguments declared by this tool schema.")
        else:
            try:
                out = fn(args)
            except Exception as exc:
                return rpc_result(rid, {"content": [{"type": "text", "text": f"tool error: {exc}"}], "isError": True})
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": bool(isinstance(out, dict) and (out.get("error") or not out.get("ok", True)))})
    if rid is None:
        return None
    return rpc_error(rid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: Any = None) -> None:
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
        msgs = payload if isinstance(payload, list) else [payload]
        replies = [r for m in msgs if (r := handle_rpc(inject_header_context(m, self.headers))) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):  # noqa: A002 — BaseHTTPRequestHandler signature
        sys.stderr.write("data-query-planner-mcp " + format % args + "\n")


def self_check() -> None:
    context = {"org_id": "1", "user_id": "planner-self-check"}
    run_id = ARTIFACTS.create_run(context)
    metadata_ref = ARTIFACTS.write_json(context, run_id, "dataset-metadata", {"dataset_id": "self-check-dataset", "datasource_uid": "self-check", "datasource_type": "yesoreyeram-infinity-datasource", "fields": [{"name": "timestamp", "type": "date"}, {"name": "metric", "type": "number"}, {"name": "feature", "type": "number"}], "date_range": {"all_from": "2026-01-01", "all_to": "2026-12-31"}, "query_template": {"refId": "A", "datasource": {"uid": "self-check", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/data.csv", "parser": "backend", "columns": [{"selector": "timestamp", "text": "timestamp", "type": "timestamp"}, {"selector": "metric", "text": "metric", "type": "number"}, {"selector": "feature", "text": "feature", "type": "number"}]}})
    plan = tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["timestamp", "metric", "feature"], "minimum_rows": 20, "_server_context": context})
    if not plan.get("ok") or plan.get("datasource_uid") != "self-check" or not plan.get("plan_ref", "").startswith("artifact://"):
        raise RuntimeError(str(plan))
    plan_artifact = ARTIFACTS.read_json(context, plan["plan_ref"])
    if plan_artifact["analysis_input_contract"] != {"required_fields": ["timestamp", "metric", "feature"], "optional_fields": [], "validity_rules": [], "minimum_rows": 20, "maximum_rows": 5000, "maximum_fields": 50, "maximum_response_bytes": 4194304} or plan_artifact.get("time_range") != {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T23:59:59Z"}:
        raise RuntimeError(str(plan_artifact))
    if "next_step" in plan or "request" in plan_artifact:
        raise RuntimeError("query plan must not contain a fixed workflow or natural-language routing")
    invalid_field = tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["missing"], "_server_context": context})
    natural_language = tool_plan_query({"request": "fixed intent must not be routed", "_server_context": context})
    if invalid_field.get("ok") or natural_language.get("ok"):
        raise RuntimeError("invalid planner inputs must fail")
    validation = tool_validate_query({"plan_ref": plan["plan_ref"], "_server_context": context})
    if not validation["ok"]:
        raise RuntimeError(str(validation))
    print(json.dumps({"ok": True, "generic_plan_ref": plan["plan_ref"], "runtime_tools": [tool["name"] for tool in TOOLS], "negative_checks": ["invalid_field", "natural_language_routing", "legacy_wferp_route_absent"]}, ensure_ascii=False, indent=2))


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
