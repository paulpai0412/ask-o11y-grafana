#!/usr/bin/env python3
"""Approval-gated Grafana rendering for authorized Sandbox output artifacts."""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from html.parser import HTMLParser
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
    PORT = int(os.environ.get("GRAFANA_RENDERER_MCP_PORT", "8773"))
except ValueError:
    PORT = 8773
SERVER_INFO = {"name": "grafana-renderer-mcp", "version": "0.4.0"}
PROTOCOL = "2025-03-26"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
APPROVAL_TTL_SECONDS = 30 * 60
PREVIEW_TAG = "ask-o11y-preview"
APPROVAL_LOCK = threading.Lock()
MAX_PANELS = 8
MAX_RPC_BODY_BYTES = 128 * 1024
MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_VISUALIZATION_OPTIONS_BYTES = 16 * 1024
SUPPORTED_MIME_TYPES = {"image/png", "text/csv", "text/html", "text/plain", "application/json"}
FORBIDDEN_INPUT_KEYS = {"execution", "results", "mime", "data", "dashboard", "panels", "approval_confirmed", "targets", "datasource", "query"}
FORBIDDEN_OPTION_KEYS = {"content", "data", "datasource", "datasourceUid", "expr", "links", "rawSql", "targets", "url"}


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
    args.pop("context", None)
    args.pop("_server_context", None)
    if context := context_from_headers(headers):
        args["_server_context"] = context
    return msg


def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    context = args.get("_server_context")
    if isinstance(context, dict) and context.get("org_id") and context.get("user_id"):
        return {"org_id": str(context["org_id"]), "user_id": str(context["user_id"])}
    raise WorkflowContractError("verified artifact context is required")


class SafeHTML(HTMLParser):
    allowed = frozenset({"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "p", "br", "strong", "em", "code", "pre", "ul", "ol", "li"})
    blocked = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.blocked:
            self.blocked_depth += 1
        elif not self.blocked_depth and tag in self.allowed:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.blocked and self.blocked_depth:
            self.blocked_depth -= 1
        elif not self.blocked_depth and tag in self.allowed:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.parts.append(html.escape(data))


def sanitize_html(value: str) -> str:
    parser = SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def validate_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("results"), list) or value.get("error"):
        raise WorkflowContractError("execution_ref must reference a successful sandbox-execution artifact")
    return value


def execution_outputs(execution: dict[str, Any]) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    for result in execution["results"]:
        if not isinstance(result, dict):
            continue
        mime = result.get("mime")
        display_name = result.get("display_name") if isinstance(result.get("display_name"), str) else None
        if isinstance(mime, dict) and mime:
            outputs.extend({"mime_type": str(mime_type), "data": data, **({"display_name": display_name} if display_name else {})} for mime_type, data in mime.items() if isinstance(data, str))
        elif isinstance(result.get("text"), str):
            outputs.append({"mime_type": "text/plain", "data": result["text"], **({"display_name": display_name} if display_name else {})})
    return outputs


def select_outputs(execution: dict[str, Any], requested: Any) -> list[tuple[int, dict[str, str]]]:
    results = execution_outputs(execution)
    if requested is None:
        indexes = [index for index, item in enumerate(results) if item.get("mime_type") in SUPPORTED_MIME_TYPES]
    elif isinstance(requested, list) and all(isinstance(index, int) and not isinstance(index, bool) for index in requested):
        indexes = requested
    else:
        raise WorkflowContractError("output_indices must be an array of integers")
    if not indexes or len(indexes) > MAX_PANELS or len(set(indexes)) != len(indexes):
        raise WorkflowContractError(f"select between 1 and {MAX_PANELS} unique supported outputs")
    selected: list[tuple[int, dict[str, str]]] = []
    for index in indexes:
        if index < 0 or index >= len(results) or not isinstance(results[index], dict):
            raise WorkflowContractError(f"output index {index} is invalid")
        item = results[index]
        mime_type, data = item.get("mime_type"), item.get("data")
        if mime_type not in SUPPORTED_MIME_TYPES or not isinstance(data, str):
            raise WorkflowContractError(f"output index {index} has unsupported MIME type {mime_type!r}; generate Matplotlib PNG for Grafana-compatible plots")
        selected.append((index, {"mime_type": mime_type, "data": data, **({"display_name": item["display_name"]} if isinstance(item.get("display_name"), str) else {})}))
    return selected


def panel_content(item: dict[str, str]) -> str:
    mime_type, data = item["mime_type"], item["data"]
    if mime_type == "image/png":
        try:
            payload = base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise WorkflowContractError("image/png output is not valid base64") from exc
        if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) > MAX_INLINE_IMAGE_BYTES:
            raise WorkflowContractError("image/png output is invalid or too large for a dashboard")
        return f'<img alt="Sandbox analysis output" style="max-width:100%;height:auto" src="data:image/png;base64,{data}">'
    if len(data.encode()) > MAX_TEXT_BYTES:
        raise WorkflowContractError(f"{mime_type} output is too large for a dashboard")
    if mime_type == "text/html":
        return sanitize_html(data)
    if mime_type == "text/csv":
        rows = list(csv.reader(io.StringIO(data)))
        if len(rows) > 5000 or any(len(row) > 100 for row in rows):
            raise WorkflowContractError("CSV output exceeds dashboard table bounds")
        return "<table><tbody>" + "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows) + "</tbody></table>"
    return f"<pre>{html.escape(data)}</pre>"


