#!/usr/bin/env python3
"""Prepare facts, compose evidence-bound report panels, and resolve opaque refs.

The host LLM chooses report flow/content from the complete bounded report; this
service only validates and renders that synthesis. It never analyzes data or
writes Grafana. Ask O11y's built-in Grafana MCP remains the only writer.
"""
from __future__ import annotations

import argparse
import importlib.util
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
ml_dashboard_contract = load_module("ml_dashboard_contract", ROOT / "ml_dashboard_contract.py")
ml_plotly_contract = load_module("ml_plotly_contract", ROOT / "ml_plotly_contract.py")
ml_report_contract = load_module("ml_report_contract", ROOT / "ml_report_contract.py")
ml_dashboard_compositor = load_module("ml_dashboard_compositor", ROOT / "ml_dashboard_compositor.py")
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
SERVER_INFO = {"name": "artifact-bridge-mcp", "version": "0.2.0"}
PROTOCOL = "2025-03-26"
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
MAX_RPC_BODY_BYTES = 512 * 1024
MAX_DASHBOARD_BYTES = 384 * 1024
MAX_PANELS = 24
MAX_TARGETS = 48
MAX_ASSET_BINDINGS = 24
MAX_PLOTLY_BINDINGS = 8
ARTIFACT_PUBLIC_BASE = os.environ.get("ARTIFACT_PUBLIC_BASE", "http://127.0.0.1:8777").rstrip("/")
QUERY_PLACEHOLDER_KEYS = {"$plan_ref", "fields", "refId", "datasource"}


def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-Actor-User-Id") or headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
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


def json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("dashboard JSON is invalid") from exc


def asset_expiry() -> int:
    try:
        return int(time.time()) + ARTIFACTS.retention_seconds
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("artifact retention is invalid") from exc


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


