#!/usr/bin/env python3
"""Approval-gated Grafana rendering for authorized Sandbox output artifacts."""
from __future__ import annotations

import argparse
import base64
import html
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
SERVER_INFO = {"name": "grafana-renderer-mcp", "version": "0.2.0"}
PROTOCOL = "2025-03-26"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
APPROVAL_TTL_SECONDS = 10 * 60
APPROVAL_LOCK = threading.Lock()
MAX_PANELS = 8
MAX_RPC_BODY_BYTES = 128 * 1024
MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
SUPPORTED_MIME_TYPES = {"image/png", "text/html", "text/plain", "application/json"}
FORBIDDEN_INPUT_KEYS = {"execution", "results", "mime", "data", "dashboard", "panels", "approval_confirmed"}


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
    allowed = {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "p", "br", "strong", "em", "code", "pre", "ul", "ol", "li"}
    blocked = {"script", "style", "iframe", "object", "embed", "svg", "math"}

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
        if isinstance(mime, dict) and mime:
            outputs.extend({"mime_type": str(mime_type), "data": data} for mime_type, data in mime.items() if isinstance(data, str))
        elif isinstance(result.get("text"), str):
            outputs.append({"mime_type": "text/plain", "data": result["text"]})
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
        selected.append((index, {"mime_type": mime_type, "data": data}))
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
    return f"<pre>{html.escape(data)}</pre>"