def build_dashboard(title: str, selected: list[tuple[int, dict[str, str]]], run_id: str) -> dict[str, Any]:
    panels = []
    for row, (index, item) in enumerate(selected):
        panels.append({
            "id": row + 1,
            "title": str(item.get("display_name") or f"Output {index + 1} · {item['mime_type']}")[:160],
            "type": "text",
            "gridPos": {"x": 0, "y": row * 9, "w": 24, "h": 9},
            "targets": [],
            "fieldConfig": {"defaults": {}, "overrides": []},
            "options": {"mode": "html", "content": panel_content(item)},
        })
    return {
        "uid": ("analysis-" + run_id.replace("_", "-"))[:40],
        "title": title,
        "tags": ["ask-o11y", "sandbox-analysis"],
        "timezone": "browser",
        "schemaVersion": 41,
        "version": 0,
        "panels": panels,
    }


def validate_display_options(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        raise WorkflowContractError("visualization display options are too deeply nested")
    if isinstance(value, dict):
        if len(value) > 100:
            raise WorkflowContractError("visualization display options contain too many keys")
        output = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 64 or key in FORBIDDEN_OPTION_KEYS:
                raise WorkflowContractError(f"visualization display option key {key!r} is not allowed")
            output[key] = validate_display_options(item, depth + 1)
        return output
    if isinstance(value, list):
        if len(value) > 100:
            raise WorkflowContractError("visualization display option list is too long")
        return [validate_display_options(item, depth + 1) for item in value]
    if isinstance(value, str):
        if len(value.encode()) > 2048:
            raise WorkflowContractError("visualization display option string is too long")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise WorkflowContractError("visualization display options must be JSON values")


def fetch_panel_catalog() -> list[dict[str, str]]:
    if not GRAFANA_URL:
        raise RuntimeError("GRAFANA_URL is required")
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    request = urllib.request.Request(GRAFANA_URL + "/api/plugins?type=panel", headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            plugins = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Grafana panel catalog HTTP {exc.code}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana panel catalog request failed: {exc}") from exc
    if not isinstance(plugins, list):
        raise RuntimeError("Grafana panel catalog response is invalid")
    return sorted(
        ({"id": str(item["id"]), "name": str(item.get("name") or item["id"]), "description": str((item.get("info") or {}).get("description") or "")[:240]} for item in plugins if isinstance(item, dict) and item.get("id") and item.get("enabled", True)),
        key=lambda item: item["id"],
    )


def validate_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("grafana_query"), dict):
        raise WorkflowContractError("plan_ref must reference a valid query-plan artifact")
    fields = value.get("selected_fields")
    if not isinstance(fields, list) or not fields or any(not isinstance(field, str) for field in fields):
        raise WorkflowContractError("query plan selected fields are invalid")
    if not isinstance(value.get("datasource_uid"), str) or not isinstance(value.get("datasource_type"), str):
        raise WorkflowContractError("query plan datasource identity is invalid")
    return value


def csv_fields(item: dict[str, str]) -> list[str]:
    data = item.get("data", "")
    if item.get("mime_type") != "text/csv" or len(data.encode()) > MAX_TEXT_BYTES:
        raise WorkflowContractError("native artifact panels currently require a bounded text/csv output")
    try:
        header = next(csv.reader(io.StringIO(data)))
    except (StopIteration, csv.Error) as exc:
        raise WorkflowContractError("CSV output has no valid header") from exc
    if not header or len(header) > 100 or any(not field or len(field) > 160 for field in header) or len(set(header)) != len(header):
        raise WorkflowContractError("CSV output header is invalid")
    return header


def validate_visualizations(plan: dict[str, Any], value: Any, catalog: list[dict[str, str]], execution: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_PANELS:
        raise WorkflowContractError(f"visualizations must contain between 1 and {MAX_PANELS} panel specifications")
    available_types = {item["id"] for item in catalog}
    available_fields = set(plan["selected_fields"])
    visualizations = []
    for spec in value:
        if not isinstance(spec, dict) or set(spec) - {"title", "panel_type", "fields", "options", "field_config", "output_index"}:
            raise WorkflowContractError("visualization spec contains unsupported keys")
        title, panel_type, fields = spec.get("title"), spec.get("panel_type"), spec.get("fields")
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise WorkflowContractError("visualization title must be a non-empty string up to 160 characters")
        if not isinstance(panel_type, str) or panel_type not in available_types:
            raise WorkflowContractError(f"panel type {panel_type!r} is not enabled in Grafana")
        if not isinstance(fields, list) or not fields or len(fields) > 50 or any(not isinstance(field, str) for field in fields) or len(set(fields)) != len(fields):
            raise WorkflowContractError("visualization fields must be a non-empty unique string array")
        output_index = spec.get("output_index")
        if output_index is not None:
            if not isinstance(output_index, int) or isinstance(output_index, bool) or execution is None:
                raise WorkflowContractError("artifact visualization output_index requires an authorized execution_ref")
            outputs = execution_outputs(execution)
            if output_index < 0 or output_index >= len(outputs):
                raise WorkflowContractError(f"artifact visualization output index {output_index} is invalid")
            source_fields = set(csv_fields(outputs[output_index]))
        else:
            source_fields = available_fields
        unknown = [field for field in fields if field not in source_fields]
        if unknown:
            raise WorkflowContractError("visualization fields are not authorized by its source: " + ", ".join(unknown))
        options = validate_display_options(spec.get("options") or {})
        field_config = validate_display_options(spec.get("field_config") or {"defaults": {}, "overrides": []})
        if len(json.dumps({"options": options, "field_config": field_config}).encode()) > MAX_VISUALIZATION_OPTIONS_BYTES:
            raise WorkflowContractError("visualization display options exceed limit")
        visualizations.append({"title": title.strip(), "panel_type": panel_type, "fields": fields, "options": options, "field_config": field_config, **({"output_index": output_index} if output_index is not None else {})})
    return visualizations


def infer_csv_columns(data: str, fields: list[str]) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(data)))[:100]
    columns = []
    for field in fields:
        values = [row.get(field, "") for row in rows if row.get(field, "") != ""]
        value_type = "string"
        if values:
            try:
                for value in values:
                    float(value)
                value_type = "number"
            except ValueError:
                try:
                    for value in values:
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                    value_type = "timestamp"
                except ValueError:
                    value_type = "string"
        columns.append({"selector": field, "text": field, "type": value_type})
    return columns


