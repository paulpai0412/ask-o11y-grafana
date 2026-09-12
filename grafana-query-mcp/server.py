#!/usr/bin/env python3
"""Read authorized datasets through Grafana /api/ds/query.

Validates returned frame shape and resource bounds without requiring an analysis
plan. Datasource access stays behind Grafana; SQL remains policy-checked.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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
uploaded_datasets = load_module("uploaded_datasets", ROOT / "uploaded_datasets.py")
ArtifactStore = artifact_store.ArtifactStore
authenticate_headers = mcp_security.authenticate_headers
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
success_response = workflow_node.success_response
error_response = workflow_node.error_response

try:
    PORT = int(os.environ.get("GRAFANA_QUERY_MCP_PORT", "8772"))
except ValueError:
    PORT = 8772
GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
uploaded_datasets.cleanup_expired()
SERVER_INFO = {"name": "grafana-query-mcp", "version": "0.4.0"}
PROTOCOL = "2025-03-26"
CATALOG_FILE = ROOT / "config" / "authorized-grafana-datasets.json"
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
MAX_RESULT_ROWS = 100_000
MAX_RESULT_FIELDS = 200
MAX_TIME_RANGE_SECONDS = 367 * 24 * 60 * 60
UPLOAD_PUBLIC_BASE = os.environ.get("UPLOAD_PUBLIC_BASE", "http://127.0.0.1:8772").rstrip("/")



def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-Actor-User-Id") or headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    if org and user:
        context = {"org_id": str(org), "user_id": str(user)}
        session_id = headers.get("X-Grafana-Session-Id")
        if session_id:
            context["session_id"] = str(session_id)
        return context
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
    args.pop("_server_session_id", None)
    context = context_from_headers(headers)
    if context is not None:
        args["_server_context"] = context
        if context.get("session_id"):
            args["_server_session_id"] = context["session_id"]
    return msg

def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    raw_context = args.get("_server_context")
    if isinstance(raw_context, dict) and raw_context.get("org_id") and raw_context.get("user_id"):
        context = {"org_id": str(raw_context["org_id"]), "user_id": str(raw_context["user_id"])}
        session_id = args.get("_server_session_id")
        if isinstance(session_id, str) and session_id:
            context["session_id"] = session_id
        return context
    raise workflow_node.WorkflowContractError("verified artifact context is required")


def post_grafana(path: str, body: dict[str, Any], maximum_bytes: int = MAX_RESPONSE_BYTES) -> dict[str, Any]:
    if not GRAFANA_URL:
        raise RuntimeError("GRAFANA_URL is required")
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        GRAFANA_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            raw = resp.read(maximum_bytes + 1)
            if len(raw) > maximum_bytes:
                raise RuntimeError(f"Grafana response exceeds maximum {maximum_bytes} bytes")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Grafana HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {exc}") from exc


def get_grafana(path: str) -> Any:
    if not GRAFANA_URL:
        raise RuntimeError("GRAFANA_URL is required")
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    request = urllib.request.Request(GRAFANA_URL + path, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Grafana HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {exc}") from exc


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise workflow_node.WorkflowContractError(f"cannot load configured dataset metadata: {path.name}") from exc


def configured_datasets() -> list[dict[str, Any]]:
    raw = load_json(CATALOG_FILE)
    datasets = raw.get("datasets") if isinstance(raw, dict) else None
    if not isinstance(datasets, list) or any(not isinstance(item, dict) for item in datasets):
        raise workflow_node.WorkflowContractError("authorized dataset catalog is invalid")
    return datasets


def tool_discover_datasets(args: dict[str, Any]) -> dict[str, Any]:
    step = "discover_datasets"
    try:
        context = context_from_args(args)
        live = get_grafana("/api/datasources")
        if not isinstance(live, list):
            raise workflow_node.WorkflowContractError("Grafana datasource catalog must be a list")
        live_by_uid = {str(item.get("uid")): item for item in live if isinstance(item, dict) and item.get("uid")}
        candidates = []
        for configured in configured_datasets():
            uid = str(configured.get("datasource_uid") or "")
            datasource = live_by_uid.get(uid)
            if not datasource or datasource.get("type") != configured.get("datasource_type"):
                continue
            candidates.append({"dataset_id": configured.get("id"), "title": configured.get("title"), "description": configured.get("description"), "domain_hints": configured.get("domain_hints", []), "datasource_uid": uid, "datasource_type": datasource.get("type")})
        infinity = live_by_uid.get("csv-poc")
        if infinity and infinity.get("type") == "yesoreyeram-infinity-datasource":
            for upload in uploaded_datasets.list_uploads(context):
                candidates.append({"dataset_id": upload["id"], "title": upload["filename"], "description": "Session-owned uploaded dataset", "domain_hints": ["uploaded", "csv", "excel"], "datasource_uid": "csv-poc", "datasource_type": infinity.get("type"), "session_id": upload["session_id"], "rows": upload["rows"], "columns": upload["columns"]})
        run_id = ARTIFACTS.create_run(context)
        catalog_ref = ARTIFACTS.write_json(context, run_id, "datasource-catalog", {"datasets": candidates})
    except (RuntimeError, workflow_node.WorkflowContractError, OSError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; authorized Grafana datasource discovery failed.")
    return success_response(step=step, run_id=run_id, refs={"datasource_catalog_ref": catalog_ref}, instruction="Choose an authorized dataset that answers the user's question. Inspect its schema when needed, or call query_dataset directly. No datasource query has executed.", evidence={"grafana_metadata_read": True, "datasource_query_executed": False}, datasets=candidates, datasource_catalog_ref=catalog_ref)


def tool_inspect_dataset(args: dict[str, Any]) -> dict[str, Any]:
    step = "inspect_dataset"
    dataset_id = args.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        return error_response(step=step, error="dataset_id is required", recoverable=False, instruction="Stop; choose an authorized dataset from discover_datasets.")
    try:
        context = context_from_args(args)
        configured = next((item for item in configured_datasets() if item.get("id") == dataset_id), None)
        if dataset_id.startswith("upload_"):
            upload = uploaded_datasets.inspect_upload(context, dataset_id, context.get("session_id"))
            uid, expected_type, query_kind = "csv-poc", "yesoreyeram-infinity-datasource", "uploaded_csv"
        else:
            if configured is None:
                raise workflow_node.WorkflowContractError("dataset_id is not authorized")
            upload = None
            uid = str(configured.get("datasource_uid") or "")
            expected_type = configured.get("datasource_type")
            query_kind = str(configured.get("query_kind") or "")
        live = get_grafana("/api/datasources/uid/" + urllib.parse.quote(uid, safe=""))
        if not isinstance(live, dict) or live.get("type") != expected_type:
            raise workflow_node.WorkflowContractError("configured dataset does not match the live Grafana datasource")
        if upload is not None:
            signed_url = uploaded_datasets.sign_csv_url(public_base=UPLOAD_PUBLIC_BASE, secret=os.environ.get("MCP_SHARED_TOKEN", ""), metadata=upload)
            fields = [{"name": field["name"], "type": field["type"], "display_name": field["name"]} for field in upload["fields"]]
            query_columns = [{"selector": field["name"], "text": field["name"], "type": "timestamp" if field["type"] == "date" else field["type"]} for field in upload["fields"]]
            query_template = {"refId": "A", "datasource": {"uid": uid, "type": live.get("type")}, "type": "csv", "source": "url", "url": signed_url, "parser": "backend", "format": "table", "url_options": {"method": "GET", "data": ""}, "csv_options": {"delimiter": ",", "skip_empty_lines": True}, "columns": query_columns}
            metadata_artifact = {"dataset_id": dataset_id, "title": upload["filename"], "description": "Session-owned uploaded dataset", "domain_hints": ["uploaded", "csv", "excel"], "datasource_uid": uid, "datasource_type": live.get("type"), "query_kind": query_kind, "session_id": upload["session_id"], "source_format": upload.get("source_format"), "fields": fields, "minimum_rows": 1, "row_count_hint": upload["rows"], "date_range": upload.get("date_range") or {"kind": "unbounded"}, "query_template": query_template}
        if query_kind == "infinity_csv" and configured is not None:
            metadata = load_json(ROOT / str(configured.get("metadata_file")))
            profile = load_json(ROOT / str(configured.get("query_profile_file")))
            fields = [{key: field.get(key) for key in ["name", "type", "display_name", "unit", "description", "aliases", "validity_for", "accepted_values"]} for field in metadata.get("fields", []) if isinstance(field, dict)]
            csv_url = os.environ.get(str(profile.get("csv_url_env") or ""), "")
            if not csv_url:
                raise workflow_node.WorkflowContractError("configured Infinity dataset URL is unavailable")
            query_columns = [{"selector": field["name"], "text": field["name"], "type": "timestamp" if field.get("type") == "date" else field.get("type", "string")} for field in fields]
            query_template = {"refId": "A", "datasource": {"uid": uid, "type": live.get("type")}, "type": "csv", "source": "url", "url": csv_url, "parser": "backend", "format": "table", "url_options": {"method": "GET", "data": ""}, "csv_options": {"delimiter": ",", "skip_empty_lines": True}, "columns": query_columns}
            metadata_artifact = {"dataset_id": dataset_id, "title": configured.get("title"), "description": configured.get("description"), "domain_hints": configured.get("domain_hints", []), "datasource_uid": uid, "datasource_type": live.get("type"), "query_kind": query_kind, "fields": fields, "minimum_rows": metadata.get("minimum_rows", 1), "row_count_hint": (metadata.get("row_counts") or {}).get("total"), "date_range": metadata.get("date_range", {}), "query_template": query_template}
        else:
            raise workflow_node.WorkflowContractError("configured dataset query_kind is unsupported")
        run_id = ARTIFACTS.create_run(context)
        metadata_ref = ARTIFACTS.write_json(context, run_id, "dataset-metadata", metadata_artifact)
        document_ref = None
        if upload is not None and upload.get("source_format") in {"csv", "xlsx"}:
            uploaded_datasets.read_source(context, dataset_id, upload["session_id"])
            document_ref = ARTIFACTS.write_json(context, run_id, "uploaded-document", {"upload_id": dataset_id, "session_id": upload["session_id"], "filename": upload["filename"], "sheet": upload.get("sheet"), "source_format": upload["source_format"], "source_sha256": upload["source_sha256"]})
    except (RuntimeError, workflow_node.WorkflowContractError, OSError, StopIteration) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; authorized dataset inspection failed.")
    preview_keys = ["dataset_id", "title", "description", "domain_hints", "datasource_uid", "datasource_type", "query_kind", "source_format", "fields", "minimum_rows", "row_count_hint", "date_range", "schema_summary"]
    preview = {key: metadata_artifact[key] for key in preview_keys if key in metadata_artifact}
    instruction = "Use the returned fields to choose analysis or visualization. query_dataset executes without an analysis-plan ticket."
    refs = {"dataset_metadata_ref": metadata_ref}
    if document_ref is not None:
        refs["document_ref"] = document_ref
    return success_response(step=step, run_id=run_id, refs=refs, instruction=instruction, evidence={"grafana_metadata_read": True, "datasource_query_executed": False}, dataset_metadata_ref=metadata_ref, document_ref=document_ref, metadata=preview)


def validate_frame(response: dict[str, Any], contract: dict[str, Any], ref_id: str = "A") -> dict[str, Any]:
    result = response.get("results", {}).get(ref_id)
    if not result:
        return {"ok": False, "errors": ["missing Grafana query result"], "frames": []}
    frames = result.get("frames") or []
    if len(frames) > 1:
        return {"ok": False, "errors": ["expected one complete frame, not multiple unmerged frames"], "frames": []}
    if result.get("status") != 200 or not frames:
        return {"ok": False, "errors": [f"bad Grafana result: {result.get('status')} {result.get('error')}"], "frames": frames}
    fields = [field.get("name") for field in frames[0].get("schema", {}).get("fields", [])]
    values = frames[0].get("data", {}).get("values") or []
    errors = []
    try:
        maximum_fields = int(contract.get("maximum_fields", MAX_RESULT_FIELDS))
        maximum_rows = int(contract.get("maximum_rows", MAX_RESULT_ROWS))
    except (TypeError, ValueError):
        maximum_fields, maximum_rows = 0, 0
    if maximum_fields < 1 or maximum_fields > MAX_RESULT_FIELDS or maximum_rows < 1 or maximum_rows > MAX_RESULT_ROWS:
        errors.append("DataFrame contract contains invalid maximum bounds")
    if not fields or not isinstance(values, list) or len(values) != len(fields):
        errors.append("Grafana DataFrame must contain one values column per field")
        return {"ok": False, "errors": errors, "field_names": fields, "row_count": 0, "minimum_rows": 0, "frames": frames}
    if any(not isinstance(column, list) for column in values):
        return {"ok": False, "errors": ["frame columns must be arrays"], "frames": []}
    lengths = [len(column) for column in values]
    if len(set(lengths)) != 1:
        errors.append("Grafana DataFrame value columns must have equal lengths")
        return {"ok": False, "errors": errors, "field_names": fields, "row_count": 0, "minimum_rows": 0, "frames": frames}
    row_count = lengths[0] if lengths else 0
    if len(fields) > maximum_fields:
        errors.append(f"field_count {len(fields)} exceeds maximum_fields {maximum_fields}")
    if row_count > maximum_rows:
        errors.append(f"row_count {row_count} exceeds maximum_rows {maximum_rows}")
    expected_rows = contract.get("expected_rows")
    if expected_rows is not None and (isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or row_count != expected_rows):
        errors.append(f"row_count {row_count} does not match complete source rows {expected_rows}")
    missing = [field for field in contract.get("required_fields", []) if field not in fields]
    if missing:
        errors.append(f"missing required fields: {missing}")
    try:
        minimum_rows = int(contract.get("minimum_rows", 0) or 0)
    except (TypeError, ValueError):
        minimum_rows = 0
    if row_count < minimum_rows:
        errors.append(f"row_count {row_count} below minimum_rows {minimum_rows}")
    return {"ok": not errors, "errors": errors, "field_names": fields, "row_count": row_count, "minimum_rows": minimum_rows, "maximum_rows": maximum_rows, "maximum_fields": maximum_fields, "frames": frames}


_RELATIVE_TIME_RANGE = re.compile(r"^now-(?P<amount>[1-9]\d*)(?P<unit>[smhdw])$")


def parse_time_bound(value: str, reference_time: datetime) -> datetime:
    if value == "now":
        return reference_time
    match = _RELATIVE_TIME_RANGE.fullmatch(value)
    if not match:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    try:
        amount = int(match.group("amount"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("relative time range amount is invalid") from exc
    unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[match.group("unit")]
    return reference_time - timedelta(**{unit: amount})


def tool_query_dataset(args: dict[str, Any]) -> dict[str, Any]:
    """Build the authorized read at execution time; no analysis plan is a ticket."""
    step = "query_dataset"
    try:
        if set(args) - {"dataset_id", "sql", "time_range", "_server_context", "_server_session_id"}:
            raise workflow_node.WorkflowContractError("unsupported query arguments")
        context = context_from_args(args)
        if not context.get("session_id"):
            raise workflow_node.WorkflowContractError("query execution requires an authenticated session")
        inspected = tool_inspect_dataset({"dataset_id": args.get("dataset_id"), "_server_context": context, "_server_session_id": context.get("session_id")})
        if not inspected.get("ok"):
            return inspected
        metadata = ARTIFACTS.read_json(context, inspected["dataset_metadata_ref"])
        kind = metadata.get("query_kind")
        if "sql" in args or kind not in {"infinity_csv", "uploaded_csv"}:
            raise workflow_node.WorkflowContractError("this dataset uses its authorized CSV query, not model-authored SQL or URLs")
        query = metadata["query_template"]
        time_range = args.get("time_range")
        if time_range is None:
            dates = metadata.get("date_range") or {}
            start_date = dates.get("all_from") or dates.get("valid_from")
            end_date = dates.get("all_to") or dates.get("valid_to")
            time_range = {"from": start_date + "T00:00:00Z", "to": end_date + "T23:59:59Z"} if isinstance(start_date, str) and isinstance(end_date, str) else {"from": "now-367d", "to": "now"}
        if not isinstance(time_range, dict) or set(time_range) != {"from", "to"} or any(not isinstance(value, str) for value in time_range.values()):
            raise workflow_node.WorkflowContractError("time_range must contain from/to strings")
        now = datetime.now(timezone.utc)
        start, end = (parse_time_bound(time_range[key], now) for key in ("from", "to"))
        if start.tzinfo is None or end.tzinfo is None or not 0 <= (end - start).total_seconds() <= MAX_TIME_RANGE_SECONDS:
            raise workflow_node.WorkflowContractError("query time bounds exceed executor limits")
        response = post_grafana("/api/ds/query", {"queries": [query], **time_range}, MAX_RESPONSE_BYTES)
        bounds = {"maximum_rows": MAX_RESULT_ROWS, "maximum_fields": MAX_RESULT_FIELDS}
        validation = validate_frame(response, bounds)
        if not validation["ok"]:
            return error_response(step=step, error="Grafana frame rejected", recoverable=True,
                                  instruction="Correct the query or request a smaller explicit scope; no frame was saved.",
                                  evidence={"errors": validation["errors"]})
        run_id = ARTIFACTS.create_run(context)
        frame_ref = ARTIFACTS.write_json(context, run_id, "grafana-frame", validation["frames"])
        response_ref = ARTIFACTS.write_json(context, run_id, "grafana-query-response", response)
        validation_ref = ARTIFACTS.write_json(context, run_id, "dataframe-validation", {key: value for key, value in validation.items() if key != "frames"})
    except (PermissionError, workflow_node.WorkflowContractError, RuntimeError, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="The query failed. Correct its parameters without changing permissions or accessing the datasource directly.")
    return success_response(step=step, run_id=run_id, refs={"frame_ref": frame_ref, "response_ref": response_ref, "validation_ref": validation_ref},
                            frame_ref=frame_ref, available_fields=validation["field_names"], row_count=validation["row_count"],
                            instruction="Use this authorized frame directly with Python when analysis is needed. Choose methods yourself; there is no required analysis plan.",
                            evidence={"executed_by": "Grafana /api/ds/query", "dataset_id": metadata["dataset_id"], "time_range": time_range})


TOOLS = [
    {"name": "discover_datasets", "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}, "description": "List compact authorized Grafana-backed datasets and domain hints. Reads Grafana datasource metadata only; never executes a datasource query and never exposes credentials or physical paths.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "inspect_dataset", "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}, "description": "Inspect one authorized dataset's sanitized fields, types, units, row/date hints, and domain hints. Returns an opaque dataset_metadata_ref containing the internal query template; does not execute a datasource query.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_id": {"type": "string"}}, "required": ["dataset_id"]}},
    {"name": "query_dataset", "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}, "description": "Read an authorized dataset through Grafana and save a frame for Python. Queries use server-owned datasource/URL settings; model-authored SQL and URLs are rejected. No analysis-plan ticket is required. Optional time_range is the Grafana request window; it does not itself filter CSV rows.", "inputSchema": {"type": "object", "additionalProperties": False, "required": ["dataset_id"], "properties": {"dataset_id": {"type": "string"}, "sql": {"type": "string"}, "time_range": {"type": "object", "additionalProperties": False, "required": ["from", "to"], "properties": {"from": {"type": "string"}, "to": {"type": "string"}}}}}},
]
HANDLERS = {"discover_datasets": tool_discover_datasets, "inspect_dataset": tool_inspect_dataset, "query_dataset": tool_query_dataset}


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
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context", "_server_session_id"}
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
        if self.path.startswith("/uploaded-csv/"):
            token = self.path.removeprefix("/uploaded-csv/").split("?", 1)[0]
            try:
                path = uploaded_datasets.read_signed_csv(token, os.environ.get("MCP_SHARED_TOKEN", ""))
                size = path.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "private, max-age=300")
                self.end_headers()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        self.wfile.write(chunk)
            except (PermissionError, OSError):
                self._send(403, {"error": "uploaded dataset URL is invalid or expired"})
            return
        self._send(405 if self.path.rstrip("/") == "/mcp" else 404, {"error": "POST JSON-RPC to /mcp"})

    def do_PUT(self):
        if self.path.rstrip("/") != "/uploads":
            return self._send(404, {"error": "not found"})
        context = authenticate_headers(self.headers)
        if context is None:
            return self._send(401, {"error": "authenticated upload identity is required"})
        try:
            filename = urllib.parse.unquote(self.headers.get("X-Upload-Filename", ""))
            sheet = urllib.parse.unquote(self.headers.get("X-Upload-Sheet", "")) or None
        except UnicodeError:
            return self._send(400, {"error": "upload filename or sheet is invalid"})
        session_id = self.headers.get("X-Upload-Session-Id", "")
        if not filename or not session_id:
            return self._send(400, {"error": "upload filename and session id are required"})
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = uploaded_datasets.read_limited(self.rfile, content_length)
            metadata = uploaded_datasets.store_upload(context=context, session_id=session_id, filename=filename, raw=raw, sheet=sheet)
            self._send(201, {"ok": True, "dataset_id": metadata["id"], "filename": metadata["filename"], "sheet": metadata["sheet"], "rows": metadata["rows"], "columns": metadata["columns"], "fields": metadata["fields"], "expires_at": metadata["expires_at"]})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})

    def do_DELETE(self):
        if not self.path.startswith("/uploads/"):
            return self._send(404, {"error": "not found"})
        context = authenticate_headers(self.headers)
        if context is None:
            return self._send(401, {"error": "authenticated upload identity is required"})
        upload_id = self.path.removeprefix("/uploads/").split("?", 1)[0]
        session_id = self.headers.get("X-Upload-Session-Id", "")
        try:
            uploaded_datasets.delete_upload(context, upload_id, session_id)
            self._send(200, {"ok": True})
        except (PermissionError, OSError) as exc:
            self._send(404, {"error": str(exc)})

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

    def log_message(self, format, *args):
        sys.stderr.write("grafana-query-mcp " + format % args + "\n")


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    require_runtime_token()
    require_service_identity()
    bind_host = runtime_bind_host()
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} on {bind_host}:{PORT}", file=sys.stderr)
    ThreadingHTTPServer((bind_host, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