def selected_columns(columns: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    by_name = {str(item.get("selector")): item for item in columns if isinstance(item, dict) and item.get("selector")}
    unknown = sorted(set(fields) - set(by_name))
    if unknown:
        raise WorkflowContractError("dashboard target fields are not authorized by its source: " + ", ".join(unknown))
    return [by_name[field] for field in fields]


def resolve_target(context: dict[str, str], target: dict[str, Any]) -> dict[str, Any]:
    if "$execution_ref" in target:
        raise WorkflowContractError("analysis artifacts may only be attached through an image asset binding")
    if "$plan_ref" not in target:
        if target:
            raise WorkflowContractError("nonempty dashboard targets require an authorized opaque binding")
        return target
    unexpected = sorted(set(target) - QUERY_PLACEHOLDER_KEYS)
    if unexpected:
        raise WorkflowContractError("opaque dashboard target has unsupported keys: " + ", ".join(unexpected))
    plan_ref = target.get("$plan_ref")
    if not isinstance(plan_ref, str):
        raise WorkflowContractError("opaque query target requires $plan_ref")
    _, parts = parse_artifact_ref(plan_ref)
    if parts != ("query-plan",):
        raise WorkflowContractError("$plan_ref must reference query-plan")
    plan = validate_plan(ARTIFACTS.read_json(context, plan_ref))
    fields = target.get("fields")
    ref_id = target.get("refId", "A")
    if not isinstance(ref_id, str) or not ref_id or len(ref_id) > 8:
        raise WorkflowContractError("dashboard target refId is invalid")
    if not isinstance(fields, list) or not fields or len(fields) > 100 or not all(isinstance(item, str) and item for item in fields):
        raise WorkflowContractError("opaque query target requires bounded fields")
    query = json_clone(plan["grafana_query"])
    columns = query.get("columns")
    if isinstance(columns, list):
        query["columns"] = selected_columns(columns, fields)
    elif plan.get("query_language") == "mssql" and plan.get("dataset_id") == "wferp" and isinstance(query.get("rawSql"), str):
        unknown = sorted(set(fields) - set(plan["selected_fields"]))
        if unknown:
            raise WorkflowContractError("dashboard target fields are not authorized by its source: " + ", ".join(unknown))
    else:
        raise WorkflowContractError("query plan has no trusted output mapping")
    query["refId"] = ref_id
    return query


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


def replace_plotly_placeholder(value: Any, placeholder: str, figure: dict[str, Any]) -> tuple[Any, int]:
    """Replace exact-match placeholder strings with a sanitized figure object."""
    if value == placeholder:
        return json_clone(figure), 1
    if isinstance(value, str):
        if placeholder in value:
            raise WorkflowContractError("plotly placeholder must be the entire option value, not a substring")
        return value, 0
    if isinstance(value, list):
        count = 0
        output = []
        for item in value:
            replaced, found = replace_plotly_placeholder(item, placeholder, figure)
            output.append(replaced)
            count += found
        return output, count
    if isinstance(value, dict):
        count = 0
        output = {}
        for key, item in value.items():
            replaced, found = replace_plotly_placeholder(item, placeholder, figure)
            output[key] = replaced
            count += found
        return output, count
    return value, 0


def resolve_plotly_bindings(context: dict[str, str], panel: dict[str, Any], counters: dict[str, int]) -> dict[str, Any]:
    """Resolve askO11yPlotlyBindings into static, sanitized panel options."""
    bindings = panel.pop("askO11yPlotlyBindings", [])
    if not isinstance(bindings, list) or len(bindings) > MAX_PLOTLY_BINDINGS:
        raise WorkflowContractError(f"panel plotly bindings must be an array with at most {MAX_PLOTLY_BINDINGS} entries")
    counters["plotly"] += len(bindings)
    if counters["plotly"] > MAX_PLOTLY_BINDINGS:
        raise WorkflowContractError(f"dashboard has more than {MAX_PLOTLY_BINDINGS} plotly bindings")
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"placeholder", "$execution_ref", "output_index", "plugin_id"}:
            raise WorkflowContractError("plotly binding requires only placeholder, $execution_ref, output_index, and plugin_id")
        placeholder = binding["placeholder"]
        execution_ref = binding["$execution_ref"]
        output_index = binding["output_index"]
        plugin_id = binding["plugin_id"]
        if not isinstance(placeholder, str) or not placeholder.startswith("$plotly_") or not placeholder.removeprefix("$plotly_").replace("_", "").isalnum():
            raise WorkflowContractError("plotly placeholder must start with $plotly_ and contain only letters, digits, or underscores")
        if plugin_id != ml_plotly_contract.PLOTLY_PLUGIN_ID:
            raise WorkflowContractError(f"plotly binding plugin id is not the approved {ml_plotly_contract.PLOTLY_PLUGIN_ID}")
        if not isinstance(execution_ref, str) or isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
            raise WorkflowContractError("plotly binding requires an opaque execution ref and non-negative output index")
        execution = ARTIFACTS.read_json(context, execution_ref)
        try:
            result = execution["results"][output_index]
            mime = result.get("mime") if isinstance(result, dict) else None
        except (KeyError, IndexError, TypeError) as exc:
            raise WorkflowContractError("plotly output index does not exist") from exc
        payload = None
        for mime_type in ("application/vnd.plotly.v1+json", "application/json"):
            if isinstance(mime, dict) and isinstance(mime.get(mime_type), str):
                payload = mime[mime_type]
                break
        if payload is None:
            raise WorkflowContractError("plotly binding requires an application/json or plotly output")
        try:
            figure = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WorkflowContractError("plotly output is not valid JSON") from exc
        sanitized = ml_plotly_contract.sanitize_figure(figure)
        panel, replacements = replace_plotly_placeholder(panel, placeholder, sanitized)
        if replacements == 0:
            raise WorkflowContractError("plotly placeholder is not used by the panel")
    if "$plotly_" in json.dumps(panel, ensure_ascii=False):
        raise WorkflowContractError("dashboard contains an unresolved plotly placeholder")
    serialized_options = json.dumps(panel.get("options") or {}, ensure_ascii=False).lower()
    for forbidden in ("script", "onclick", "callback", "new function", "eval("):
        if forbidden in serialized_options:
            raise WorkflowContractError(f"plotly panel options must not contain {forbidden!r}")
    if bindings:
        fallback_url = str((panel.get("options") or {}).get("fallbackUrl") or "")
        if not fallback_url.startswith("http"):
            raise WorkflowContractError("plotly panel requires a resolved PNG fallbackUrl (askO11yAssetBindings)")
    return panel


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
            expires_at=asset_expiry(),
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
        raw_item = json_clone(panel)
        nested_panels = raw_item.pop("panels", None)
        item = resolve_asset_bindings(context, raw_item, counters)
        item = resolve_plotly_bindings(context, item, counters)
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
        if nested_panels is not None:
            item["panels"] = resolve_panels(context, nested_panels, counters)
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
        output = json_clone(dashboard)
        tags = output.get("tags")
        if isinstance(tags, list) and (ml_dashboard_contract.REPORT_TAG in tags or any("ml" in str(tag).lower() for tag in tags)):
            ml_dashboard_contract.validate_ml_dashboard_minimum(output)
        counters = {"panels": 0, "targets": 0, "assets": 0, "plotly": 0}
        output["panels"] = resolve_panels(context, output.get("panels", []), counters)
        if counters["assets"] and counters["targets"]:
            raise WorkflowContractError("analysis dashboards may only contain image/text panels, not Grafana data targets")
    except (ArtifactAuthError, PermissionError) as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; the opaque dashboard binding is not authorized for this context.")
    except (WorkflowContractError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Revise only the dashboard binding placeholders; do not rerun successful query or analysis work.")
    return success_response(
        step=step,
        run_id="run_" + uuid.uuid4().hex,
        refs={},
        instruction="Internal host result: dispatch the resolved dashboard only to Ask O11y's built-in Grafana MCP; never expose it to the model.",
        evidence={"resolved_panels": counters["panels"], "resolved_targets": counters["targets"], "resolved_assets": counters["assets"], "resolved_plotly": counters["plotly"]},
        dashboard=output,
    )


def _plotly_capability(result: Any) -> dict[str, Any]:
    try:
        payload = result["mime"]["application/json"]
        figure = json.loads(payload)
        data = figure["data"]
        layout = figure.get("layout") or {}
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("Plotly output is invalid") from exc
    if not isinstance(data, list) or not isinstance(layout, dict):
        raise WorkflowContractError("Plotly output shape is invalid")
    x_axes = {key for key in layout if key == "xaxis" or (key.startswith("xaxis") and key.removeprefix("xaxis").isdigit())}
    subplot_count = max(len(x_axes), 1)
    has_matrix = any(isinstance(trace, dict) and trace.get("type") == "heatmap" for trace in data)
    full = subplot_count >= 3 or has_matrix or len(data) > 4
    min_height = 18 if subplot_count >= 7 else 16 if subplot_count >= 3 else 14 if has_matrix else 12
    return {"recommended_width": "full" if full else "half", "min_height": min_height}


def _report_material(args: dict[str, Any]) -> tuple[dict[str, str], str, dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    context = context_from_args(args)
    execution_ref = args.get("execution_ref")
    manifest_index = args.get("manifest_output_index")
    if not isinstance(execution_ref, str) or not execution_ref.startswith("artifact://"):
        raise WorkflowContractError("execution_ref is required")
    if isinstance(manifest_index, bool) or not isinstance(manifest_index, int) or manifest_index < 0:
        raise WorkflowContractError("manifest_output_index must be non-negative")
    execution = ARTIFACTS.read_json(context, execution_ref)
    try:
        results = execution["results"]
        manifest_result = results[manifest_index]
        manifest_payload = manifest_result["mime"]["application/json"]
        manifest = json.loads(manifest_payload)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("manifest output is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise WorkflowContractError("manifest output must be an object")
    by_name = {
        str(result.get("display_name")): index
        for index, result in enumerate(results)
        if isinstance(result, dict) and result.get("display_name")
    }
    artifacts: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, int]] = {}
    for item in manifest.get("artifacts") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.endswith(".png") or name not in by_name:
            raise WorkflowContractError("manifest artifact has no matching PNG output")
        artifact_id = name.removesuffix(".png")
        output = {"png_index": by_name[name]}
        plotly_name = f"ml-plotly-{artifact_id}.json"
        if plotly_name in by_name:
            output["plotly_index"] = by_name[plotly_name]
            output.update(_plotly_capability(results[output["plotly_index"]]))
        outputs[artifact_id] = output
        artifacts.append({
            "artifact_id": artifact_id,
            "png_output_index": output["png_index"],
            **({
                "plotly_output_index": output["plotly_index"],
                "recommended_width": output["recommended_width"],
                "min_height": output["min_height"],
            } if "plotly_index" in output else {}),
        })
    if not artifacts:
        raise WorkflowContractError("manifest contains no renderable artifacts")
    return context, execution_ref, manifest, artifacts, outputs


def prepare_ml_report(args: dict[str, Any]) -> dict[str, Any]:
    step = "prepare_ml_report"
    try:
        unexpected = sorted(set(args) - {"execution_ref", "manifest_output_index", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        _context, execution_ref, manifest, artifacts, _outputs = _report_material(args)
        facts = ml_report_contract.build_fact_catalog(manifest)
    except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; keep successful analysis outputs and correct only the opaque report reference.")
    return success_response(
        step=step, run_id="run_" + uuid.uuid4().hex, refs={"execution_ref": execution_ref},
        instruction="Inspect the entire bounded report and every artifact once, then author one flow/content synthesis without inventing numbers. Pass it to compose_ml_dashboard.",
        evidence={"artifact_count": len(artifacts), "fact_count": len(facts)},
        report_context={
            "purpose": manifest.get("purpose"), "conclusion": manifest.get("conclusion"),
            "artifacts": artifacts, "facts": facts,
            "restrictions": {"numeric_text": "Use evidence fact_ref + format; narrative fields contain no digits.", "flow": "Choose sections, order, titles, charts, collapsed state, and narratives from this report; no fixed template."},
        },
    )


def compose_ml_dashboard(args: dict[str, Any]) -> dict[str, Any]:
    step = "compose_ml_dashboard"
    try:
        unexpected = sorted(set(args) - {"execution_ref", "manifest_output_index", "synthesis", "uid", "title", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        _context, execution_ref, manifest, _artifacts, outputs = _report_material(args)
        synthesis = args.get("synthesis")
        if not isinstance(synthesis, dict):
            raise WorkflowContractError("synthesis is required")
        dashboard = ml_dashboard_compositor.compose_dashboard(
            manifest, synthesis, execution_ref=execution_ref, outputs=outputs,
            uid=str(args.get("uid") or ""), title=str(args.get("title") or ""),
        )
        ml_dashboard_contract.validate_preview_dashboard(dashboard)
    except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Revise only the LLM report synthesis; do not rerun query or analysis.")
    return success_response(
        step=step, run_id="run_" + uuid.uuid4().hex, refs={},
        instruction="The evidence-bound opaque dashboard is ready. Resolve bindings, then dispatch only to the approved Grafana writer.",
        evidence={"sections": len(synthesis["sections"]), "artifacts": sum(len(section["panels"]) for section in synthesis["sections"])},
        dashboard=dashboard,
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
}, {
    "name": "prepare_ml_report",
    "description": "Return a bounded artifact and deterministic fact catalog for one complete report so the host LLM can synthesize flow and per-chart narratives without raw rows or signed URLs.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {"execution_ref": {"type": "string"}, "manifest_output_index": {"type": "integer", "minimum": 0}},
        "required": ["execution_ref", "manifest_output_index"],
    },
}, {
    "name": "compose_ml_dashboard",
    "description": "Validate one whole-report LLM synthesis against deterministic facts/artifacts and compose an opaque Grafana dashboard without choosing or hardcoding its flow or content.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "execution_ref": {"type": "string"}, "manifest_output_index": {"type": "integer", "minimum": 0},
            "synthesis": {"type": "object"}, "uid": {"type": "string"}, "title": {"type": "string"},
        },
        "required": ["execution_ref", "manifest_output_index", "synthesis", "uid", "title"],
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
        handlers = {
            "resolve_dashboard_refs": (resolve_dashboard_refs, {"dashboard", "_server_context"}),
            "prepare_ml_report": (prepare_ml_report, {"execution_ref", "manifest_output_index", "_server_context"}),
            "compose_ml_dashboard": (compose_ml_dashboard, {"execution_ref", "manifest_output_index", "synthesis", "uid", "title", "_server_context"}),
        }
        if name not in handlers:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        handler, allowed = handlers[name]
        unexpected = sorted(set(arguments) - allowed)
        output = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Pass only declared tool arguments.") if unexpected else handler(arguments)
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
            {"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "plot.png"},
        ], "error": None})
        query_dashboard = {"title": "Query", "panels": [
            {"type": "xychart", "targets": [{"$plan_ref": plan_ref, "fields": ["x", "y"], "refId": "A"}]},
        ]}
        image_dashboard = {"title": "Analysis", "panels": [
            {"type": "text", "options": {"mode": "html", "content": "<img src=\"$asset_url_plot\">"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_plot", "$execution_ref": execution_ref, "output_index": 0}]},
        ]}
        result = resolve_dashboard_refs({"dashboard": query_dashboard, "_server_context": context})
        image_result = resolve_dashboard_refs({"dashboard": image_dashboard, "_server_context": context})
        nested_image_result = resolve_dashboard_refs({"dashboard": {"panels": [{"type": "row", "collapsed": True, "panels": image_dashboard["panels"]}]}, "_server_context": context})
        analysis_target = resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$execution_ref": execution_ref}]}]}, "_server_context": context})
        mixed_dashboard = resolve_dashboard_refs({"dashboard": {"panels": [*query_dashboard["panels"], *image_dashboard["panels"]]}, "_server_context": context})
        bad = resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$plan_ref": plan_ref, "fields": ["missing"]}]}]}, "_server_context": context})
        raw_target = resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"datasource": {"uid": "raw"}, "expr": "up"}]}]}, "_server_context": context})
        nested = {"targets": []}
        for _ in range(MAX_PANELS):
            nested = {"targets": [], "panels": [nested]}
        excessive_panels = resolve_dashboard_refs({"dashboard": {"panels": [nested]}, "_server_context": context})
        checks = {
            "panel_json_untouched": result.get("dashboard", {}).get("panels", [{}])[0].get("type") == "xychart",
            "plan_ref_resolved_server_side": result.get("dashboard", {}).get("panels", [{}])[0].get("targets", [{}])[0].get("url") == "http://data.example/input.csv",
            "opaque_refs_removed_before_grafana": "$plan_ref" not in json.dumps(result.get("dashboard", {})),
            "asset_url_resolved_without_panel_generation": image_result.get("ok") and "/assets/" in image_result.get("dashboard", {}).get("panels", [{}])[0].get("options", {}).get("content", "") and "askO11yAssetBindings" not in image_result.get("dashboard", {}).get("panels", [{}])[0],
            "nested_asset_url_resolved": nested_image_result.get("ok") and "/assets/" in json.dumps(nested_image_result.get("dashboard", {})) and "askO11yAssetBindings" not in json.dumps(nested_image_result.get("dashboard", {})),
            "analysis_target_rejected": not analysis_target.get("ok"),
            "mixed_analysis_and_native_targets_rejected": not mixed_dashboard.get("ok"),
            "unknown_field_rejected": not bad.get("ok"),
            "raw_target_rejected": not raw_target.get("ok"),
            "nested_panel_limit_enforced": not excessive_panels.get("ok"),
        }
        if not result.get("ok") or not all(checks.values()):
            raise SystemExit(json.dumps({"ok": False, "checks": checks, "result": result, "image_result": image_result, "bad": bad}, indent=2))
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