def build_native_dashboard(title: str, plan: dict[str, Any], visualizations: list[dict[str, Any]], run_id: str, execution: dict[str, Any] | None = None) -> dict[str, Any]:
    datasource = {"type": plan["datasource_type"], "uid": plan["datasource_uid"]}
    query_columns = {item.get("selector"): item for item in plan["grafana_query"].get("columns", []) if isinstance(item, dict) and item.get("selector")}
    panels = []
    for row, spec in enumerate(visualizations):
        if "output_index" in spec:
            if execution is None:
                raise WorkflowContractError("artifact visualization is missing its execution")
            output = execution_outputs(execution)[spec["output_index"]]
            csv_fields(output)
            query = {"refId": chr(ord("A") + row), "datasource": datasource, "type": "csv", "source": "inline", "data": output["data"], "parser": "backend", "format": "table", "columns": infer_csv_columns(output["data"], spec["fields"])}
        else:
            query = json.loads(json.dumps(plan["grafana_query"]))
            query["refId"] = chr(ord("A") + row)
            query["datasource"] = datasource
            query["columns"] = [query_columns[field] for field in spec["fields"] if field in query_columns]
            if len(query["columns"]) != len(spec["fields"]):
                raise WorkflowContractError("query plan cannot project every visualization field")
        panels.append({
            "id": row + 1,
            "title": spec["title"],
            "type": spec["panel_type"],
            "datasource": datasource,
            "gridPos": {"x": 0, "y": row * 9, "w": 24, "h": 9},
            "targets": [query],
            "fieldConfig": spec["field_config"],
            "options": spec["options"],
        })
    dashboard = {
        "uid": ("analysis-" + run_id.replace("_", "-"))[:40],
        "title": title,
        "tags": ["ask-o11y", "native-query"],
        "timezone": "browser",
        "schemaVersion": 41,
        "version": 0,
        "panels": panels,
    }
    time_range = plan.get("time_range")
    if isinstance(time_range, dict) and isinstance(time_range.get("from"), str) and isinstance(time_range.get("to"), str):
        dashboard["time"] = {"from": time_range["from"], "to": time_range["to"]}
    return dashboard


