#!/usr/bin/env python3
"""Resolve opaque analysis refs inside model-authored Grafana dashboards.

This service never chooses a panel type and never writes Grafana. Ask O11y's
built-in Grafana MCP remains the only dashboard writer.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import uuid
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
artifact_assets = load_module("artifact_assets", ROOT / "artifact_assets.py")
ArtifactAuthError = artifact_store.ArtifactAuthError
ArtifactStore = artifact_store.ArtifactStore
WorkflowContractError = workflow_node.WorkflowContractError
authenticate_headers = mcp_security.authenticate_headers
error_response = workflow_node.error_response
parse_artifact_ref = workflow_node.parse_artifact_ref
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
success_response = workflow_node.success_response

try:
    PORT = int(os.environ.get("ARTIFACT_BRIDGE_MCP_PORT", "8773"))
except ValueError:
    PORT = 8773
SERVER_INFO = {"name": "artifact-bridge-mcp", "version": "0.1.0"}
PROTOCOL = "2025-03-26"
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
MAX_RPC_BODY_BYTES = 512 * 1024
MAX_DASHBOARD_BYTES = 384 * 1024
MAX_PANELS = 24
MAX_TARGETS = 48
MAX_INLINE_CSV_BYTES = 1024 * 1024
MAX_ASSET_BINDINGS = 24
ARTIFACT_PUBLIC_BASE = os.environ.get("ARTIFACT_PUBLIC_BASE", "http://127.0.0.1:8777").rstrip("/")
PLACEHOLDER_KEYS = {"$plan_ref", "$execution_ref", "output_index", "fields", "refId", "datasource"}


def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    return {"org_id": str(org), "user_id": str(user)} if org and user else None


def inject_header_context(msg: dict[str, Any], headers) -> dict[str, Any]:
    if msg.get("method") != "tools/call":
        return msg
    params = msg.setdefault("params", {})
    if not isinstance(params, dict):
        return msg
    args = params.setdefault("arguments", {})
    if not isinstance(args, dict):
        return msg
    context = context_from_headers(headers)
    if context:
        args["_server_context"] = context
    return msg


def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    context = args.get("_server_context")
    if not isinstance(context, dict) or not context.get("org_id") or not context.get("user_id"):
        raise PermissionError("trusted execution context is required")
    return {"org_id": str(context["org_id"]), "user_id": str(context["user_id"])}


def validate_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("grafana_query"), dict):
        raise WorkflowContractError("plan_ref payload is invalid")
    fields = value.get("selected_fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(item, str) for item in fields):
        raise WorkflowContractError("plan_ref selected_fields are invalid")
    datasource_uid = value.get("datasource_uid")
    datasource_type = value.get("datasource_type")
    if not isinstance(datasource_uid, str) or not datasource_uid or not isinstance(datasource_type, str) or not datasource_type:
        raise WorkflowContractError("plan_ref datasource identity is invalid")
    return value


def validate_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("results"), list) or value.get("error"):
        raise WorkflowContractError("execution_ref payload is invalid")
    return value


def csv_output(execution: dict[str, Any], index: Any) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(index, int) or index < 0 or index >= len(execution["results"]):
        raise WorkflowContractError("output_index is invalid")
    result = execution["results"][index]
    mime = result.get("mime") if isinstance(result, dict) else None
    data = mime.get("text/csv") if isinstance(mime, dict) else None
    if not isinstance(data, str):
        raise WorkflowContractError("artifact dashboard targets require a bounded text/csv output")
    if len(data.encode()) > MAX_INLINE_CSV_BYTES:
        raise WorkflowContractError("CSV output is too large for an inline Grafana target")
    reader = csv.DictReader(io.StringIO(data))
    if not reader.fieldnames or len(reader.fieldnames) > 100 or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise WorkflowContractError("CSV output schema is invalid")
    rows = []
    for _, row in zip(range(100), reader):
        rows.append(row)
    columns = []
    for field in reader.fieldnames:
        values = [row.get(field, "") for row in rows if row.get(field, "") != ""]
        value_type = "string"
        if values:
            try:
                for value in values:
                    float(value)
                value_type = "number"
            except ValueError:
                if all("-" in value and ":" in value for value in values):
                    value_type = "timestamp"
        columns.append({"selector": field, "text": field, "type": value_type})
    return data, columns


def selected_columns(columns: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    by_name = {str(item.get("selector")): item for item in columns if isinstance(item, dict) and item.get("selector")}
    unknown = sorted(set(fields) - set(by_name))
    if unknown:
        raise WorkflowContractError("dashboard target fields are not authorized by its source: " + ", ".join(unknown))
    return [by_name[field] for field in fields]


def resolve_target(context: dict[str, str], target: dict[str, Any]) -> dict[str, Any]:
    if "$plan_ref" not in target and "$execution_ref" not in target:
        if target:
            raise WorkflowContractError("nonempty dashboard targets require an authorized opaque binding")
        return target
    unexpected = sorted(set(target) - PLACEHOLDER_KEYS)
    if unexpected:
        raise WorkflowContractError("opaque dashboard target has unsupported keys: " + ", ".join(unexpected))
    plan_ref = target.get("$plan_ref")
    execution_ref = target.get("$execution_ref")
    if plan_ref is None and isinstance(execution_ref, str):
        execution_run_id, execution_parts = parse_artifact_ref(execution_ref)
        if execution_parts != ("sandbox-execution",):
            raise WorkflowContractError("$execution_ref must reference sandbox-execution")
        provenance_ref = f"artifact://{execution_run_id}/sandbox-provenance"
        provenance = ARTIFACTS.read_json(context, provenance_ref)
        input_ref = provenance.get("input_frame_ref") if isinstance(provenance, dict) else None
        if not isinstance(input_ref, str) or parse_artifact_ref(input_ref)[1] != ("grafana-frame",):
            raise WorkflowContractError("Sandbox provenance has no authorized Grafana input ref")
        plan_ref = f"artifact://{parse_artifact_ref(input_ref)[0]}/query-plan"
    if not isinstance(plan_ref, str):
        raise WorkflowContractError("opaque dashboard target requires $plan_ref or derivable $execution_ref provenance")
    run_id, parts = parse_artifact_ref(plan_ref)
    if parts != ("query-plan",):
        raise WorkflowContractError("$plan_ref must reference query-plan")
    plan = validate_plan(ARTIFACTS.read_json(context, plan_ref))
    fields = target.get("fields")
    ref_id = target.get("refId", "A")
    if not isinstance(ref_id, str) or not ref_id or len(ref_id) > 8:
        raise WorkflowContractError("dashboard target refId is invalid")
    if execution_ref is None:
        if not isinstance(fields, list) or not fields or len(fields) > 100 or not all(isinstance(item, str) and item for item in fields):
            raise WorkflowContractError("opaque query target requires bounded fields")
        query = json.loads(json.dumps(plan["grafana_query"]))
        columns = query.get("columns")
        if not isinstance(columns, list):
            raise WorkflowContractError("query plan has no trusted column mapping")
        query["columns"] = selected_columns(columns, fields)
        query["refId"] = ref_id
        return query
    if not isinstance(execution_ref, str) or parse_artifact_ref(execution_ref)[1] != ("sandbox-execution",):
        raise WorkflowContractError("$execution_ref must reference sandbox-execution")
    execution = validate_execution(ARTIFACTS.read_json(context, execution_ref))
    data, columns = csv_output(execution, target.get("output_index"))
    if fields is None:
        fields = [column["selector"] for column in columns]
    if not isinstance(fields, list) or not fields or len(fields) > 100 or not all(isinstance(item, str) and item for item in fields):
        raise WorkflowContractError("opaque artifact target requires bounded fields")
    return {
        "refId": ref_id,
        "datasource": {"uid": plan["datasource_uid"], "type": plan["datasource_type"]},
        "type": "csv",
        "source": "inline",
        "data": data,
        "parser": "backend",
        "format": "table",
        "columns": selected_columns(columns, fields),
        "csv_options": {"delimiter": ",", "skip_empty_lines": True},
        "url_options": {"method": "GET", "data": ""},
    }


def replace_asset_placeholder(value: Any, placeholder: str, asset_url: str) -> tuple[Any, int]:
    if isinstance(value, str):
        return value.replace(placeholder, asset_url), value.count(placeholder)
    if isinstance(value, list):
        count = 0
        output = []
        for item in value:
            replaced, found = replace_asset_placeholder(item, placeholder, asset_url)
            output.append(replaced)
            count += found
        return output, count
    if isinstance(value, dict):
        count = 0
        output = {}
        for key, item in value.items():
            replaced, found = replace_asset_placeholder(item, placeholder, asset_url)
            output[key] = replaced
            count += found
        return output, count
    return value, 0


def resolve_asset_bindings(context: dict[str, str], panel: dict[str, Any], counters: dict[str, int]) -> dict[str, Any]:
    if "/assets/" in json.dumps(panel, ensure_ascii=False):
        raise WorkflowContractError("model-authored dashboard must use opaque asset bindings, not asset URLs")
    bindings = panel.pop("askO11yAssetBindings", [])
    if not isinstance(bindings, list) or len(bindings) > MAX_ASSET_BINDINGS:
        raise WorkflowContractError(f"panel asset bindings must be an array with at most {MAX_ASSET_BINDINGS} entries")
    counters["assets"] += len(bindings)
    if counters["assets"] > MAX_ASSET_BINDINGS:
        raise WorkflowContractError(f"dashboard has more than {MAX_ASSET_BINDINGS} asset bindings")
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"placeholder", "$execution_ref", "output_index"}:
            raise WorkflowContractError("asset binding requires only placeholder, $execution_ref, and output_index")
        placeholder = binding["placeholder"]
        execution_ref = binding["$execution_ref"]
        output_index = binding["output_index"]
        if not isinstance(placeholder, str) or not placeholder.startswith("$asset_url_") or not placeholder.removeprefix("$asset_url_").replace("_", "").isalnum():
            raise WorkflowContractError("asset placeholder must start with $asset_url_ and contain only letters, digits, or underscores")
        if not isinstance(execution_ref, str) or isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
            raise WorkflowContractError("asset binding requires an opaque execution ref and non-negative output index")
        execution = ARTIFACTS.read_json(context, execution_ref)
        try:
            result = execution["results"][output_index]
            mime = result.get("mime") if isinstance(result, dict) else None
        except (KeyError, IndexError, TypeError) as exc:
            raise WorkflowContractError("asset output index does not exist") from exc
        if not isinstance(mime, dict) or not isinstance(mime.get("image/png"), str):
            raise WorkflowContractError("dashboard asset binding currently requires image/png")
        asset_url = artifact_assets.sign_output_url(
            public_base=ARTIFACT_PUBLIC_BASE,
            secret=os.environ.get("MCP_SHARED_TOKEN", ""),
            context=context,
            execution_ref=execution_ref,
            output_index=output_index,
            expires_at=int(time.time()) + ARTIFACTS.retention_seconds,
        )
        panel, replacements = replace_asset_placeholder(panel, placeholder, asset_url)
        if replacements == 0:
            raise WorkflowContractError("asset placeholder is not used by the panel")
    if "$asset_url_" in json.dumps(panel, ensure_ascii=False):
        raise WorkflowContractError("dashboard contains an unresolved asset placeholder")
    return panel


def resolve_panels(context: dict[str, str], panels: Any, counters: dict[str, int]) -> list[dict[str, Any]]:
    if not isinstance(panels, list) or len(panels) > MAX_PANELS:
        raise WorkflowContractError(f"dashboard panels must be an array with at most {MAX_PANELS} entries")
    resolved = []
    for panel in panels:
        counters["panels"] += 1
        if counters["panels"] > MAX_PANELS:
            raise WorkflowContractError(f"dashboard has more than {MAX_PANELS} panels")
        if not isinstance(panel, dict):
            raise WorkflowContractError("dashboard panel is invalid")
        item = resolve_asset_bindings(context, json.loads(json.dumps(panel)), counters)
        targets = item.get("targets", [])
        if not isinstance(targets, list):
            raise WorkflowContractError("dashboard panel targets must be an array")
        counters["targets"] += len(targets)
        if counters["targets"] > MAX_TARGETS:
            raise WorkflowContractError(f"dashboard has more than {MAX_TARGETS} targets")
        item["targets"] = [resolve_target(context, target) if isinstance(target, dict) else target for target in targets]
        datasources = [target.get("datasource") for target in item["targets"] if isinstance(target, dict) and isinstance(target.get("datasource"), dict)]
        if datasources and all(datasource == datasources[0] for datasource in datasources):
            item["datasource"] = datasources[0]
        if "panels" in item:
            item["panels"] = resolve_panels(context, item["panels"], counters)
        resolved.append(item)
    return resolved


def resolve_dashboard_refs(args: dict[str, Any]) -> dict[str, Any]:
    step = "resolve_dashboard_refs"
    try:
        context = context_from_args(args)
        dashboard = args.get("dashboard")
        if not isinstance(dashboard, dict):
            raise WorkflowContractError("dashboard is required")
        if len(json.dumps(dashboard, ensure_ascii=False).encode()) > MAX_DASHBOARD_BYTES:
            raise WorkflowContractError("dashboard exceeds resolver size limit")
        output = json.loads(json.dumps(dashboard))
        counters = {"panels": 0, "targets": 0, "assets": 0}
        output["panels"] = resolve_panels(context, output.get("panels", []), counters)
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the opaque dashboard binding is not authorized for this context.")
    except (WorkflowContractError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Revise only the dashboard binding placeholders; do not rerun successful query or analysis work.")
    return success_response(
        step=step,
        run_id="run_" + uuid.uuid4().hex,
        refs={},
        instruction="Internal host result: dispatch the resolved dashboard only to Ask O11y's built-in Grafana MCP; never expose it to the model.",
        evidence={"resolved_panels": counters["panels"], "resolved_targets": counters["targets"], "resolved_assets": counters["assets"]},
        dashboard=output,
    )


TOOLS = [{
    "name": "resolve_dashboard_refs",
    "description": "Internal-only: resolve authorized opaque query/analysis refs inside a dashboard before dispatch to Ask O11y's built-in Grafana MCP. It never chooses panels or writes Grafana.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"dashboard": {"type": "object"}},
        "required": ["dashboard"],
    },
}]


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
        name = params.get("name")
        if name != "resolve_dashboard_refs":
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        unexpected = sorted(set(arguments) - {"dashboard", "_server_context"})
        output = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Pass only declared tool arguments.") if unexpected else resolve_dashboard_refs(arguments)
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}], "isError": not bool(output.get("ok"))})
    return None if rid is None else rpc_error(rid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: Any = None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self._send(405, {"error": "Streamable HTTP MCP uses POST"})

    def do_DELETE(self):
        self._send(405, {"error": "sessions are stateless"})

    def do_POST(self):
        if not authenticate_headers(self.headers):
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_RPC_BODY_BYTES:
                self._send(413, {"error": f"request body exceeds {MAX_RPC_BODY_BYTES} bytes"})
                return
            message = json.loads(self.rfile.read(length) or b"{}")
            result = handle_rpc(inject_header_context(message, self.headers))
            self._send(202 if result is None else 200, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})

    def log_message(self, format, *args):
        return


def self_check() -> int:
    global ARTIFACTS
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        context = {"org_id": "1", "user_id": "self-check"}
        run_id = ARTIFACTS.create_run(context)
        plan_ref = ARTIFACTS.write_json(context, run_id, "query-plan", {
            "datasource_uid": "csv-poc",
            "datasource_type": "yesoreyeram-infinity-datasource",
            "selected_fields": ["date", "x", "y"],
            "grafana_query": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://data.example/input.csv", "parser": "backend", "format": "table", "columns": [{"selector": "date", "text": "date", "type": "timestamp"}, {"selector": "x", "text": "x", "type": "number"}, {"selector": "y", "text": "y", "type": "number"}]},
        })
        execution_ref = ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": [
            {"mime": {"text/csv": "date,x,y\n2026-01-01,1,2\n"}, "display_name": "derived.csv"},
            {"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "plot.png"},
        ], "error": None})
        dashboard = {"title": "Dynamic", "panels": [
            {"type": "xychart", "targets": [{"$plan_ref": plan_ref, "fields": ["x", "y"], "refId": "A"}]},
            {"type": "trend", "targets": [{"$plan_ref": plan_ref, "$execution_ref": execution_ref, "output_index": 0, "fields": ["date", "x", "y"], "refId": "B"}]},
            {"type": "text", "options": {"mode": "html", "content": "<img src=\"$asset_url_plot\">"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_plot", "$execution_ref": execution_ref, "output_index": 1}]},
        ]}
        result = resolve_dashboard_refs({"dashboard": dashboard, "_server_context": context})
        bad = resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$plan_ref": plan_ref, "fields": ["missing"]}]}]}, "_server_context": context})
        raw_target = resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"datasource": {"uid": "raw"}, "expr": "up"}]}]}, "_server_context": context})
        nested = {"targets": []}
        for _ in range(MAX_PANELS):
            nested = {"targets": [], "panels": [nested]}
        excessive_panels = resolve_dashboard_refs({"dashboard": {"panels": [nested]}, "_server_context": context})
        checks = {
            "panel_json_untouched": result.get("dashboard", {}).get("panels", [{}])[0].get("type") == "xychart",
            "plan_ref_resolved_server_side": result.get("dashboard", {}).get("panels", [{}])[0].get("targets", [{}])[0].get("url") == "http://data.example/input.csv",
            "artifact_ref_resolved_to_inline_csv": result.get("dashboard", {}).get("panels", [{}, {}])[1].get("targets", [{}])[0].get("source") == "inline",
            "opaque_refs_removed_before_grafana": "$plan_ref" not in json.dumps(result.get("dashboard", {})),
            "asset_url_resolved_without_panel_generation": "/assets/" in result.get("dashboard", {}).get("panels", [{}, {}, {}])[2].get("options", {}).get("content", "") and "askO11yAssetBindings" not in result.get("dashboard", {}).get("panels", [{}, {}, {}])[2],
            "unknown_field_rejected": not bad.get("ok"),
            "raw_target_rejected": not raw_target.get("ok"),
            "nested_panel_limit_enforced": not excessive_panels.get("ok"),
        }
        if not result.get("ok") or not all(checks.values()):
            raise SystemExit(json.dumps({"ok": False, "checks": checks, "result": result, "bad": bad}, indent=2))
        print(json.dumps({"ok": True, "checks": list(checks)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        return self_check()
    require_runtime_token()
    require_service_identity()
    server = ThreadingHTTPServer((runtime_bind_host(), PORT), Handler)
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} on {runtime_bind_host()}:{PORT}", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