def build_dashboard(title: str, selected: list[tuple[int, dict[str, str]]], run_id: str) -> dict[str, Any]:
    panels = []
    for row, (index, item) in enumerate(selected):
        panels.append({
            "id": row + 1,
            "title": f"Output {index + 1} · {item['mime_type']}",
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


def prepare_dashboard_write(args: dict[str, Any]) -> dict[str, Any]:
    step = "prepare_dashboard_write"
    if forbidden := sorted(FORBIDDEN_INPUT_KEYS & args.keys()):
        return error_response(step=step, error="forbidden raw/invented input keys: " + ", ".join(forbidden), recoverable=False, instruction="Pass only an authorized execution_ref and display selection.")
    ref = args.get("execution_ref")
    if not isinstance(ref, str):
        return error_response(step=step, error="execution_ref is required", recoverable=False, instruction="Use an opaque ref returned by Sandbox Analysis.")
    try:
        context = context_from_args(args)
        run_id, parts = parse_artifact_ref(ref)
        if parts[0] != "sandbox-execution":
            raise WorkflowContractError("execution_ref must reference sandbox-execution")
        execution = validate_execution(ARTIFACTS.read_json(context, ref))
        selected = select_outputs(execution, args.get("output_indices"))
        title = str(args.get("title") or "Sandbox Analysis")[:160]
        preview = [{"output_index": index, "mime_type": item["mime_type"]} for index, item in selected]
        issued_at = time.time()
        name = "render-approval-" + uuid.uuid4().hex
        approval_ref = ARTIFACTS.write_json(context, run_id, name, {
            "status": "pending",
            "execution_ref": ref,
            "title": title,
            "output_indices": [index for index, _ in selected],
            "issued_at": issued_at,
            "expires_at": issued_at + APPROVAL_TTL_SECONDS,
        })
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the execution is not authorized for this context.")
    except (WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Generate a supported Sandbox output or correct the selection.")
    return success_response(
        step=step,
        run_id=run_id,
        refs={"execution_ref": ref, "approval_ref": approval_ref},
        instruction="Preview these exact panels, then immediately call create_dashboard_from_artifacts with only this one-time approval_ref. The Ask O11y host will pause that write call for user approval; do not request a separate chat turn.",
        evidence={"grafana_write": False, "panel_preview": preview, "expires_in_seconds": APPROVAL_TTL_SECONDS},
        approval_ref=approval_ref,
        panel_preview=preview,
    )


def create_dashboard_from_artifacts(args: dict[str, Any], post_fn=post_dashboard) -> dict[str, Any]:
    step = "create_dashboard_from_artifacts"
    if forbidden := sorted(FORBIDDEN_INPUT_KEYS & args.keys()):
        return error_response(step=step, error="forbidden raw/invented input keys: " + ", ".join(forbidden), recoverable=False, instruction="Pass only the server-issued approval_ref.")
    approval_ref = args.get("approval_ref")
    if not isinstance(approval_ref, str):
        return error_response(step=step, error="server-issued approval_ref is required", recoverable=False, instruction="Prepare and approve the exact dashboard first.")
    try:
        context = context_from_args(args)
        run_id, parts = parse_artifact_ref(approval_ref)
        if not parts[0].startswith("render-approval-"):
            raise WorkflowContractError("approval_ref is not a renderer capability")
        approval = ARTIFACTS.read_json(context, approval_ref)
        if not isinstance(approval, dict) or approval.get("status") != "pending" or float(approval.get("expires_at", 0)) < time.time():
            raise WorkflowContractError("approval_ref is invalid, expired, or already consumed")
        execution_ref = approval.get("execution_ref")
        if not isinstance(execution_ref, str) or parse_artifact_ref(execution_ref)[0] != run_id:
            raise WorkflowContractError("approval_ref is not bound to this execution run")
        execution = validate_execution(ARTIFACTS.read_json(context, execution_ref))
        selected = select_outputs(execution, approval.get("output_indices"))
        dashboard = build_dashboard(str(approval.get("title") or "Sandbox Analysis"), selected, run_id)
        with APPROVAL_LOCK:
            latest = ARTIFACTS.read_json(context, approval_ref)
            if not isinstance(latest, dict) or latest.get("status") != "pending":
                raise WorkflowContractError("approval_ref is invalid or already consumed")
            latest["status"] = "consumed"
            latest["consumed_at"] = time.time()
            ARTIFACTS.write_json(context, run_id, parts[0], latest)
        created = post_fn(dashboard)
        dashboard_url = GRAFANA_URL + str(created.get("url", ""))
        dashboard_ref = ARTIFACTS.write_json(context, run_id, "dashboard", {"dashboard": dashboard, "grafana_response": created})
        evidence = {
            "execution_ref": execution_ref,
            "dashboard_ref": dashboard_ref,
            "dashboard_url": dashboard_url,
            "panel_count": len(dashboard["panels"]),
            "mime_types": [item["mime_type"] for _, item in selected],
            "approval_ref": approval_ref,
            "approval_consumed": True,
        }
        evidence_ref = ARTIFACTS.write_json(context, run_id, "render-evidence", evidence)
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the capability is not authorized for this context.")
    except (WorkflowContractError, RuntimeError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Renderer refused the artifact dashboard write.")
    return success_response(
        step=step,
        run_id=run_id,
        refs={"execution_ref": execution_ref, "dashboard_ref": dashboard_ref, "evidence_ref": evidence_ref},
        instruction="Only now may Ask O11y state that the Grafana dashboard exists.",
        evidence=evidence,
        dashboard_url=dashboard_url,
        panel_count=len(dashboard["panels"]),
        final_answer=f"已建立 Grafana dashboard：{dashboard_url}。Panel count: {len(dashboard['panels'])}。",
    )


TOOLS = [
    {
        "name": "prepare_dashboard_write",
        "description": "Validate authorized opaque Sandbox outputs and issue a short-lived one-time capability bound to the exact title and output selection. This tool never writes Grafana. Supports PNG, sanitized HTML, plain text, and JSON; ask Sandbox to produce Matplotlib PNG for SHAP or other plots. In a confirmed dashboard run, immediately pass its approval_ref to create_dashboard_from_artifacts; the Ask O11y host pauses that write call for user approval, so do not request another chat turn.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"execution_ref": {"type": "string"}, "title": {"type": "string", "maxLength": 160}, "output_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "maxItems": MAX_PANELS, "uniqueItems": True}}, "required": ["execution_ref"]},
    },
    {
        "name": "create_dashboard_from_artifacts",
        "description": "Approval-gated Grafana write using the exact one-time approval_ref from prepare_dashboard_write. The Ask O11y host must obtain user approval before dispatching this tool.",
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
        elif name == "prepare_dashboard_write":
            output = prepare_dashboard_write(arguments)
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
    return {"results": [{"mime": {"image/png": png}}, {"mime": {"text/html": "<table><tr><td>42</td></tr></table><script>bad()</script>"}}, {"text": "model metrics", "mime": {}}], "stdout": [], "stderr": [], "error": None}


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
            return {"url": "/d/self-check"}

        prepared = prepare_dashboard_write({"execution_ref": execution_ref, "title": "SHAP analysis", "_server_context": context})
        created = create_dashboard_from_artifacts({"approval_ref": prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        replay = create_dashboard_from_artifacts({"approval_ref": prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        forged = create_dashboard_from_artifacts({"approval_ref": f"artifact://{run_id}/render-approval-forged", "_server_context": context}, post_fn=fake_post)
        foreign = prepare_dashboard_write({"execution_ref": execution_ref, "_server_context": other})
        raw = prepare_dashboard_write({"execution_ref": execution_ref, "results": [], "_server_context": context})
        html_content = writes[0]["panels"][1]["options"]["content"] if writes else ""
        checks = {
            "preview_no_write": prepared.get("ok") and len(writes) == 1,
            "png_html_and_text_panels": created.get("ok") and created.get("panel_count") == 3 and "script" not in html_content and "bad()" not in html_content,
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
