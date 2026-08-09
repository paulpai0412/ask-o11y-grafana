#!/usr/bin/env python3
"""Grafana Renderer workflow-node MCP.

Reads full AnalysisResult artifacts, renders their recommended_panels into a
Grafana dashboard, writes renderer evidence artifacts, and returns a final_answer
that ask-o11y should use verbatim.
"""
from __future__ import annotations

import argparse
import base64
import csv
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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
ArtifactAuthError = artifact_store.ArtifactAuthError
authenticate_headers = mcp_security.authenticate_headers
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
ArtifactStore = artifact_store.ArtifactStore
WorkflowContractError = workflow_node.WorkflowContractError
error_response = workflow_node.error_response
parse_artifact_ref = workflow_node.parse_artifact_ref
success_response = workflow_node.success_response

try:
    PORT = int(os.environ.get("GRAFANA_RENDERER_MCP_PORT", "8773"))
except ValueError:
    PORT = 8773
SERVER_INFO = {"name": "grafana-renderer-mcp", "version": "0.1.0"}
PROTOCOL = "2025-03-26"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
RENDER_DATASOURCE_UID = os.environ.get("GRAFANA_RENDERER_DATASOURCE_UID", "csv-poc")
RENDER_DATASOURCE_TYPE = os.environ.get("GRAFANA_RENDERER_DATASOURCE_TYPE", "yesoreyeram-infinity-datasource")
CHART_OUTPUT_DIR = Path(os.environ.get("ANALYSIS_CSV_OUTPUT_DIR", ROOT / "data" / "poc" / "analysis"))
CHART_URL_BASE = os.environ.get("ANALYSIS_CSV_URL_BASE", "")
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
FORBIDDEN_INPUT_KEYS = {"analysis_result", "analysis", "findings", "data_frames", "recommended_panels", "dashboard", "panels", "mock", "canned_output", "precomputed_result"}
APPROVAL_TTL_SECONDS = 10 * 60
APPROVAL_LOCK = threading.Lock()


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
    raise WorkflowContractError("verified artifact context is required")


def forbidden_input_error(args: dict[str, Any]) -> str | None:
    present = sorted(key for key in FORBIDDEN_INPUT_KEYS if key in args)
    if present:
        return "renderer reads AnalysisResult artifacts only; forbidden invented/raw input keys: " + ", ".join(present)
    return None