def post_dashboard(dashboard: dict[str, Any]) -> dict[str, Any]:
    if not GRAFANA_URL:
        raise RuntimeError("GRAFANA_URL is required")
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        GRAFANA_URL + "/api/dashboards/db",
        data=json.dumps({"dashboard": dashboard, "overwrite": True, "folderUid": None}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Grafana HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana dashboard request failed: {exc}") from exc


def delete_if_preview(dashboard_uid: str) -> None:
    if not GRAFANA_URL:
        return
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    try:
        with urllib.request.urlopen(urllib.request.Request(GRAFANA_URL + "/api/dashboards/uid/" + dashboard_uid, headers=headers), timeout=30) as response:
            dashboard = json.loads(response.read()).get("dashboard", {})
        if PREVIEW_TAG not in dashboard.get("tags", []):
            return
        with urllib.request.urlopen(urllib.request.Request(GRAFANA_URL + "/api/dashboards/uid/" + dashboard_uid, headers=headers, method="DELETE"), timeout=30):
            return
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError):
        return


def schedule_preview_cleanup(dashboard_uid: str) -> None:
    timer = threading.Timer(APPROVAL_TTL_SECONDS, delete_if_preview, args=(dashboard_uid,))
    timer.daemon = True
    timer.start()


def list_visualization_capabilities(args: dict[str, Any], catalog_fn=fetch_panel_catalog) -> dict[str, Any]:
    step = "list_visualization_capabilities"
    try:
        catalog = catalog_fn()
    except RuntimeError as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Report that Grafana visualization capabilities could not be inspected.")
    return success_response(
        step=step,
        run_id="run_" + uuid.uuid4().hex,
        instruction="Choose only the visualization types needed by the user's current intent; this catalog does not prescribe a dashboard flow or panel order.",
        evidence={"source": "Grafana installed panel plugins", "count": len(catalog)},
        visualizations=catalog,
    )