def frame_to_rows(frame: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    try:
        fields = [str(field["name"]) for field in frame["schema"]["fields"]]
        values = frame["data"]["values"]
    except (KeyError, TypeError) as exc:
        raise WorkflowContractError("data_frame must use Grafana DataFrame JSON shape") from exc
    if not isinstance(values, list) or len(values) != len(fields):
        raise WorkflowContractError("data_frame data.values must have one column per field")
    lengths = [len(column) for column in values]
    if len(set(lengths)) != 1:
        raise WorkflowContractError("data_frame columns must have consistent lengths")
    row_count = lengths[0] if lengths else 0
    return fields, [[values[c][r] for c in range(len(fields))] for r in range(row_count)]


def write_frame_csv(frame: dict[str, Any], path: Path) -> None:
    fields, rows = frame_to_rows(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)


def field_type(field: dict[str, Any]) -> str:
    value = field.get("type", "string")
    return "timestamp" if value == "time" else str(value or "string")


def infinity_target(ref_id: str, frame: dict[str, Any], url: str, columns: list[str] | None = None) -> dict[str, Any]:
    try:
        fields = frame["schema"]["fields"]
    except (KeyError, TypeError) as exc:
        raise WorkflowContractError("data_frame schema.fields is required") from exc
    selected = [field for field in fields if columns is None or field.get("name") in columns]
    if not selected:
        raise WorkflowContractError("recommended panel selected no columns")
    return {
        "refId": ref_id,
        "datasource": {"uid": RENDER_DATASOURCE_UID, "type": RENDER_DATASOURCE_TYPE},
        "type": "csv",
        "source": "url",
        "url": url,
        "parser": "backend",
        "format": "table",
        "url_options": {"method": "GET", "data": ""},
        "csv_options": {"delimiter": ",", "skip_empty_lines": True},
        "columns": [{"selector": field.get("name"), "text": field.get("name"), "type": field_type(field)} for field in selected],
    }


def grafana_panel(panel_id: int, spec: dict[str, Any], frame: dict[str, Any], frame_url: str) -> dict[str, Any]:
    panel_type = str(spec.get("type") or "table")
    field_names = {str(field.get("name")) for field in frame.get("schema", {}).get("fields", []) if isinstance(field, dict)}
    width = 24 if panel_type in {"table", "heatmap", "correlation_heatmap"} else 12
    row = (panel_id - 1) // 2
    col = (panel_id - 1) % 2
    columns = None
    options: dict[str, Any] = {}
    field_config: dict[str, Any] = {"defaults": {}, "overrides": []}
    if panel_type in {"heatmap", "correlation_heatmap"}:
        source, target, value = (spec.get(key) for key in ("source", "target", "value"))
        if not all(isinstance(field, str) and field in field_names for field in (source, target, value)):
            raise WorkflowContractError("heatmap requires valid source, target, and value fields")
        columns = [str(source), str(target), str(value)]
        rendered_type = str(spec.get("plugin_id") or "esnet-matrix-panel")
        options = {
            "sourceField": source,
            "targetField": target,
            "valueField": value,
            "sourceText": "Parameter",
            "targetText": "Parameter",
            "valueText": "Correlation",
            "cellSize": 32,
            "cellPadding": 4,
            "txtLength": 40,
            "txtSize": 11,
            "showLegend": True,
            "legendType": "range",
            "inputList": False,
            "addUrl": False,
            "nullColor": "#808080",
            "defaultColor": "#808080",
        }
        field_config = {"defaults": {"min": -1, "max": 1, "decimals": 3, "color": {"mode": "continuous-RdYlGr"}}, "overrides": []}
    else:
        x_field = spec.get("x")
        y_fields = spec.get("y") if isinstance(spec.get("y"), list) else []
        columns = [str(x_field), *[str(field) for field in y_fields]] if x_field and y_fields else None
        rendered_type = "barchart" if panel_type in {"barchart", "bar"} else "timeseries" if panel_type in {"timeseries", "time_series"} else "xychart" if panel_type == "scatter" else "table"
        if panel_type == "scatter":
            options = {"seriesMapping": "auto", "series": [], "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "single", "sort": "none"}}
    return {
        "id": panel_id,
        "title": str(spec.get("title") or f"Panel {panel_id}"),
        "type": rendered_type,
        "gridPos": {"x": 0 if width == 24 else col * 12, "y": row * 8, "w": width, "h": 8},
        "datasource": {"uid": RENDER_DATASOURCE_UID, "type": RENDER_DATASOURCE_TYPE},
        "targets": [infinity_target("A", frame, frame_url, columns)],
        "fieldConfig": field_config,
        "options": options,
    }


def validate_analysis_result(analysis: Any) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        raise WorkflowContractError("AnalysisResult artifact must be an object")
    required = ["analysis_type", "title", "summary", "severity", "time_range", "subject", "findings", "data_frames", "recommended_panels"]
    missing = [key for key in required if key not in analysis]
    if missing:
        raise WorkflowContractError("AnalysisResult missing required fields: " + ", ".join(missing))
    if not isinstance(analysis["data_frames"], list) or not analysis["data_frames"]:
        raise WorkflowContractError("AnalysisResult.data_frames must be a non-empty list")
    if not isinstance(analysis["recommended_panels"], list) or not analysis["recommended_panels"]:
        raise WorkflowContractError("AnalysisResult.recommended_panels must be a non-empty list")
    if not isinstance(analysis["findings"], list) or not analysis["findings"]:
        raise WorkflowContractError("AnalysisResult.findings must be a non-empty list")
    raw_details = analysis.get("details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    raw_method_refs = details.get("method_result_refs")
    method_refs: dict[str, Any] = raw_method_refs if isinstance(raw_method_refs, dict) else {}
    raw_method_source = details.get("method_source")
    method_source: dict[str, Any] = raw_method_source if isinstance(raw_method_source, dict) else {}
    if not method_refs:
        raise WorkflowContractError("AnalysisResult must include at least one method result ref")
    if bool(method_source.get("runtime_agent")) or bool(method_source.get("runtime_llm")) or bool(method_source.get("runtime_skill")):
        raise WorkflowContractError("AnalysisResult method provenance must not use a runtime agent, LLM, or skill")
    for key in ["implementation", "method", "algorithm", "algorithm_version"]:
        if not method_source.get(key):
            raise WorkflowContractError(f"AnalysisResult missing method_source.{key}")
    frames = {}
    for frame in analysis["data_frames"]:
        if not isinstance(frame, dict) or not isinstance(frame.get("name"), str):
            raise WorkflowContractError("each data_frame requires a name")
        frame_to_rows(frame)
        frames[frame["name"]] = frame
    supported_panel_types = {"table", "timeseries", "time_series", "barchart", "bar", "scatter", "heatmap", "correlation_heatmap"}
    for spec in analysis["recommended_panels"]:
        if not isinstance(spec, dict):
            raise WorkflowContractError("each recommended_panel must be an object")
        panel_type = str(spec.get("type") or "table")
        if panel_type not in supported_panel_types:
            raise WorkflowContractError(f"unsupported recommended_panel type: {panel_type}")
        frame_name = spec.get("data_frame")
        if not isinstance(frame_name, str) or frame_name not in frames:
            raise WorkflowContractError(f"recommended_panel references unknown data_frame: {frame_name}")
        field_names = {str(field.get("name")) for field in frames[frame_name].get("schema", {}).get("fields", []) if isinstance(field, dict)}
        if panel_type in {"heatmap", "correlation_heatmap"}:
            required_fields = [spec.get(key) for key in ["source", "target", "value"]]
            if not all(isinstance(field, str) and field in field_names for field in required_fields):
                raise WorkflowContractError("heatmap panel requires valid source, target, and value fields")
        elif panel_type != "table":
            x_field, y_fields = spec.get("x"), spec.get("y")
            if not isinstance(x_field, str) or x_field not in field_names or not isinstance(y_fields, list) or not y_fields or any(not isinstance(field, str) or field not in field_names for field in y_fields):
                raise WorkflowContractError(f"{panel_type} panel requires valid x and non-empty y fields")
    return analysis


def validate_method_artifacts(context: dict[str, str], analysis: dict[str, Any], analysis_run_id: str) -> None:
    raw_details = analysis.get("details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    raw_method_refs = details.get("method_result_refs")
    method_refs: dict[str, Any] = raw_method_refs if isinstance(raw_method_refs, dict) else {}
    if not method_refs:
        raise WorkflowContractError("AnalysisResult method_result_refs must not be empty")
    for name, ref in method_refs.items():
        if not isinstance(ref, str):
            raise WorkflowContractError(f"method_result_ref for {name} must be an artifact ref")
        method_run_id, _ = parse_artifact_ref(ref)
        if method_run_id != analysis_run_id:
            raise WorkflowContractError(f"method result artifact {name} must belong to the analysis run")
        artifact = ARTIFACTS.read_json(context, ref)
        if not isinstance(artifact, dict):
            raise WorkflowContractError(f"method result artifact {name} must be an object")
        raw_source = artifact.get("method_source")
        source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
        if bool(source.get("runtime_agent")) or bool(source.get("runtime_llm")) or bool(source.get("runtime_skill")):
            raise WorkflowContractError(f"method result artifact {name} used a forbidden runtime")
        for key in ["implementation", "method", "algorithm", "algorithm_version"]:
            if not source.get(key):
                raise WorkflowContractError(f"method result artifact {name} missing method_source.{key}")
        if source.get("mode") == "k_dense_skill_guided_deterministic":
            raw_skills = source.get("skills")
            skills: list[Any] = raw_skills if isinstance(raw_skills, list) else []
            if not skills or any(not isinstance(item, dict) or not item.get("name") or not item.get("sha256") for item in skills):
                raise WorkflowContractError(f"method result artifact {name} missing skill hashes")
        else:
            raw_libraries = source.get("libraries")
            libraries: list[Any] = raw_libraries if isinstance(raw_libraries, list) else []
            if not libraries or any(not isinstance(item, dict) or not item.get("name") or not item.get("version") for item in libraries):
                raise WorkflowContractError(f"method result artifact {name} missing deterministic library versions")


def validate_chart_url_base(chart_url_base: str) -> str:
    parsed = urllib.parse.urlsplit(chart_url_base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise WorkflowContractError("ANALYSIS_CSV_URL_BASE must be an http(s) origin/path without query or fragment")
    return chart_url_base.rstrip("/")


def build_dashboard(analysis: dict[str, Any], run_id: str, chart_url_base: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not chart_url_base:
        raise WorkflowContractError("ANALYSIS_CSV_URL_BASE is required for renderer output")
    frames = {frame["name"]: frame for frame in analysis["data_frames"]}
    run_chart_dir = CHART_OUTPUT_DIR / run_id
    chart_base = validate_chart_url_base(chart_url_base) + f"/{run_id}"
    written = []
    for frame_name, frame in frames.items():
        path = run_chart_dir / f"{frame_name}.csv"
        write_frame_csv(frame, path)
        written.append({"data_frame": frame_name, "url": f"{chart_base}/{frame_name}.csv"})
    panels = []
    for idx, spec in enumerate(analysis["recommended_panels"], 1):
        frame_name = str(spec["data_frame"])
        panels.append(grafana_panel(idx, spec, frames[frame_name], f"{chart_base}/{frame_name}.csv"))
    uid = "analysis-" + run_id.replace("_", "-")
    return {
        "uid": uid[:40],
        "title": str(analysis.get("title") or "Analysis Dashboard"),
        "tags": ["ask-o11y", "analysis", str(analysis.get("analysis_type"))],
        "timezone": "browser",
        "schemaVersion": 41,
        "version": 0,
        "panels": panels,
    }, written


def post_dashboard(dashboard: dict[str, Any]) -> dict[str, Any]:
    if not GRAFANA_URL:
        raise RuntimeError("GRAFANA_URL is required")
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    body = {"dashboard": dashboard, "overwrite": True, "folderUid": None}
    req = urllib.request.Request(
        GRAFANA_URL + "/api/dashboards/db",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Grafana HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana dashboard request failed: {exc}") from exc


def final_answer(analysis: dict[str, Any], dashboard_url: str, panel_count: int) -> str:
    summary = str(analysis.get("summary") or "")
    limitations = " ".join(str(item) for item in analysis.get("limitations", []) if item)
    causality = "相關性不是因果"
    if causality in limitations and causality not in summary:
        summary = summary.rstrip("。") + f"。{causality}。"
    return f"已完成分析並建立 Grafana dashboard：{dashboard_url}。Panel count: {panel_count}。{summary}"


def prepare_dashboard_write(args: dict[str, Any]) -> dict[str, Any]:
    step = "prepare_dashboard_write"
    err = forbidden_input_error(args)
    if err:
        return error_response(step=step, error=err, recoverable=False, instruction="Stop; renderer only accepts an authorized AnalysisResult ref.")
    ref = args.get("analysis_result_ref")
    if not isinstance(ref, str):
        return error_response(step=step, error="analysis_result_ref is required", recoverable=False, instruction="Stop; call the selected domain analysis first.")
    try:
        context = context_from_args(args)
        run_id, parts = parse_artifact_ref(ref)
        if not parts[0].startswith("analysis"):
            raise WorkflowContractError("analysis_result_ref must reference an analysis artifact")
        analysis = validate_analysis_result(ARTIFACTS.read_json(context, ref))
        validate_method_artifacts(context, analysis, run_id)
        approval_name = "render-approval-" + uuid.uuid4().hex
        issued_at = time.time()
        approval_ref = ARTIFACTS.write_json(context, run_id, approval_name, {"status": "pending", "analysis_result_ref": ref, "issued_at": issued_at, "expires_at": issued_at + APPROVAL_TTL_SECONDS})
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the analysis result is not authorized for this context.")
    except (WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; renderer refused to prepare this write.")
    return success_response(step=step, run_id=run_id, refs={"analysis_result_ref": ref, "approval_ref": approval_ref}, instruction="Pass this exact one-time approval_ref to create_dashboard_from_analysis. The Ask O11y host must still obtain user approval before executing that write tool.", evidence={"analysis_result_ref": ref, "approval_ref": approval_ref, "grafana_write": False, "expires_in_seconds": APPROVAL_TTL_SECONDS}, analysis_result_ref=ref, approval_ref=approval_ref)


def render_analysis(args: dict[str, Any], post_fn=post_dashboard) -> dict[str, Any]:
    step = "create_dashboard_from_analysis"
    err = forbidden_input_error(args)
    if err:
        return error_response(step=step, error=err, recoverable=False, instruction="Stop; renderer only accepts analysis_result_ref and a server-issued approval_ref.")
    try:
        context = context_from_args(args)
    except WorkflowContractError as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; artifact context is required.")
    ref = args.get("analysis_result_ref")
    approval_ref = args.get("approval_ref")
    if not isinstance(ref, str) or not isinstance(approval_ref, str):
        return error_response(step=step, error="analysis_result_ref and server-issued approval_ref are required", recoverable=False, instruction="Stop; prepare the dashboard write and wait for host approval.")
    try:
        run_id, parts = parse_artifact_ref(ref)
        approval_run_id, approval_parts = parse_artifact_ref(approval_ref)
        if not parts[0].startswith("analysis"):
            raise WorkflowContractError("analysis_result_ref must reference an analysis artifact")
        if approval_run_id != run_id or not approval_parts[0].startswith("render-approval-"):
            raise WorkflowContractError("approval_ref is not bound to this analysis run")
        analysis = validate_analysis_result(ARTIFACTS.read_json(context, ref))
        validate_method_artifacts(context, analysis, run_id)
        with APPROVAL_LOCK:
            approval = ARTIFACTS.read_json(context, approval_ref)
            if not isinstance(approval, dict) or approval.get("status") != "pending" or approval.get("analysis_result_ref") != ref:
                raise WorkflowContractError("approval_ref is invalid, mismatched, or already consumed")
            if float(approval.get("expires_at", 0)) < time.time():
                raise WorkflowContractError("approval_ref has expired")
            approval["status"] = "consumed"
            approval["consumed_at"] = time.time()
            ARTIFACTS.write_json(context, run_id, approval_parts[0], approval)
        dashboard, chart_files = build_dashboard(analysis, run_id, CHART_URL_BASE)
        created = post_fn(dashboard)
        dashboard_url = GRAFANA_URL + str(created.get("url", ""))
        dashboard_ref = ARTIFACTS.write_json(context, run_id, "dashboard", {"dashboard": dashboard, "grafana_response": created})
        evidence = {
            "analysis_result_ref": ref,
            "dashboard_ref": dashboard_ref,
            "dashboard_url": dashboard_url,
            "panel_count": len(dashboard["panels"]),
            "chart_files": chart_files,
            "rendered_from_recommended_panels": True,
            "approval_ref": approval_ref,
            "approval_consumed": True,
        }
        evidence_ref = ARTIFACTS.write_json(context, run_id, "render-evidence", evidence)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the analysis_result_ref is not authorized for this context.")
    except (WorkflowContractError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; renderer refused the analysis artifact.")
    answer = final_answer(analysis, dashboard_url, len(dashboard["panels"]))
    return success_response(
        step=step,
        run_id=run_id,
        refs={"analysis_result_ref": ref, "dashboard_ref": dashboard_ref, "evidence_ref": evidence_ref},
        instruction="Use final_answer verbatim in the user-facing response unless the user explicitly asks to translate or shorten it.",
        evidence=evidence,
        dashboard_url=dashboard_url,
        panel_count=len(dashboard["panels"]),
        final_answer=answer,
    )


TOOLS = [
    {
        "name": "prepare_dashboard_write",
        "description": "Validate an authorized AnalysisResult and issue a short-lived, one-time server capability for the exact dashboard write. This tool never mutates Grafana. Use its exact approval_ref only after the Ask O11y host obtains user approval for create_dashboard_from_analysis.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"analysis_result_ref": {"type": "string"}}, "required": ["analysis_result_ref"]},
    },
    {
        "name": "create_dashboard_from_analysis",
        "description": "Approval-gated Grafana write. Requires the exact short-lived one-time approval_ref previously issued for this AnalysisResult; the server rejects forged, mismatched, expired, or replayed capabilities. The Ask O11y host must additionally approve this write tool call.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"analysis_result_ref": {"type": "string"}, "approval_ref": {"type": "string"}}, "required": ["analysis_result_ref", "approval_ref"]},
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
            out = error_response(step=str(name), error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only arguments declared by this tool schema.")
        elif name == "prepare_dashboard_write":
            out = prepare_dashboard_write(arguments)
        else:
            out = render_analysis(arguments)
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": bool(isinstance(out, dict) and not out.get("ok"))})
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
        msgs = payload if isinstance(payload, list) else [payload]
        replies = [reply for msg in msgs if (reply := handle_rpc(inject_header_context(msg, self.headers))) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):
        sys.stderr.write("grafana-renderer-mcp " + format % args + "\n")


def sample_analysis() -> dict[str, Any]:
    source = {
        "mode": "deterministic_library",
        "implementation": "renderer-self-check",
        "method": "profile",
        "algorithm": "declarative_table",
        "algorithm_version": "1",
        "libraries": [{"name": "pandas", "version": "3.0.5"}],
        "runtime_agent": False,
        "runtime_llm": False,
        "runtime_skill": False,
    }
    return {
        "analysis_type": "engineering_profile",
        "title": "Renderer Self-check",
        "summary": "Rendered deterministic profile evidence. 相關性不是因果。",
        "severity": "info",
        "time_range": {"from": None, "to": None},
        "subject": {"domain": "engineering", "fields": ["value"]},
        "findings": [{"level": "info", "message": "Profile is ready."}],
        "data_frames": [{"name": "profile", "schema": {"fields": [{"name": "field", "type": "string"}, {"name": "count", "type": "number"}]}, "data": {"values": [["value"], [2]]}}],
        "recommended_panels": [{"title": "Profile", "type": "table", "data_frame": "profile"}],
        "details": {"method_results": {"profile": {"method": "profile", "method_source": source}}, "method_result_refs": {}, "method_source": source},
    }


def self_check() -> None:
    global ARTIFACTS, CHART_OUTPUT_DIR, CHART_URL_BASE, GRAFANA_URL
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        CHART_OUTPUT_DIR = Path(tmp) / "chart-csv"
        CHART_URL_BASE = "http://example.invalid/analysis"
        GRAFANA_URL = "http://grafana.example.invalid"
        context = {"org_id": os.environ.get("ANALYSIS_CONTEXT_ORG_ID", "1"), "user_id": os.environ.get("ANALYSIS_CONTEXT_USER_ID", "self-check-renderer")}
        other_context = {"org_id": "1", "user_id": "other"}
        run_id = ARTIFACTS.create_run(context, "run_renderer01")
        analysis = sample_analysis()
        raw_details = analysis.get("details")
        details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
        raw_method_results = details.get("method_results")
        method_results: dict[str, Any] = raw_method_results if isinstance(raw_method_results, dict) else {}
        details["method_result_refs"] = {
            name: ARTIFACTS.write_json(context, run_id, "method-" + str(name).replace("_", "-"), result)
            for name, result in method_results.items()
        }
        analysis["details"] = details
        analysis_ref = ARTIFACTS.write_json(context, run_id, "analysis-result", analysis)

        def fake_post(dashboard: dict[str, Any]) -> dict[str, Any]:
            return {"uid": dashboard["uid"], "url": f"/d/{dashboard['uid']}/self-check"}

        prepared = prepare_dashboard_write({"analysis_result_ref": analysis_ref, "_server_context": context})
        if not prepared.get("ok"):
            raise RuntimeError(str(prepared))
        approval_ref = prepared["approval_ref"]
        out = render_analysis({"analysis_result_ref": analysis_ref, "approval_ref": approval_ref, "_server_context": context}, post_fn=fake_post)
        if not out.get("ok") or out.get("panel_count", 0) < 1:
            raise RuntimeError(str(out))
        if "相關性不是因果" not in out.get("final_answer", ""):
            raise RuntimeError("final_answer lost causality warning")
        dashboard_artifact = ARTIFACTS.read_json(context, out["refs"]["dashboard_ref"])
        if len(dashboard_artifact["dashboard"]["panels"]) != out["panel_count"]:
            raise RuntimeError("dashboard artifact panel mismatch")
        generic_source = {
            "mode": "deterministic_library",
            "implementation": "engineering-analysis-mcp.v1",
            "method": "pairwise_correlation",
            "algorithm": "pandas.DataFrame.corr.pearson",
            "algorithm_version": "pandas-3.0.5",
            "libraries": [{"name": "pandas", "version": "3.0.5"}],
            "runtime_agent": False,
            "runtime_llm": False,
            "runtime_skill": False,
        }
        correlation_method_ref = ARTIFACTS.write_json(context, run_id, "method-engineering-correlation", {"method": "pairwise_correlation", "method_source": generic_source})
        correlation_analysis = {
            "analysis_type": "engineering_correlation",
            "title": "Correlation Matrix",
            "summary": "Pairwise correlation matrix. 相關性不是因果。",
            "severity": "info",
            "time_range": {"from": None, "to": None},
            "subject": {"domain": "engineering"},
            "findings": [{"level": "info", "message": "Correlation matrix ready."}],
            "data_frames": [{"name": "correlation_matrix", "schema": {"fields": [{"name": "source", "type": "string"}, {"name": "target", "type": "string"}, {"name": "correlation", "type": "number"}]}, "data": {"values": [["a", "a", "b", "b"], ["a", "b", "a", "b"], [1.0, 0.5, 0.5, 1.0]]}}],
            "recommended_panels": [{"title": "Correlation Heatmap", "type": "heatmap", "plugin_id": "esnet-matrix-panel", "data_frame": "correlation_matrix", "source": "source", "target": "target", "value": "correlation"}],
            "details": {"method_result_refs": {"correlation": correlation_method_ref}, "method_source": generic_source},
        }
        correlation_ref = ARTIFACTS.write_json(context, run_id, "analysis-engineering-correlation", correlation_analysis)
        correlation_prepared = prepare_dashboard_write({"analysis_result_ref": correlation_ref, "_server_context": context})
        correlation_out = render_analysis({"analysis_result_ref": correlation_ref, "approval_ref": correlation_prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        correlation_dashboard = ARTIFACTS.read_json(context, correlation_out["refs"]["dashboard_ref"])
        if not correlation_out.get("ok") or correlation_dashboard["dashboard"]["panels"][0].get("type") != "esnet-matrix-panel":
            raise RuntimeError(f"correlation heatmap rendering failed: {correlation_out}")
        bad_raw = render_analysis({"analysis_result_ref": analysis_ref, "approval_ref": approval_ref, "_server_context": context, "analysis_result": {}}, post_fn=fake_post)
        bad_missing = render_analysis({"_server_context": context}, post_fn=fake_post)
        bad_approval = render_analysis({"analysis_result_ref": analysis_ref, "_server_context": context}, post_fn=fake_post)
        replay = render_analysis({"analysis_result_ref": analysis_ref, "approval_ref": approval_ref, "_server_context": context}, post_fn=fake_post)
        auth_prepared = prepare_dashboard_write({"analysis_result_ref": analysis_ref, "_server_context": context})
        old_org, old_user = os.environ.pop("ANALYSIS_CONTEXT_ORG_ID", None), os.environ.pop("ANALYSIS_CONTEXT_USER_ID", None)
        try:
            bad_auth = render_analysis({"analysis_result_ref": analysis_ref, "approval_ref": auth_prepared.get("approval_ref"), "_server_context": other_context}, post_fn=fake_post)
        finally:
            if old_org is not None:
                os.environ["ANALYSIS_CONTEXT_ORG_ID"] = old_org
            if old_user is not None:
                os.environ["ANALYSIS_CONTEXT_USER_ID"] = old_user
        malformed_ref = ARTIFACTS.write_json(context, run_id, "analysis-malformed", {"analysis_type": "invalid"})
        bad_malformed = prepare_dashboard_write({"analysis_result_ref": malformed_ref, "_server_context": context})
        for name, result in {"raw_analysis": bad_raw, "missing_ref": bad_missing, "missing_approval": bad_approval, "replayed_approval": replay, "unauthorized": bad_auth, "malformed": bad_malformed}.items():
            if result.get("ok"):
                raise RuntimeError(f"negative check should fail: {name}")
        print(json.dumps({"ok": True, "dashboard_url": out["dashboard_url"], "panel_count": out["panel_count"], "correlation_heatmap_panel": "esnet-matrix-panel", "server_verified_one_time_approval": True, "final_answer_contains_causality_warning": True, "negative_checks": ["raw_analysis", "missing_ref", "missing_approval", "replayed_approval", "unauthorized", "malformed"]}, ensure_ascii=False, indent=2))


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