def prepare_dashboard_write(args: dict[str, Any], catalog_fn=fetch_panel_catalog) -> dict[str, Any]:
    step = "prepare_dashboard_write"
    if forbidden := sorted(FORBIDDEN_INPUT_KEYS & args.keys()):
        return error_response(step=step, error="forbidden raw/invented input keys: " + ", ".join(forbidden), recoverable=False, instruction="Pass only opaque refs and declared display specifications.")
    execution_ref, plan_ref, requested_visualizations = args.get("execution_ref"), args.get("plan_ref"), args.get("visualizations")
    native = plan_ref is not None or requested_visualizations is not None
    if native and (not isinstance(plan_ref, str) or requested_visualizations is None):
        return error_response(step=step, error="plan_ref and visualizations are both required for native panels", recoverable=True, instruction="Use the successful authorized query plan and installed visualization catalog.")
    if not native and not isinstance(execution_ref, str):
        return error_response(step=step, error="execution_ref is required for Sandbox artifact panels", recoverable=True, instruction="Use the successful Sandbox Result Preview.")
    try:
        context = context_from_args(args)
        title = str(args.get("title") or "Ask O11y Analysis")[:160]
        issued_at = time.time()
        if native:
            run_id, parts = parse_artifact_ref(plan_ref)
            if parts != ("query-plan",):
                raise WorkflowContractError("plan_ref must reference query-plan")
            plan = validate_plan(ARTIFACTS.read_json(context, plan_ref))
            execution = None
            if execution_ref is not None:
                if not isinstance(execution_ref, str) or parse_artifact_ref(execution_ref)[1] != ("sandbox-execution",):
                    raise WorkflowContractError("execution_ref must reference sandbox-execution")
                execution = validate_execution(ARTIFACTS.read_json(context, execution_ref))
            visualizations = validate_visualizations(plan, requested_visualizations, catalog_fn(), execution)
            preview = [{"title": item["title"], "panel_type": item["panel_type"], "fields": item["fields"], **({"output_index": item["output_index"]} if "output_index" in item else {"source": "query-plan"})} for item in visualizations]
            approval = {"mode": "native-query", "plan_ref": plan_ref, "visualizations": visualizations, **({"execution_ref": execution_ref} if execution_ref is not None else {})}
            refs = {"plan_ref": plan_ref, **({"execution_ref": execution_ref} if execution_ref is not None else {})}
            dashboard = build_native_dashboard(title, plan, visualizations, run_id, execution)
        else:
            run_id, parts = parse_artifact_ref(execution_ref)
            if parts != ("sandbox-execution",):
                raise WorkflowContractError("execution_ref must reference sandbox-execution")
            execution = validate_execution(ARTIFACTS.read_json(context, execution_ref))
            selected = select_outputs(execution, args.get("output_indices"))
            preview = [{"output_index": index, "mime_type": item["mime_type"]} for index, item in selected]
            approval = {"mode": "sandbox-artifacts", "execution_ref": execution_ref, "output_indices": [index for index, _ in selected]}
            refs = {"execution_ref": execution_ref}
            dashboard = build_dashboard(title, selected, run_id)
        name = "render-approval-" + uuid.uuid4().hex
        approval.update({"status": "prepared", "title": title, "issued_at": issued_at, "expires_at": issued_at + APPROVAL_TTL_SECONDS, "dashboard": dashboard})
        approval_ref = ARTIFACTS.write_json(context, run_id, name, approval)
        refs["approval_ref"] = approval_ref
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the source ref is not authorized for this context.")
    except (WorkflowContractError, RuntimeError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Revise only the unsupported publication specification; do not rerun successful query or analysis work.")
    return success_response(
        step=step,
        run_id=run_id,
        refs=refs,
        instruction="Immediately call create_temporary_dashboard_preview with only this one-time approval_ref. That separate host-approved mutation returns the visible Grafana Preview URL; do not formally publish it in this turn.",
        evidence={"grafana_write": False, "publication_preview": preview, "mode": approval["mode"], "expires_in_seconds": APPROVAL_TTL_SECONDS},
        approval_ref=approval_ref,
        publication_preview=preview,
    )


def create_temporary_dashboard_preview(args: dict[str, Any], post_fn=post_dashboard) -> dict[str, Any]:
    step = "create_temporary_dashboard_preview"
    if forbidden := sorted(FORBIDDEN_INPUT_KEYS & args.keys()):
        return error_response(step=step, error="forbidden raw/invented input keys: " + ", ".join(forbidden), recoverable=False, instruction="Pass only the server-issued approval_ref.")
    approval_ref = args.get("approval_ref")
    if not isinstance(approval_ref, str):
        return error_response(step=step, error="server-issued approval_ref is required", recoverable=True, instruction="Prepare the exact Grafana Preview first.")
    try:
        context = context_from_args(args)
        run_id, parts = parse_artifact_ref(approval_ref)
        if not parts[0].startswith("render-approval-"):
            raise WorkflowContractError("approval_ref is not a renderer capability")
        with APPROVAL_LOCK:
            approval = ARTIFACTS.read_json(context, approval_ref)
            if not isinstance(approval, dict) or approval.get("status") != "prepared" or float(approval.get("expires_at", 0)) < time.time():
                raise WorkflowContractError("approval_ref is invalid, expired, or already used for preview")
            dashboard = approval.get("dashboard")
            if not isinstance(dashboard, dict) or not isinstance(dashboard.get("uid"), str) or not isinstance(dashboard.get("panels"), list):
                raise WorkflowContractError("approval_ref has no valid bound dashboard")
            approval["status"] = "previewing"
            ARTIFACTS.write_json(context, run_id, parts[0], approval)
        try:
            created = post_fn({**dashboard, "tags": [*dashboard.get("tags", []), PREVIEW_TAG]})
        except Exception:
            with APPROVAL_LOCK:
                approval["status"] = "prepared"
                ARTIFACTS.write_json(context, run_id, parts[0], approval)
            raise
        dashboard_uid = str(created.get("uid") or dashboard["uid"])
        relative_url = str(created.get("url") or f"/d/{dashboard_uid}")
        dashboard_slug = relative_url.rstrip("/").split("/")[-1] if relative_url else ""
        dashboard_url = GRAFANA_URL + relative_url
        with APPROVAL_LOCK:
            approval.update({"status": "previewed", "preview_dashboard_uid": dashboard_uid, "preview_dashboard_url": dashboard_url, "previewed_at": time.time()})
            ARTIFACTS.write_json(context, run_id, parts[0], approval)
        if post_fn is post_dashboard:
            schedule_preview_cleanup(dashboard_uid)
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the capability is not authorized for this context.")
    except (WorkflowContractError, RuntimeError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Retry only the temporary preview write from the existing approval_ref; never rerun analysis.")
    return success_response(
        step=step,
        run_id=run_id,
        refs={"approval_ref": approval_ref},
        instruction="Open grafana_preview_url for the user and stop. Ask whether to publish this exact Grafana Preview. Do not call create_dashboard_from_artifacts until a later explicit confirmation.",
        evidence={"grafana_write": True, "temporary_preview": True, "expires_in_seconds": APPROVAL_TTL_SECONDS},
        approval_ref=approval_ref,
        grafana_preview_url=dashboard_url,
        preview_dashboard_uid=dashboard_uid,
        preview_dashboard_slug=dashboard_slug,
        preview_expires_in_seconds=APPROVAL_TTL_SECONDS,
    )


def create_dashboard_from_artifacts(args: dict[str, Any], post_fn=post_dashboard, catalog_fn=fetch_panel_catalog) -> dict[str, Any]:
    step = "create_dashboard_from_artifacts"
    if forbidden := sorted(FORBIDDEN_INPUT_KEYS & args.keys()):
        return error_response(step=step, error="forbidden raw/invented input keys: " + ", ".join(forbidden), recoverable=False, instruction="Pass only the server-issued approval_ref.")
    approval_ref = args.get("approval_ref")
    if not isinstance(approval_ref, str):
        return error_response(step=step, error="server-issued approval_ref is required", recoverable=True, instruction="Prepare the exact publication after the user confirms its Result Preview.")
    try:
        context = context_from_args(args)
        run_id, parts = parse_artifact_ref(approval_ref)
        if not parts[0].startswith("render-approval-"):
            raise WorkflowContractError("approval_ref is not a renderer capability")
        approval = ARTIFACTS.read_json(context, approval_ref)
        if not isinstance(approval, dict) or approval.get("status") != "previewed" or float(approval.get("expires_at", 0)) < time.time():
            raise WorkflowContractError("approval_ref is invalid, expired, or already consumed")
        mode = approval.get("mode")
        if mode == "native-query":
            plan_ref = approval.get("plan_ref")
            if not isinstance(plan_ref, str) or parse_artifact_ref(plan_ref)[0] != run_id:
                raise WorkflowContractError("approval_ref is not bound to this query plan")
            plan = validate_plan(ARTIFACTS.read_json(context, plan_ref))
            execution_ref = approval.get("execution_ref")
            execution = None
            if execution_ref is not None:
                if not isinstance(execution_ref, str):
                    raise WorkflowContractError("approval execution_ref is invalid")
                execution = validate_execution(ARTIFACTS.read_json(context, execution_ref))
            visualizations = validate_visualizations(plan, approval.get("visualizations"), catalog_fn(), execution)
            source_refs = {"plan_ref": plan_ref, **({"execution_ref": execution_ref} if execution_ref is not None else {})}
            panel_evidence = {"panel_types": [item["panel_type"] for item in visualizations]}
        elif mode == "sandbox-artifacts":
            execution_ref = approval.get("execution_ref")
            if not isinstance(execution_ref, str) or parse_artifact_ref(execution_ref)[0] != run_id:
                raise WorkflowContractError("approval_ref is not bound to this execution run")
            execution = validate_execution(ARTIFACTS.read_json(context, execution_ref))
            selected = select_outputs(execution, approval.get("output_indices"))
            source_refs = {"execution_ref": execution_ref}
            panel_evidence = {"mime_types": [item["mime_type"] for _, item in selected]}
        else:
            raise WorkflowContractError("approval_ref publication mode is invalid")
        dashboard = approval.get("dashboard")
        if not isinstance(dashboard, dict) or not isinstance(dashboard.get("uid"), str) or dashboard.get("uid") != approval.get("preview_dashboard_uid") or not isinstance(dashboard.get("panels"), list):
            raise WorkflowContractError("approval_ref has no valid bound Grafana Preview")
        if PREVIEW_TAG in dashboard.get("tags", []):
            raise WorkflowContractError("approval_ref final dashboard still has preview status")
        with APPROVAL_LOCK:
            latest = ARTIFACTS.read_json(context, approval_ref)
            if not isinstance(latest, dict) or latest.get("status") != "previewed":
                raise WorkflowContractError("approval_ref is invalid or already consumed")
            latest["status"] = "consumed"
            latest["consumed_at"] = time.time()
            ARTIFACTS.write_json(context, run_id, parts[0], latest)
        created = post_fn(dashboard)
        dashboard_uid = str(created.get("uid") or dashboard["uid"])
        relative_url = str(created.get("url") or f"/d/{dashboard_uid}")
        dashboard_slug = relative_url.rstrip("/").split("/")[-1] if relative_url else ""
        dashboard_url = GRAFANA_URL + relative_url
        dashboard_ref = ARTIFACTS.write_json(context, run_id, "dashboard", {"dashboard": dashboard, "grafana_response": created, "dashboard_uid": dashboard_uid, "dashboard_slug": dashboard_slug, "dashboard_url": dashboard_url})
        evidence = {
            **source_refs,
            "dashboard_ref": dashboard_ref,
            "dashboard_uid": dashboard_uid,
            "dashboard_slug": dashboard_slug,
            "dashboard_url": dashboard_url,
            "panel_count": len(dashboard["panels"]),
            **panel_evidence,
            "approval_ref": approval_ref,
            "preview_dashboard_uid": approval.get("preview_dashboard_uid"),
            "approval_consumed": True,
        }
        evidence_ref = ARTIFACTS.write_json(context, run_id, "render-evidence", evidence)
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the capability is not authorized for this context.")
    except (WorkflowContractError, RuntimeError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        recoverable = isinstance(exc, WorkflowContractError) and any(word in str(exc) for word in ("expired", "already consumed", "panel type"))
        return error_response(step=step, error=str(exc), recoverable=recoverable, instruction="If recoverable, prepare a fresh capability from the existing successful refs; never rerun analysis solely because publication failed.")
    refs = {**source_refs, "dashboard_ref": dashboard_ref, "evidence_ref": evidence_ref}
    return success_response(
        step=step,
        run_id=run_id,
        refs=refs,
        instruction="The approved Grafana dashboard is now formally published. Preserve dashboard_uid exactly; never substitute the URL slug.",
        evidence=evidence,
        dashboard_uid=dashboard_uid,
        dashboard_slug=dashboard_slug,
        dashboard_url=dashboard_url,
        panel_count=len(dashboard["panels"]),
        final_answer=f"已正式發佈 Grafana dashboard：{dashboard_url}。UID：{dashboard_uid}。Panel count: {len(dashboard['panels'])}。",
    )


TOOLS = [
    {
        "name": "list_visualization_capabilities",
        "description": "Read the currently enabled Grafana panel plugin catalog. Use it when native Grafana visualizations are requested; the returned capabilities are choices, not a prescribed workflow or panel order.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "prepare_dashboard_write",
        "description": "Prepare the exact payload for an expiring Grafana Preview after successful analysis. Pass the intended final title; preview status is server-managed. Use execution_ref alone for Sandbox artifact panels. For native panels use plan_ref plus dynamic visualization specs; optionally include execution_ref and an output_index per visualization to use a named CSV Sandbox output as an inline native data source. This tool does not write Grafana; it returns a short-lived one-time capability for the separately approved preview write.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "execution_ref": {"type": "string"},
                "plan_ref": {"type": "string"},
                "title": {"type": "string", "maxLength": 160},
                "output_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "maxItems": MAX_PANELS, "uniqueItems": True},
                "visualizations": {"type": "array", "minItems": 1, "maxItems": MAX_PANELS, "items": {"type": "object", "additionalProperties": False, "properties": {"title": {"type": "string", "maxLength": 160}, "panel_type": {"type": "string", "maxLength": 64}, "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50, "uniqueItems": True}, "output_index": {"type": "integer", "minimum": 0}, "options": {"type": "object"}, "field_config": {"type": "object"}}, "required": ["title", "panel_type", "fields"]}},
            },
            "anyOf": [{"required": ["execution_ref"]}, {"required": ["plan_ref", "visualizations"]}],
        },
    },
    {
        "name": "create_temporary_dashboard_preview",
        "description": "Create the visible, expiring Grafana Preview bound by prepare_dashboard_write. Call immediately after prepare with only its approval_ref. This temporary mutation requires Ask O11y host approval and must stop before formal publication.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"approval_ref": {"type": "string"}}, "required": ["approval_ref"]},
    },
    {
        "name": "create_dashboard_from_artifacts",
        "description": "Promote the exact Grafana Preview bound by prepare_dashboard_write to a formal dashboard at the same UID. Call only after the user explicitly confirms that visible Grafana Preview; the Ask O11y host must independently approve this mutation before dispatch.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"approval_ref": {"type": "string"}}, "required": ["approval_ref"]},
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
        name = params.get("name")
        tool = next((item for item in TOOLS if item["name"] == name), None)
        if tool is None:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context"}
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            output = error_response(step=str(name), error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Pass only declared tool arguments.")
        elif name == "list_visualization_capabilities":
            output = list_visualization_capabilities(arguments)
        elif name == "prepare_dashboard_write":
            output = prepare_dashboard_write(arguments)
        elif name == "create_temporary_dashboard_preview":
            output = create_temporary_dashboard_preview(arguments)
        else:
            output = create_dashboard_from_artifacts(arguments)
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


def sample_execution() -> dict[str, Any]:
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nexample").decode()
    return {
        "results": [
            {"mime": {"image/png": png}, "display_name": "SHAP plot.png"},
            {"mime": {"text/html": "<table><tr><td>42</td></tr></table><script>bad()</script>"}, "display_name": "Metrics"},
            {"text": "model metrics", "mime": {}, "display_name": "Summary"},
            {"text": None, "mime": {"text/csv": "date,score\n2026-01-01,42\n"}, "display_name": "scores.csv"},
        ],
        "stdout": [],
        "stderr": [],
        "error": None,
    }


def self_check() -> int:
    global ARTIFACTS, GRAFANA_URL
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        GRAFANA_URL = "http://grafana.example"
        context = {"org_id": "1", "user_id": "self-check"}
        other = {"org_id": "1", "user_id": "other"}
        run_id = ARTIFACTS.create_run(context)
        execution_ref = ARTIFACTS.write_json(context, run_id, "sandbox-execution", sample_execution())
        writes: list[dict[str, Any]] = []

        def fake_post(dashboard: dict[str, Any]):
            writes.append(dashboard)
            return {"uid": dashboard["uid"], "url": f"/d/{dashboard['uid']}/self-check"}

        prepared = prepare_dashboard_write({"execution_ref": execution_ref, "title": "SHAP analysis", "_server_context": context})
        prepared_writes = len(writes)
        previewed = create_temporary_dashboard_preview({"approval_ref": prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        preview_writes = len(writes)
        created = create_dashboard_from_artifacts({"approval_ref": prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        replay = create_dashboard_from_artifacts({"approval_ref": prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        plan_ref = ARTIFACTS.write_json(context, run_id, "query-plan", {
            "datasource_uid": "csv-poc",
            "datasource_type": "yesoreyeram-infinity-datasource",
            "selected_fields": ["date", "heat_rate"],
            "time_range": {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T23:59:59Z"},
            "grafana_query": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://csv.example/u1.csv", "parser": "backend", "format": "table", "columns": [{"selector": "date", "text": "date", "type": "timestamp"}, {"selector": "heat_rate", "text": "heat_rate", "type": "number"}]},
        })
        catalog = [{"id": "table", "name": "Table", "description": "table"}, {"id": "timeseries", "name": "Time series", "description": "time"}]
        native_prepared = prepare_dashboard_write({"plan_ref": plan_ref, "execution_ref": execution_ref, "title": "Native analysis", "visualizations": [{"title": "Heat rate", "panel_type": "timeseries", "fields": ["date", "heat_rate"], "field_config": {"defaults": {"unit": "kcal/kWh"}, "overrides": []}}, {"title": "Scores", "panel_type": "table", "fields": ["date", "score"], "output_index": 3}], "_server_context": context}, catalog_fn=lambda: catalog)
        native_previewed = create_temporary_dashboard_preview({"approval_ref": native_prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        native_preview_writes = len(writes)
        native_created = create_dashboard_from_artifacts({"approval_ref": native_prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post, catalog_fn=lambda: catalog)
        forged = create_dashboard_from_artifacts({"approval_ref": f"artifact://{run_id}/render-approval-forged", "_server_context": context}, post_fn=fake_post)
        foreign = prepare_dashboard_write({"execution_ref": execution_ref, "_server_context": other})
        raw = prepare_dashboard_write({"execution_ref": execution_ref, "results": [], "_server_context": context})
        html_content = writes[0]["panels"][1]["options"]["content"] if writes else ""
        native_panel = writes[2]["panels"][0] if len(writes) > 2 else {}
        native_csv_panel = writes[2]["panels"][1] if len(writes) > 2 else {}
        checks = {
            "server_capability_before_preview_write": prepared.get("ok") and prepared_writes == 0 and previewed.get("ok") and native_prepared.get("ok") and native_previewed.get("ok"),
            "grafana_preview_written_before_publish": preview_writes == 1 and previewed.get("preview_dashboard_uid") == writes[0].get("uid") and f"/d/{writes[0].get('uid')}/" in previewed.get("grafana_preview_url", "") and PREVIEW_TAG in writes[0].get("tags", []) and native_preview_writes == 3,
            "named_png_html_text_and_csv_panels": created.get("ok") and created.get("panel_count") == 4 and writes[1]["panels"][0]["title"] == "SHAP plot.png" and writes[1]["panels"][3]["title"] == "scores.csv" and "script" not in html_content and "bad()" not in html_content,
            "native_installed_panels_with_query_targets": native_created.get("ok") and native_panel.get("type") == "timeseries" and len(native_panel.get("targets", [])) == 1 and [column["selector"] for column in native_panel["targets"][0]["columns"]] == ["date", "heat_rate"] and native_csv_panel.get("type") == "table" and native_csv_panel["targets"][0].get("source") == "inline" and [column["selector"] for column in native_csv_panel["targets"][0]["columns"]] == ["date", "score"],
            "structured_dashboard_identity": created.get("dashboard_uid") == writes[0].get("uid") and created.get("dashboard_slug") == "self-check" and "ask-o11y-preview" not in writes[1].get("tags", []),
            "approval_consumed": created.get("evidence", {}).get("approval_consumed") is True and not replay.get("ok"),
            "forged_rejected": not forged.get("ok"),
            "foreign_context_rejected": not foreign.get("ok"),
            "raw_output_rejected": not raw.get("ok"),
        }
        if not all(checks.values()):
            raise SystemExit(json.dumps({"ok": False, "checks": checks, "prepared": prepared, "created": created, "replay": replay}, indent=2))
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
    print(f"grafana-renderer-mcp listening on {runtime_bind_host()}:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
