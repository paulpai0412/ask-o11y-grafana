#!/usr/bin/env python3
"""Prepare facts, compose evidence-bound report panels, and resolve opaque refs.

The host LLM chooses report flow/content from the complete bounded report; this
service only validates and renders that synthesis. It never analyzes data or
writes Grafana. Ask O11y's built-in Grafana MCP remains the only writer.
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import importlib.util
import json
import math
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
ml_plotly_contract = load_module("ml_plotly_contract", ROOT / "ml_plotly_contract.py")
ml_dashboard_contract = load_module("ml_dashboard_contract", ROOT / "ml_dashboard_contract.py")
ml_report_contract = load_module("ml_report_contract", ROOT / "ml_report_contract.py")
ontology_contract = load_module("artifact_bridge_ontology_contract", ROOT / "ontology_contract.py")
ml_figure_inspection = load_module("ml_figure_inspection", ROOT / "ml_figure_inspection.py")
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
SERVER_INFO = {"name": "artifact-bridge-mcp", "version": "0.3.0"}
PROTOCOL = "2025-03-26"
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()
MAX_RPC_BODY_BYTES = 512 * 1024
MAX_DASHBOARD_BYTES = 384 * 1024
MAX_PANELS = 24
MAX_TARGETS = 48
MAX_ASSET_BINDINGS = 24
ARTIFACT_PUBLIC_BASE = os.environ.get("ARTIFACT_PUBLIC_BASE", "http://127.0.0.1:8777").rstrip("/")
QUERY_PLACEHOLDER_KEYS = {"$plan_ref", "fields", "refId", "datasource"}


def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-Actor-User-Id") or headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    return {"org_id": str(org), "user_id": str(user), "session_id": str(headers.get("X-Grafana-Session-Id") or "")} if org and user else None


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
    args.pop("_server_context", None)
    if context:
        args["_server_context"] = context
    return msg


def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    context = args.get("_server_context")
    if not isinstance(context, dict) or not context.get("org_id") or not context.get("user_id"):
        raise PermissionError("trusted execution context is required")
    return {"org_id": str(context["org_id"]), "user_id": str(context["user_id"]), "session_id": str(context.get("session_id") or "")}


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


def _read_report_binding(context: dict[str, str], report_manifest_ref: Any, artifact_id: Any, expected_mode: str) -> tuple[str, int, dict[str, Any]]:
    if not isinstance(report_manifest_ref, str) or not report_manifest_ref.startswith("artifact://"):
        raise WorkflowContractError("report manifest ref is invalid")
    manifest_run_id, parts = parse_artifact_ref(report_manifest_ref)
    if parts != ("report-manifest",):
        raise WorkflowContractError("report binding requires a canonical report manifest ref")
    manifest = ARTIFACTS.read_json(context, report_manifest_ref)
    if not isinstance(manifest, dict) or manifest.get("format") not in ml_report_contract.REPORT_MANIFEST_FORMATS:
        raise WorkflowContractError("report manifest format is invalid")
    execution_ref = manifest.get("execution_ref")
    if not isinstance(execution_ref, str) or not execution_ref.startswith("artifact://"):
        raise WorkflowContractError("report manifest execution ref is invalid")
    execution_run_id, execution_parts = parse_artifact_ref(execution_ref)
    if execution_run_id != manifest_run_id or execution_parts != ("sandbox-execution",):
        raise WorkflowContractError("report manifest execution ref is not paired with its manifest")
    execution = ARTIFACTS.read_json(context, execution_ref)
    if not isinstance(execution, dict) or execution.get("error"):
        raise WorkflowContractError("report manifest execution is unavailable")
    if not isinstance(artifact_id, str):
        raise WorkflowContractError("report artifact id is required")
    item = next((item for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("artifact_id") == artifact_id), None)
    if item is None:
        raise WorkflowContractError("report artifact id is unknown")
    render = item.get("render")
    if not isinstance(render, dict):
        raise WorkflowContractError("report artifact render is invalid")
    render_mode = render.get("mode")
    if expected_mode == "plotly" and render_mode == "error":
        # Recompute the whole source binding before exposing a host-only error marker.
        _canonical_report_material(context, report_manifest_ref)
        return execution_ref, render["output_index"], {"data": [], "layout": {}, "error": render["error_code"]}
    if expected_mode == "plotly" and render_mode != "plotly":
        raise WorkflowContractError("report artifact render mode must be plotly")
    if expected_mode == "image":
        if render_mode == "image":
            output_index = render.get("output_index")
            expected_digest = render.get("sha256")
        elif render_mode == "plotly":
            output_index = render.get("png_output_index")
            expected_digest = render.get("png_sha256")
            if output_index is None:
                raise WorkflowContractError("report artifact has no PNG fallback")
        else:
            raise WorkflowContractError("report artifact render mode must be image or plotly with a PNG fallback")
    else:
        output_index = render.get("output_index")
        expected_digest = render.get("sha256")
    if isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
        raise WorkflowContractError("report artifact output index is invalid")
    try:
        result = execution["results"][output_index]
    except (KeyError, IndexError, TypeError) as exc:
        raise WorkflowContractError("report artifact output index does not exist") from exc
    if not isinstance(result, dict) or not isinstance(result.get("mime"), dict):
        raise WorkflowContractError("report artifact output is invalid")
    mime_type = "image/png" if expected_mode == "image" else render.get("mime_type")
    payload = result["mime"].get(mime_type)
    if not isinstance(payload, str):
        raise WorkflowContractError("report artifact MIME output is unavailable")
    if expected_mode == "plotly":
        figure = _plotly_figure(result, legacy=manifest["format"] == ml_report_contract.LEGACY_REPORT_MANIFEST_FORMAT)
        digest = hashlib.sha256(json.dumps(figure, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if expected_digest != digest:
            raise WorkflowContractError("report Plotly digest does not match the manifest")
        return execution_ref, output_index, figure
    if mime_type != "image/png":
        raise WorkflowContractError("report image render must use image/png")
    try:
        encoded = ml_report_contract._validate_png(payload)
    except (ValueError, binascii.Error) as exc:
        raise WorkflowContractError("report PNG output is invalid") from exc
    if expected_digest != hashlib.sha256(encoded).hexdigest():
        raise WorkflowContractError("report PNG digest does not match the manifest")
    return execution_ref, output_index, {"payload": payload}


def resolve_plotly_bindings(context: dict[str, str], panel: dict[str, Any], counters: dict[str, int]) -> dict[str, Any]:
    """Resolve askO11yPlotlyBindings into static, sanitized panel options."""
    bindings = panel.pop("askO11yPlotlyBindings", [])
    if not isinstance(bindings, list):
        raise WorkflowContractError("panel plotly bindings must be an array")
    # Overall dashboard bytes/panels already bound this transport; no second chart quota.
    counters["plotly"] += len(bindings)
    for binding in bindings:
        if not isinstance(binding, dict):
            raise WorkflowContractError("plotly binding is invalid")
        if set(binding) == {"placeholder", "$report_manifest_ref", "artifact_id", "plugin_id"}:
            placeholder = binding["placeholder"]
            plugin_id = binding["plugin_id"]
            _execution_ref, _output_index, sanitized = _read_report_binding(context, binding["$report_manifest_ref"], binding["artifact_id"], "plotly")
        elif set(binding) == {"placeholder", "$execution_ref", "output_index", "plugin_id"}:
            placeholder = binding["placeholder"]
            execution_ref = binding["$execution_ref"]
            output_index = binding["output_index"]
            plugin_id = binding["plugin_id"]
            if not isinstance(execution_ref, str) or isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
                raise WorkflowContractError("plotly binding requires an opaque execution ref and non-negative output index")
            execution = ARTIFACTS.read_json(context, execution_ref)
            try:
                result = execution["results"][output_index]
            except (KeyError, IndexError, TypeError) as exc:
                raise WorkflowContractError("plotly output index does not exist") from exc
            # Legacy execution bindings have no v2 figure-format marker.
            sanitized = _plotly_figure(result, legacy=True)
        else:
            raise WorkflowContractError("plotly binding requires only placeholder, report manifest ref, artifact id, and plugin id")
        if not isinstance(placeholder, str) or not placeholder.startswith("$plotly_") or not placeholder.removeprefix("$plotly_").replace("_", "").replace("-", "").isalnum():
            raise WorkflowContractError("plotly placeholder must start with $plotly_ and contain only letters, digits, underscores, or hyphens")
        if plugin_id != ml_plotly_contract.PLOTLY_PLUGIN_ID:
            raise WorkflowContractError(f"plotly binding plugin id is not the approved {ml_plotly_contract.PLOTLY_PLUGIN_ID}")
        panel, replacements = replace_plotly_placeholder(panel, placeholder, sanitized)
        if replacements == 0:
            raise WorkflowContractError("plotly placeholder is not used by the panel")
    if "$plotly_" in json.dumps(panel, ensure_ascii=False):
        raise WorkflowContractError("dashboard contains an unresolved plotly placeholder")
    # Only actual executable option slots are forbidden; words inside figure labels
    # (for example 'transcript') do not create an execution capability.
    if {"script", "onclick", "callback"} & (panel.get("options") or {}).keys():
        raise WorkflowContractError("plotly panel options must not contain executable handlers")
    if bindings and (panel.get("options") or {}).get("renderMode") != "plotly":
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
        if not isinstance(binding, dict):
            raise WorkflowContractError("asset binding is invalid")
        if set(binding) == {"placeholder", "$report_manifest_ref", "artifact_id"}:
            placeholder = binding["placeholder"]
            execution_ref, output_index, _image = _read_report_binding(context, binding["$report_manifest_ref"], binding["artifact_id"], "image")
        elif set(binding) == {"placeholder", "$execution_ref", "output_index"}:
            placeholder = binding["placeholder"]
            execution_ref = binding["$execution_ref"]
            output_index = binding["output_index"]
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
        else:
            raise WorkflowContractError("asset binding requires only placeholder, report manifest ref, and artifact id")
        if not isinstance(placeholder, str) or not placeholder.startswith("$asset_url_") or not placeholder.removeprefix("$asset_url_").replace("_", "").replace("-", "").isalnum():
            raise WorkflowContractError("asset placeholder must start with $asset_url_ and contain only letters, digits, or underscores")
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
        asset_bindings = panel.get("askO11yAssetBindings")
        if asset_bindings and panel.get("type") != ml_dashboard_contract.PLOTLY_PLUGIN_ID:
            raise WorkflowContractError("all analysis images must use asko11y-plotly-panel")
        if asset_bindings and not panel.get("askO11yPlotlyBindings"):
            options = panel.get("options")
            placeholders = {binding.get("placeholder") for binding in asset_bindings if isinstance(binding, dict)}
            if not isinstance(options, dict) or options.get("renderMode") != "image" or options.get("fallbackUrl") not in placeholders:
                raise WorkflowContractError("image panels require options.renderMode='image' and options.fallbackUrl set to an asset placeholder")
        if panel.get("type") == "text":
            ml_dashboard_contract.validate_narrative_content((panel.get("options") or {}).get("content", ""))
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


def resolve_dashboard_payload(context: dict[str, str], dashboard: dict[str, Any]) -> dict[str, Any]:
    if "$dashboard_ref" not in dashboard:
        return dashboard
    if set(dashboard) != {"$dashboard_ref"}:
        raise WorkflowContractError("dashboard ref must be the only dashboard field")
    dashboard_ref = dashboard["$dashboard_ref"]
    if not isinstance(dashboard_ref, str) or not dashboard_ref.startswith("artifact://"):
        raise WorkflowContractError("dashboard ref is invalid")
    _, parts = parse_artifact_ref(dashboard_ref)
    if parts != ("dashboard",):
        raise WorkflowContractError("dashboard ref must reference a composed dashboard")
    resolved = ARTIFACTS.read_json(context, dashboard_ref)
    if not isinstance(resolved, dict):
        raise WorkflowContractError("composed dashboard is invalid")
    return resolved


def resolve_dashboard_refs(args: dict[str, Any]) -> dict[str, Any]:
    step = "resolve_dashboard_refs"
    try:
        context = context_from_args(args)
        dashboard = args.get("dashboard")
        if not isinstance(dashboard, dict):
            raise WorkflowContractError("dashboard is required")
        if "$dashboard_ref" not in dashboard:
            tags = dashboard.get("tags")
            serialized_dashboard = json.dumps(dashboard, ensure_ascii=False)
            has_report_binding = "$report_manifest_ref" in serialized_dashboard
            if has_report_binding or (isinstance(tags, list) and (ml_dashboard_contract.REPORT_TAG in tags or any("ml" in str(tag).lower() for tag in tags))):
                raise WorkflowContractError("report dashboards require an opaque composed dashboard ref; manual binding is not accepted")
        dashboard = resolve_dashboard_payload(context, dashboard)
        if len(json.dumps(dashboard, ensure_ascii=False).encode()) > MAX_DASHBOARD_BYTES:
            raise WorkflowContractError("dashboard exceeds resolver size limit")
        output = json_clone(dashboard)
        tags = output.get("tags")
        if isinstance(tags, list) and (ml_dashboard_contract.REPORT_TAG in tags or any("ml" in str(tag).lower() for tag in tags)):
            ml_dashboard_contract.validate_ml_dashboard_minimum(output)
        counters = {"panels": 0, "targets": 0, "assets": 0, "plotly": 0}
        output["panels"] = resolve_panels(context, output.get("panels", []), counters)
        if counters["assets"] and counters["targets"]:
            raise WorkflowContractError("analysis dashboards may only contain plugin evidence and narrative panels, not Grafana data targets")
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


def _plotly_figure(result: Any, *, legacy: bool = False) -> dict[str, Any]:
    try:
        mime = result["mime"]
        payload = mime.get("application/vnd.plotly.v1+json") or mime.get("application/json")
        figure = json.loads(payload)
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("Plotly output is invalid") from exc
    try:
        return ml_plotly_contract.sanitize_figure(figure, legacy=legacy)
    except ValueError as exc:
        raise WorkflowContractError(str(exc)) from exc


def _plotly_capability(spec: dict[str, Any]) -> dict[str, Any]:
    subplot_count = len(spec["views"])
    has_matrix = any("heatmap" in view.get("trace_types", []) for view in spec["views"])
    full = subplot_count >= 3 or has_matrix or spec["trace_count"] > 4
    rows = (subplot_count + 1) // 2
    min_height = min(40, 8 + rows * 10) if subplot_count > 1 else 16 if has_matrix else 14
    return {"recommended_width": "full" if full else "half", "min_height": min_height}


def _canonical_report_material(context: dict[str, str], report_manifest_ref: str) -> tuple[dict[str, str], str, dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(report_manifest_ref, str) or not report_manifest_ref.startswith("artifact://"):
        raise WorkflowContractError("report manifest ref is invalid")
    try:
        manifest_run_id, parts = parse_artifact_ref(report_manifest_ref)
    except ValueError as exc:
        raise WorkflowContractError("report manifest ref is missing or invalid") from exc
    if parts != ("report-manifest",):
        raise WorkflowContractError("report manifest ref must reference a canonical report manifest")
    manifest = ARTIFACTS.read_json(context, report_manifest_ref)
    if not isinstance(manifest, dict) or manifest.get("format") not in ml_report_contract.REPORT_MANIFEST_FORMATS:
        raise WorkflowContractError("report manifest format is invalid")
    execution_ref = manifest.get("execution_ref")
    if not isinstance(execution_ref, str) or not execution_ref.startswith("artifact://"):
        raise WorkflowContractError("report manifest execution ref is invalid")
    execution_run_id, execution_parts = parse_artifact_ref(execution_ref)
    if execution_run_id != manifest_run_id or execution_parts != ("sandbox-execution",):
        raise WorkflowContractError("report manifest execution ref is not paired with its manifest")
    execution = ARTIFACTS.read_json(context, execution_ref)
    if not isinstance(execution, dict) or "error" not in execution or execution.get("error") is not None or not isinstance(execution.get("results"), list):
        raise WorkflowContractError("report manifest execution is unavailable")
    results = execution["results"]
    legacy = manifest["format"] == ml_report_contract.LEGACY_REPORT_MANIFEST_FORMAT
    artifacts: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in manifest.get("artifacts") or []:
        if not isinstance(item, dict) or not isinstance(item.get("artifact_id"), str):
            raise WorkflowContractError("report manifest artifact is invalid")
        artifact_id = item["artifact_id"]
        if artifact_id in seen:
            raise WorkflowContractError("report manifest artifact ids must be unique")
        seen.add(artifact_id)
        render = item.get("render")
        if not isinstance(render, dict) or render.get("mode") not in ({"plotly", "image"} if legacy else {"plotly", "image", "error"}):
            raise WorkflowContractError("report manifest render is invalid")
        output_index = render.get("output_index")
        if isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0 or output_index >= len(results):
            raise WorkflowContractError("report manifest output index is invalid")
        result = results[output_index]
        if render["mode"] == "error":
            payload = result.get("mime", {}).get(render.get("mime_type")) if isinstance(result, dict) else None
            if not isinstance(payload, str) or hashlib.sha256(payload.encode()).hexdigest() != render.get("sha256"):
                raise WorkflowContractError("failed figure digest is invalid")
            figure_spec = {"artifact_id": artifact_id, "kind": "error", "views": [{"view_id": "figure", "title": artifact_id, "kind": "error"}]}
            output = {"plotly_index": output_index, "presentation_error": render.get("error_code"), "figure_spec": figure_spec}
        elif render["mode"] == "plotly":
            figure = _plotly_figure(result, legacy=legacy)
            digest = hashlib.sha256(json.dumps(figure, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            if render.get("mime_type") not in {"application/vnd.plotly.v1+json", "application/json"} or render.get("sha256") != digest:
                raise WorkflowContractError("report Plotly manifest digest is invalid")
            output = {"plotly_index": output_index, "figure": figure}
            png_index = render.get("png_output_index")
            if png_index is not None:
                if isinstance(png_index, bool) or not isinstance(png_index, int) or png_index < 0 or png_index >= len(results):
                    raise WorkflowContractError("report PNG fallback index is invalid")
                png_result = results[png_index]
                png_mime = png_result.get("mime") if isinstance(png_result, dict) else None
                png_payload = png_mime.get("image/png") if isinstance(png_mime, dict) else None
                if not isinstance(png_payload, str):
                    raise WorkflowContractError("report PNG fallback output is invalid")
                try:
                    encoded_png = ml_report_contract._validate_png(png_payload)
                except (ValueError, binascii.Error) as exc:
                    raise WorkflowContractError("report PNG fallback output is invalid") from exc
                if render.get("png_sha256") != hashlib.sha256(encoded_png).hexdigest():
                    raise WorkflowContractError("report PNG fallback digest is invalid")
                output["png_index"] = png_index
            figure_spec = ml_figure_inspection.inspect_figure(artifact_id, figure, whole=not legacy)
            output.update({"figure_spec": figure_spec, "recommended_width": figure_spec.get("recommended_width"), "min_height": figure_spec.get("min_height")})
        else:
            if render.get("mime_type") != "image/png" or not isinstance(result, dict) or not isinstance(result.get("mime"), dict) or not isinstance(result["mime"].get("image/png"), str):
                raise WorkflowContractError("report PNG manifest output is invalid")
            try:
                encoded = ml_report_contract._validate_png(result["mime"]["image/png"])
            except (ValueError, binascii.Error) as exc:
                raise WorkflowContractError("report PNG manifest output is invalid") from exc
            if render.get("sha256") != hashlib.sha256(encoded).hexdigest():
                raise WorkflowContractError("report PNG manifest digest is invalid")
            figure_spec = ml_figure_inspection.inspect_png_only(artifact_id, str(item.get("caption") or artifact_id))
            output = {"png_index": output_index, "figure_spec": figure_spec}
        if not legacy:
            output["figure_format"] = ml_plotly_contract.FIGURE_FORMAT
        outputs[artifact_id] = output
        public_artifact = {"artifact_id": artifact_id, "figure_spec": figure_spec}
        if "presentation_error" in output:
            public_artifact["presentation_error"] = output["presentation_error"]
        if "recommended_width" in output:
            public_artifact.update({"recommended_width": output["recommended_width"], "min_height": output["min_height"]})
        artifacts.append(public_artifact)
    if not artifacts:
        raise WorkflowContractError("report manifest contains no renderable artifacts")
    try:
        canonical = ml_report_contract.normalize_report_manifest(execution_ref=execution_ref, results=results, manifest_format=manifest["format"])
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise WorkflowContractError("report manifest cannot be recomputed from retained outputs") from exc
    if canonical != manifest:
        raise WorkflowContractError("report manifest does not match retained outputs")
    return context, execution_ref, manifest, artifacts, outputs


def _report_material(args: dict[str, Any]) -> tuple[dict[str, str], str, dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    context = context_from_args(args)
    report_manifest_ref = args.get("report_manifest_ref")
    if not isinstance(report_manifest_ref, str):
        raise WorkflowContractError("report_manifest_ref is required; re-export trusted pre-manifest results before preparing a report")
    return _canonical_report_material(context, report_manifest_ref)


def grant_artifact_reuse(args: dict[str, Any]) -> dict[str, Any]:
    step = "grant_artifact_reuse"
    try:
        context = context_from_args(args)
        execution_ref, target_session = args.get("execution_ref"), args.get("target_session_id")
        if set(args) - {"execution_ref", "target_session_id", "_server_context"} or not isinstance(execution_ref, str) or not isinstance(target_session, str):
            raise WorkflowContractError("pass only an execution ref and the user-specified target session")
        grant = ARTIFACTS.grant_reuse(context, execution_ref, target_session)
        run_id, _ = parse_artifact_ref(execution_ref)
        return success_response(step=step, run_id=run_id, refs={"execution_ref": execution_ref, "grant_ref": grant["grant_ref"]}, instruction="The same org/user may reuse existing results in the explicitly approved target session. Upload ownership and new execution authority did not change.", evidence=grant)
    except (PermissionError, OSError, ValueError, TypeError, WorkflowContractError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Reuse must be granted by the original owner session after explicit approval.")


_REEXPORT_SOURCE_FORMATS = frozenset({
    "ask-o11y-report-source-v1", "ask-o11y-ml-presentation-v1", "ask-o11y-ml-regression-v1", "ask-o11y-data-profile-v1",
})


def _result_json(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict) or not isinstance(result.get("mime"), dict):
        return None
    payload = result["mime"].get("application/json")
    if not isinstance(payload, str):
        return None
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _is_reexport_source_result(result: Any) -> bool:
    value = _result_json(result)
    return isinstance(result, dict) and (result.get("display_name") == "report-source.json" or (value or {}).get("format") in _REEXPORT_SOURCE_FORMATS)


def _result_digest(results: list[Any], *, include_source: bool) -> str:
    selected = [result for result in results if include_source or not _is_reexport_source_result(result)]
    return hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _verify_reexport_lineage(context: dict[str, str], execution_ref: str, provenance: dict[str, Any]) -> None:
    """Verify a single authenticated source packet and bounded output re-export."""
    fresh_run_id, fresh_parts = parse_artifact_ref(execution_ref)
    if fresh_parts != ("sandbox-execution",):
        raise WorkflowContractError("re-export execution ref is invalid")
    source_execution_ref = provenance.get("reexport_of")
    source_provenance_ref = provenance.get("source_provenance_ref")
    if not isinstance(source_execution_ref, str) or not isinstance(source_provenance_ref, str):
        raise WorkflowContractError("re-export lineage is incomplete")
    source_run_id, source_parts = parse_artifact_ref(source_execution_ref)
    provenance_run_id, provenance_parts = parse_artifact_ref(source_provenance_ref)
    if source_parts != ("sandbox-execution",) or provenance_parts != ("sandbox-provenance",) or source_run_id != provenance_run_id or source_run_id == fresh_run_id:
        raise WorkflowContractError("re-export source refs are not paired")
    fresh_execution = ARTIFACTS.read_json(context, execution_ref)
    source_execution = ARTIFACTS.read_json(context, source_execution_ref)
    source_provenance = ARTIFACTS.read_json(context, source_provenance_ref)
    if not isinstance(fresh_execution, dict) or "error" not in fresh_execution or fresh_execution.get("error") is not None or not isinstance(fresh_execution.get("results"), list):
        raise WorkflowContractError("re-export execution is not a concrete success")
    if fresh_execution.get("reexport_of") != source_execution_ref or fresh_execution.get("source_provenance_ref") != source_provenance_ref:
        raise WorkflowContractError("re-export execution and provenance refs do not pair")
    if not isinstance(source_execution, dict) or "error" not in source_execution or source_execution.get("error") is not None or not isinstance(source_execution.get("results"), list):
        raise WorkflowContractError("re-export source execution is not a concrete success")
    if not isinstance(source_provenance, dict) or source_provenance.get("report_manifest_ref"):
        raise WorkflowContractError("re-export source provenance is not pre-manifest")
    if "reexport_of" in source_execution or "source_provenance_ref" in source_execution or "reexport_of" in source_provenance or "source_provenance_ref" in source_provenance:
        raise WorkflowContractError("re-export chains are unsupported")
    if source_provenance.get("computation_status") != "succeeded" or source_provenance.get("report_status") not in {"rejected", "not_attempted"}:
        raise WorkflowContractError("re-export source status is not verified")
    if provenance.get("computation_status") != "succeeded" or provenance.get("report_status") != "accepted":
        raise WorkflowContractError("re-export status is not verified")
    for identity_key in ("code_sha256", "input_frame_sha256", "plan_sha256", "business_question", "analysis_contract", "executor_kind", "trusted_ml_contract"):
        if provenance.get(identity_key) != source_provenance.get(identity_key):
            raise WorkflowContractError("re-export source identity changed")
    if provenance.get("reexport_source_results_sha256") != _result_digest(source_execution["results"], include_source=False) or provenance.get("reexport_source_results_sha256") != _result_digest(fresh_execution["results"], include_source=False):
        raise WorkflowContractError("re-export changed retained outputs")
    source_candidates = [value for value in (_result_json(item) for item in source_execution["results"]) if value and value.get("format") in _REEXPORT_SOURCE_FORMATS]
    fresh_candidates = [value for value in (_result_json(item) for item in fresh_execution["results"]) if value and value.get("format") == ml_report_contract.REPORT_SOURCE_FORMAT]
    if len(source_candidates) != 1 or len(fresh_candidates) != 1:
        raise WorkflowContractError("re-export report source is not uniquely bounded")
    source_manifest_digest = hashlib.sha256(json.dumps(source_candidates[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    fresh_source_digest = hashlib.sha256(json.dumps(fresh_candidates[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if provenance.get("reexport_source_manifest_sha256") != source_manifest_digest or provenance.get("reexport_report_source_sha256") != fresh_source_digest or provenance.get("reexport_source_format") != source_candidates[0].get("format"):
        raise WorkflowContractError("re-export report source identity is invalid")
    _verify_analysis_lineage(context, source_execution_ref, source_provenance)


def _verify_generic_repair_lineage(context: dict[str, str], execution_ref: str, provenance: dict[str, Any]) -> None:
    """Verify one same-session host repair without promoting generic evidence."""
    fresh_run_id, fresh_parts = parse_artifact_ref(execution_ref)
    source_execution_ref = provenance.get("generic_repair_of")
    source_provenance_ref = provenance.get("source_provenance_ref")
    if provenance.get("generic_repair_kind") != "host_report_manifest" or not isinstance(source_execution_ref, str) or not isinstance(source_provenance_ref, str):
        raise WorkflowContractError("generic repair lineage is incomplete")
    source_run_id, source_parts = parse_artifact_ref(source_execution_ref)
    provenance_run_id, provenance_parts = parse_artifact_ref(source_provenance_ref)
    if source_parts != ("sandbox-execution",) or provenance_parts != ("sandbox-provenance",) or source_run_id != provenance_run_id or source_run_id == fresh_run_id:
        raise WorkflowContractError("generic repair source refs are not paired")
    fresh_execution = ARTIFACTS.read_json(context, execution_ref)
    source_execution = ARTIFACTS.read_json(context, source_execution_ref)
    source_provenance = ARTIFACTS.read_json(context, source_provenance_ref)
    if not isinstance(fresh_execution, dict) or "error" not in fresh_execution or fresh_execution.get("error") is not None or not isinstance(fresh_execution.get("results"), list):
        raise WorkflowContractError("generic repair execution is not a concrete success")
    if fresh_execution.get("generic_repair_of") != source_execution_ref or fresh_execution.get("source_provenance_ref") != source_provenance_ref:
        raise WorkflowContractError("generic repair execution and provenance refs do not pair")
    if not isinstance(source_execution, dict) or "error" not in source_execution or source_execution.get("error") is not None or not isinstance(source_execution.get("results"), list):
        raise WorkflowContractError("generic repair source execution is unavailable")
    if not isinstance(source_provenance, dict) or source_provenance.get("report_manifest_ref") not in (None, ""):
        raise WorkflowContractError("generic repair source is not pre-manifest")
    if source_provenance.get("computation_status") != "succeeded" or source_provenance.get("report_status") != "rejected":
        raise WorkflowContractError("generic repair source status is not verified")
    # pi-lens-ignore: is-literal, ast-grep:no-identity-operator-on-literals
    if source_provenance.get("trusted_ml_contract") is not False or source_provenance.get("executor_kind") not in {"execute_python_analysis", "revise_python_analysis"}:
        raise WorkflowContractError("generic repair source is not untrusted generic Python")
    if provenance.get("computation_status") != "succeeded" or provenance.get("report_status") != "accepted":
        raise WorkflowContractError("generic repair fresh report status is not verified")
    source_audit = source_execution.get("input_audit")
    if not isinstance(source_audit, dict) or source_audit != source_provenance.get("validity"):
        raise WorkflowContractError("generic repair source validity evidence does not match the retained input audit")
    if any(key in source_execution or key in source_provenance for key in ("reexport_of", "source_provenance_ref", "generic_repair_of")):
        raise WorkflowContractError("generic repair chains are unsupported")
    for identity_key in ("code_sha256", "input_frame_sha256", "plan_sha256", "business_question", "analysis_contract", "seed", "presentation_mode", "validity", "executor_kind", "trusted_ml_contract"):
        if provenance.get(identity_key) != source_provenance.get(identity_key):
            raise WorkflowContractError("generic repair source identity changed")
    source_results_digest = source_provenance.get("captured_results_sha256")
    if not isinstance(source_results_digest, str) or source_results_digest != _result_digest(source_execution["results"], include_source=False):
        raise WorkflowContractError("generic repair source result digest is invalid")
    if provenance.get("generic_repair_source_results_sha256") != source_results_digest or provenance.get("generic_repair_source_provenance_sha256") != hashlib.sha256(json.dumps(source_provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest():
        raise WorkflowContractError("generic repair provenance digest is invalid")
    retained = [item for item in fresh_execution["results"] if not _is_reexport_source_result(item)]
    if _result_digest(retained, include_source=True) != source_results_digest:
        raise WorkflowContractError("generic repair changed retained outputs")
    source_sources = [item for item in source_execution["results"] if _is_reexport_source_result(item)]
    fresh_sources = [item for item in fresh_execution["results"] if _is_reexport_source_result(item)]
    if len(source_sources) != 1 or len(fresh_sources) != 1:
        raise WorkflowContractError("generic repair report source is not uniquely host-owned")
    source_source = _result_json(source_sources[0])
    source_source_digest = hashlib.sha256(json.dumps(source_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() if isinstance(source_source, dict) else None
    if source_source_digest != source_provenance.get("host_report_source_sha256"):
        raise WorkflowContractError("generic repair source report source is not host-verified")
    fresh_source = _result_json(fresh_sources[0])
    if not isinstance(fresh_source, dict) or fresh_source.get("format") != ml_report_contract.REPORT_SOURCE_FORMAT:
        raise WorkflowContractError("generic repair report source format is invalid")
    fresh_digest = hashlib.sha256(json.dumps(fresh_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if provenance.get("generic_repair_report_source_sha256") != fresh_digest:
        raise WorkflowContractError("generic repair report source digest is invalid")
    if fresh_digest != source_source_digest or fresh_source != source_source:
        raise WorkflowContractError("generic repair report source is not bound to the original host receipt")
    _verify_analysis_lineage(context, source_execution_ref, source_provenance)


def _verify_analysis_lineage(context: dict[str, str], execution_ref: str, provenance: dict[str, Any]) -> None:
    """Verify retained source identities against authorized artifacts."""
    if "reexport_of" in provenance or "source_provenance_ref" in provenance:
        if "reexport_of" not in provenance or "source_provenance_ref" not in provenance:
            raise WorkflowContractError("re-export lineage is incomplete")
        _verify_reexport_lineage(context, execution_ref, provenance)
        return
    execution_run_id, _ = parse_artifact_ref(execution_ref)
    code_ref = provenance.get("code_ref")
    if not isinstance(code_ref, str):
        raise WorkflowContractError("retained source code lineage is unavailable")
    code_run_id, code_parts = parse_artifact_ref(code_ref)
    if code_run_id != execution_run_id or code_parts != ("sandbox-code",):
        raise WorkflowContractError("retained source code lineage is not paired")
    code = ARTIFACTS.read_json(context, code_ref)
    if not isinstance(code, dict) or not isinstance(code.get("source"), str):
        raise WorkflowContractError("retained source code artifact is invalid")
    code_digest = hashlib.sha256(code["source"].encode("utf-8")).hexdigest()
    if code_digest != provenance.get("code_sha256") or code.get("sha256") != provenance.get("code_sha256"):
        raise WorkflowContractError("retained source code digest does not match")
    frame_ref = provenance.get("input_frame_ref")
    if not isinstance(frame_ref, str):
        raise WorkflowContractError("retained input frame lineage is unavailable")
    frame_run_id, frame_parts = parse_artifact_ref(frame_ref)
    if frame_parts != ("grafana-frame",):
        raise WorkflowContractError("retained input frame lineage is invalid")
    frame_payload = ARTIFACTS.read_json(context, frame_ref)
    if not isinstance(frame_payload, list) or len(frame_payload) != 1 or not isinstance(frame_payload[0], dict):
        raise WorkflowContractError("retained input frame artifact is invalid")
    frame = frame_payload[0]
    frame_digest = hashlib.sha256(json.dumps(frame, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if frame_digest != provenance.get("input_frame_sha256"):
        raise WorkflowContractError("retained input frame digest does not match")
    plan_ref = f"artifact://{frame_run_id}/query-plan"
    plan = ARTIFACTS.read_json(context, plan_ref)
    if not isinstance(plan, dict):
        raise WorkflowContractError("retained query plan lineage is unavailable")
    if plan.get("ontology") is not None:
        ontology_contract.verify_plan(plan)
    elif not isinstance(plan.get("plan_sha256"), str):
        raise WorkflowContractError("retained query plan lineage is incomplete")
    plan_digest = hashlib.sha256(json.dumps({key: value for key, value in plan.items() if key != "plan_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if plan_digest != provenance.get("plan_sha256") or plan.get("plan_sha256") != provenance.get("plan_sha256"):
        raise WorkflowContractError("retained query plan digest does not match")
    if provenance.get("business_question") != plan.get("business_question"):
        raise WorkflowContractError("retained business-question lineage does not match")
    if provenance.get("executor_kind") == "execute_ml_contract" and provenance.get("analysis_contract") != plan.get("analysis_contract"):
        raise WorkflowContractError("retained analysis-contract lineage does not match")


def _analysis_coverage(context: dict[str, str], execution_ref: str, report_manifest_ref: str | None, manifest: dict[str, Any]) -> dict[str, Any]:
    """Check retained ML-contract execution, not natural-language or scientific completeness."""
    run_id, parts = parse_artifact_ref(execution_ref)
    if parts != ("sandbox-execution",):
        raise WorkflowContractError("analysis coverage requires a sandbox execution")
    coverage: dict[str, Any] = {
        "status": "not_assessed", "scope": "retained_plan_and_comparison_fact_presence",
        "business_question_status": "not_assessed", "actions_granted": [], "automatic_retry": False,
        "uncertainty": {"status": "not_assessed", "fact_refs": []},
        "gaps": [],
    }
    provenance_ref = f"artifact://{run_id}/sandbox-provenance"
    provenance = ARTIFACTS.read_json(context, provenance_ref)
    if not isinstance(provenance, dict) or provenance.get("report_manifest_ref") != report_manifest_ref:
        raise WorkflowContractError("analysis provenance is not paired with this report")
    execution = ARTIFACTS.read_json(context, execution_ref)
    if not isinstance(execution, dict) or "error" not in execution or execution.get("error") is not None or not isinstance(execution.get("results"), list) or provenance.get("computation_status") != "succeeded" or provenance.get("report_status") != ("partial" if manifest.get("format") == ml_report_contract.REPORT_MANIFEST_FORMAT and ml_report_contract.presentation_errors(manifest) else "accepted"):
        coverage.update(gaps=[{"code": "EXECUTION_STATUS_NOT_VERIFIED", "message": "The retained execution/report status is missing, indeterminate, malformed, or incompatible; no evidence is promoted."}])
        return coverage
    business_question = provenance.get("business_question")
    if business_question is not None and (not isinstance(business_question, str) or not business_question.strip() or len(business_question) > 2048 or any(token in business_question.lower() for token in ("<", ">", "http://", "https://", "javascript:"))):
        raise WorkflowContractError("retained business question is invalid")
    if provenance.get("generic_repair_of") is not None:
        _verify_generic_repair_lineage(context, execution_ref, provenance)
        if not isinstance(business_question, str) or manifest.get("purpose") != business_question.strip():
            raise WorkflowContractError("repaired report purpose does not match the retained business question")
    if business_question is not None:
        coverage["business_question"] = {
            "status": "not_assessed", "source": "retained_question_unverified", "value": business_question.strip(),
            "execution_ref": execution_ref, "report_manifest_ref": report_manifest_ref, "provenance_ref": provenance_ref,
        }
    analysis = provenance.get("analysis_contract")
    if analysis is None:
        return coverage
    if not isinstance(analysis, dict):
        raise WorkflowContractError("retained analysis contract is invalid")
    task_kind = analysis.get("task_kind", "binary_classification" if analysis.get("kind") else None)
    if not task_kind:
        return coverage
    if not isinstance(task_kind, str) or len(task_kind) > 128:
        raise WorkflowContractError("retained analysis task is invalid")
    if task_kind not in {"regression", "binary_classification"}:
        return coverage
    for key in ("plan_sha256", "input_frame_sha256"):
        digest = provenance.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise WorkflowContractError("retained analysis identity is invalid")
    frame_ref = provenance.get("input_frame_ref")
    if not isinstance(frame_ref, str) or parse_artifact_ref(frame_ref)[1] != ("grafana-frame",):
        raise WorkflowContractError("retained analysis frame identity is invalid")
    # Read retained metadata only: an existing-results reuse grant does not
    # authorize reading the original frame or approving new computations.
    coverage["requirement"] = {"task_kind": task_kind, "plan_sha256": provenance["plan_sha256"], "frame_ref": frame_ref}
    coverage["provenance_ref"] = provenance_ref
    executor_kind = provenance.get("executor_kind")
    coverage["executor_kind"] = executor_kind
    trusted = provenance.get("trusted_ml_contract")
    if executor_kind == "execute_ml_contract" and isinstance(trusted, bool) and trusted:
        try:
            _verify_analysis_lineage(context, execution_ref, provenance)
        except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
            coverage.update(
                status="not_assessed", business_question_status="not_assessed",
                gaps=[{"code": "LINEAGE_NOT_VERIFIED", "message": f"Trusted analysis lineage could not be verified: {exc}. No analytical completion is claimed."}],
            )
            return coverage
        if business_question is None:
            coverage.update(status="incomplete", business_question_status="not_assessed", gaps=[{"code": "BUSINESS_QUESTION_NOT_RECORDED", "message": "The retained analysis has no host-recorded business question. Create a new confirmed plan; do not infer the question from metrics, report prose, or chart titles."}])
            return coverage
        if manifest.get("purpose") != business_question.strip():
            raise WorkflowContractError("report purpose does not match the retained business question")
        coverage["business_question_status"] = "assessed"
        coverage["business_question"] = {
            "status": "assessed", "source": "host_retained_plan", "value": business_question.strip(),
            "plan_sha256": provenance["plan_sha256"], "input_frame_ref": frame_ref,
            "input_frame_sha256": provenance["input_frame_sha256"], "execution_ref": execution_ref,
            "report_manifest_ref": report_manifest_ref, "provenance_ref": provenance_ref,
        }
        # Use the effective objective recorded by the host. Older explicit
        # contracts can identify a metric, but missing classification metadata
        # must not be guessed from whichever scores happen to be available.
        objective = provenance.get("planned_objective", analysis.get("objective"))
        if objective is None and task_kind == "regression":
            objective = "mae"  # The existing regression contract supports MAE only.
        if objective is None:
            coverage.update(status="incomplete", gaps=[{"code": "PLANNED_OBJECTIVE_NOT_RECORDED", "message": "The retained classification objective is not recorded. Inspect retained plan evidence; do not infer it from available metrics or rerun analysis merely to repair metadata."}])
            return coverage
        allowed_objectives = {"mae"} if task_kind == "regression" else {"accuracy", "roc_auc", "pr_auc"}
        if not isinstance(objective, str) or objective not in allowed_objectives:
            raise WorkflowContractError("retained analysis objective is invalid")
        if analysis.get("objective") is not None and analysis["objective"] != objective:
            raise WorkflowContractError("retained planned objective differs from the analysis contract")
        coverage["requirement"]["objective"] = objective
        # These are trusted presentation-schema paths, not method selection.
        # Do not compare scores or require that a model beats the baseline.
        paths = (f"selected_metrics.{objective}", f"baseline_metrics.holdout_{objective}") if task_kind == "regression" else (f"results.selected.{objective}", f"results.baseline.{objective}")
        refs = ["facts." + path.replace(".", "_") for path in paths] if manifest.get("format") in ml_report_contract.REPORT_MANIFEST_FORMATS else list(paths)
        facts = ml_report_contract.build_fact_catalog(manifest)
        if all(ref in facts and facts[ref]["kind"] == "number" and isinstance(facts[ref]["value"], (int, float)) and not isinstance(facts[ref]["value"], bool) and (not isinstance(facts[ref]["value"], float) or math.isfinite(facts[ref]["value"])) for ref in refs):
            coverage.update(status="evidence_available", gaps=[], evidence_fact_refs=refs)
            if task_kind in {"binary_classification", "regression"}:
                interval_refs = [f"{ref}_interval_{index}" for ref in refs for index in (0, 1)]
                metadata_refs = ["facts.process_uncertainty_confidence", "facts.process_uncertainty_samples"]
                confidence = facts.get(metadata_refs[0], {}).get("value") if isinstance(facts.get(metadata_refs[0]), dict) else None
                samples = facts.get(metadata_refs[1], {}).get("value") if isinstance(facts.get(metadata_refs[1]), dict) else None
                try:
                    confidence_value = float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None
                except (TypeError, ValueError, OverflowError):
                    confidence_value = None
                valid_metadata = confidence_value is not None and math.isfinite(confidence_value) and 0 < confidence_value <= 1 and isinstance(samples, int) and not isinstance(samples, bool) and 100 <= samples <= 10_000
                valid_intervals = all(ref in facts and facts[ref]["kind"] == "number" and isinstance(facts[ref]["value"], (int, float)) and not isinstance(facts[ref]["value"], bool) and (not isinstance(facts[ref]["value"], float) or math.isfinite(facts[ref]["value"])) for ref in interval_refs)
                if valid_metadata and valid_intervals:
                    coverage["uncertainty"] = {
                        "status": "available", "method": "nonparametric_bootstrap_fixed_holdout_predictions",
                        "confidence": confidence_value, "samples": samples, "fact_refs": interval_refs, "metadata_fact_refs": metadata_refs,
                    }
                elif any(ref in facts for ref in (*interval_refs, *metadata_refs)):
                    coverage["uncertainty"] = {"status": "not_recorded", "fact_refs": [], "gap_code": "UNCERTAINTY_NOT_RECORDED"}
        else:
            coverage.update(status="incomplete", gaps=[{
                "code": "COMPARISON_FACTS_UNAVAILABLE",
                "message": "Trusted execution is recorded but its comparison facts are missing or invalid in this report. Inspect retained results and repair the trusted evidence export; do not repeat analysis merely to repair a report.",
            }])
    else:
        coverage.update(status="incomplete", gaps=[{
            "code": "PLANNED_ANALYSIS_NOT_EXECUTED",
            "message": "The retained ML contract has no trusted ML execution in this report; profiling or generic Python cannot substitute for it.",
        }])
    return coverage


def prepare_ml_report(args: dict[str, Any]) -> dict[str, Any]:
    step = "prepare_ml_report"
    try:
        unexpected = sorted(set(args) - {"report_manifest_ref", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        context, execution_ref, manifest, artifacts, outputs = _report_material(args)
        facts = ml_report_contract.build_fact_catalog(manifest)
        analysis_coverage = _analysis_coverage(context, execution_ref, args.get("report_manifest_ref"), manifest)
        report_context = {
            "execution_ref": execution_ref,
            "report_manifest_ref": args.get("report_manifest_ref"),
            "manifest": manifest,
            "artifacts": artifacts,
            "outputs": outputs,
            "facts": facts,
            "analysis_coverage": analysis_coverage,
            "restrictions": {
                "numeric_text": "Use evidence fact_ref + format; narrative fields contain no digits.",
                "flow": "Choose sections, order, titles, charts, collapsed state, and narratives from this report; no fixed template.",
                "inspection": "Inspect every artifact in bounded vision or spec batches before composing.",
            },
        }
        context_name = "report-context-manifest"
        context_run_id = ARTIFACTS.create_run(context)
        report_context_ref = ARTIFACTS.write_json(context, context_run_id, context_name, report_context)
    except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; keep successful analysis outputs and correct only the opaque report reference.")
    refs = {"execution_ref": execution_ref, "report_context_ref": report_context_ref}
    refs["report_manifest_ref"] = args["report_manifest_ref"]
    return success_response(
        step=step, run_id=context_run_id, refs=refs,
        instruction="Read analysis_coverage before composing: evidence_available verifies retained execution, comparison facts, and (for new trusted plans) exact host-retained business-question lineage; it still does not prove the question is scientifically or operationally answered. Resolve incomplete evidence within existing approvals and budgets, or explicitly use compose_ml_dashboard delivery_status='partial' to disclose supported findings with a host-rendered limitation notice; unchanged retries grant no authority. Inspect every artifact through inspect_report_artifacts in bounded batches (use spec when no PNG capability exists), then pass report_context_ref and all inspection refs to compose_ml_dashboard without inventing numbers.",
        evidence={"artifact_count": len(artifacts), "fact_count": len(facts), "presentation_errors": ml_report_contract.presentation_errors(manifest)},
        report_context={
            "purpose": manifest.get("purpose"), "conclusion": manifest.get("conclusion"),
            "artifacts": artifacts, "facts": facts,
            "analysis_coverage": analysis_coverage,
            "restrictions": report_context["restrictions"],
        },
    )


def _read_report_context(args: dict[str, Any]) -> tuple[dict[str, str], str, dict[str, Any]]:
    context = context_from_args(args)
    report_context_ref = args.get("report_context_ref")
    if not isinstance(report_context_ref, str) or not report_context_ref.startswith("artifact://"):
        raise WorkflowContractError("report_context_ref is required")
    report_context = ARTIFACTS.read_json(context, report_context_ref)
    if not isinstance(report_context, dict) or not isinstance(report_context.get("artifacts"), list) or not isinstance(report_context.get("outputs"), dict):
        raise WorkflowContractError("report context is invalid")
    return context, report_context_ref, report_context


class ReportContextChanged(WorkflowContractError):
    """Retained evidence identity failed, not a synthesis field to repair."""


def _context_digest(report_context: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(report_context, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _read_inspection(context: dict[str, str], ref: Any) -> dict[str, Any]:
    if not isinstance(ref, str) or parse_artifact_ref(ref)[1] != ("report-inspection",):
        raise WorkflowContractError("inspection ref must reference a report-inspection receipt")
    receipt = ARTIFACTS.read_json(context, ref)
    if not isinstance(receipt, dict):
        raise WorkflowContractError("inspection receipt is invalid")
    return receipt


def _inspection_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Expand only a host receipt, never caller-supplied context/receipt overrides."""
    if "inspection_ref" not in args:
        return args
    if any(key in args for key in ("report_context_ref", "inspection_refs", "report_manifest_ref")):
        raise WorkflowContractError("inspection_ref cannot be combined with other report references")
    receipt = _read_inspection(context_from_args(args), args["inspection_ref"])
    prior = receipt.get("prior_inspection_refs", [])
    if not isinstance(prior, list) or len(prior) >= 8 or not isinstance(receipt.get("context_sha256"), str):
        raise WorkflowContractError("inspection cursor is invalid; legacy receipts require the explicit context/refs interface")
    return {**args, "report_context_ref": receipt.get("report_context_ref"), "inspection_refs": [*prior, args["inspection_ref"]]}


def _inspection_coverage(context: dict[str, str], report_context_ref: str, report_context: dict[str, Any], inspection_refs: Any):
    if not isinstance(inspection_refs, list) or not 1 <= len(inspection_refs) <= 8 or any(not isinstance(ref, str) for ref in inspection_refs) or len(set(inspection_refs)) != len(inspection_refs):
        raise WorkflowContractError("inspection_refs must contain one to eight unique refs")
    coverage: dict[str, set[str]] = {}
    view_modes: dict[tuple[str, str], set[str]] = {}
    modes = set()
    digest = _context_digest(report_context)
    for inspection_ref in inspection_refs:
        receipt = _read_inspection(context, inspection_ref)
        if receipt.get("report_context_ref") != report_context_ref or not isinstance(receipt.get("coverage"), dict):
            raise WorkflowContractError("inspection receipt does not belong to this report")
        if "context_sha256" in receipt and receipt["context_sha256"] != digest:
            raise ReportContextChanged("report context changed after inspection")
        receipt_mode = receipt.get("mode")
        if receipt_mode not in {"vision", "spec"}:
            raise WorkflowContractError("inspection receipt mode is invalid")
        modes.add(receipt_mode)
        for artifact_id, view_ids in receipt["coverage"].items():
            if not isinstance(view_ids, list):
                raise WorkflowContractError("inspection coverage is invalid")
            artifact_key = str(artifact_id)
            coverage.setdefault(artifact_key, set()).update(str(view_id) for view_id in view_ids)
            for view_id in view_ids:
                view_modes.setdefault((artifact_key, str(view_id)), set()).add(receipt_mode)
    return coverage, view_modes, modes


def inspect_report_artifacts(args: dict[str, Any]) -> dict[str, Any]:
    step = "inspect_report_artifacts"
    try:
        unexpected = sorted(set(args) - {"report_manifest_ref", "inspection_ref", "report_context_ref", "artifact_ids", "mode", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        if sum(key in args for key in ("report_manifest_ref", "inspection_ref", "report_context_ref")) != 1:
            raise WorkflowContractError("pass exactly one report_manifest_ref, inspection_ref, or report_context_ref")
        mode = args.get("mode", "spec")
        if mode not in {"vision", "spec"}:
            raise WorkflowContractError("inspection mode must be vision or spec")
        args = _inspection_arguments(args)
        if "report_manifest_ref" in args:
            prepared = prepare_ml_report({key: args[key] for key in ("report_manifest_ref", "_server_context") if key in args})
            if not prepared["ok"]:
                return {**prepared, "step": step}
            args = {**args, "report_context_ref": prepared["refs"]["report_context_ref"]}
        context, report_context_ref, report_context = _read_report_context(args)
        prior_refs = args.get("inspection_refs", [])
        prior_coverage = _inspection_coverage(context, report_context_ref, report_context, prior_refs)[0] if prior_refs else {}
        if len(prior_refs) >= 8:
            raise WorkflowContractError("inspection cursor exceeds the eight receipt bound")
        pending = [item["artifact_id"] for item in report_context["artifacts"] if not {view["view_id"] for view in item["figure_spec"]["views"]}.issubset(prior_coverage.get(item["artifact_id"], set()))]
        artifact_ids = args.get("artifact_ids", pending[:8])
        if "artifact_ids" not in args and not pending:
            raise WorkflowContractError("inspection already complete; compose using the existing inspection_ref")
        if not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= 8 or any(not isinstance(item, str) for item in artifact_ids) or len(set(artifact_ids)) != len(artifact_ids):
            raise WorkflowContractError("artifact_ids must contain one to eight unique ids")
        available = {item["artifact_id"]: item for item in report_context["artifacts"] if isinstance(item, dict) and item.get("artifact_id")}
        if any(not isinstance(artifact_id, str) or artifact_id not in available for artifact_id in artifact_ids):
            raise WorkflowContractError("inspection references an unknown artifact")
        execution = ARTIFACTS.read_json(context, report_context["execution_ref"])
        results = execution.get("results")
        if not isinstance(results, list):
            raise WorkflowContractError("report execution results are invalid")
        details = []
        image_content = []
        coverage: dict[str, list[str]] = {}
        for artifact_id in artifact_ids:
            output = report_context["outputs"][artifact_id]
            detail = {"artifact_id": artifact_id, "figure_spec": output["figure_spec"]}
            if "presentation_error" in output:
                detail["presentation_error"] = output["presentation_error"]
            elif "plotly_index" in output:
                detail["figure"] = _plotly_figure(results[output["plotly_index"]], legacy=report_context["manifest"]["format"] == ml_report_contract.LEGACY_REPORT_MANIFEST_FORMAT)
            details.append(detail)
            view_ids = [view["view_id"] for view in output["figure_spec"]["views"]]
            coverage[artifact_id] = view_ids
            if mode == "vision":
                if "png_index" not in output:
                    raise WorkflowContractError("artifact has no PNG fallback; use spec inspection")
                try:
                    png_result = results[output["png_index"]]
                    png_data = png_result["mime"]["image/png"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise WorkflowContractError("artifact PNG output is unavailable") from exc
                if not isinstance(png_data, str):
                    raise WorkflowContractError("artifact PNG output is invalid")
                image_content.append({"type": "image", "data": png_data, "mimeType": "image/png"})
        receipt_run_id = ARTIFACTS.create_run(context)
        inspection_ref = ARTIFACTS.write_json(context, receipt_run_id, "report-inspection", {
            "report_context_ref": report_context_ref, "mode": mode, "coverage": coverage,
            "prior_inspection_refs": prior_refs, "context_sha256": _context_digest(report_context),
        })
    except (ArtifactAuthError, ReportContextChanged) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; retained report evidence is unauthorized or changed. Investigate its identity; do not retry or rerun analysis.", evidence={"repair_kind": "report_identity", "automatic_retry": False})
    except (WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Correct only the report reference or bounded inspection request; do not rerun analysis.")
    output = success_response(
        step=step, run_id=receipt_run_id, refs={"inspection_ref": inspection_ref, "report_context_ref": report_context_ref, "report_manifest_ref": report_context["report_manifest_ref"], **({"previous_inspection_ref": args["inspection_ref"]} if "inspection_ref" in args else {})},
        instruction="Read the returned facts, analysis_coverage and actual artifact evidence. If remaining_artifact_count is nonzero, continue inspect_report_artifacts with this inspection_ref; otherwise synthesize across the report and call compose_ml_dashboard with this inspection_ref. A receipt proves supplied evidence, not comprehension or visual verification; spec mode is not vision.",
        evidence={"artifact_count": len(details), "mode": mode, "remaining_artifact_count": len(set(pending) - set(artifact_ids)), "presentation_errors": ml_report_contract.presentation_errors(report_context["manifest"])},
        report_context={"purpose": report_context["manifest"].get("purpose"), "conclusion": report_context["manifest"].get("conclusion"), **{key: report_context[key] for key in ("facts", "artifacts", "analysis_coverage", "restrictions")}},
        inspection={"mode": mode, "artifacts": details},
    )
    output["_mcp_content"] = image_content
    return output


def compose_ml_dashboard(args: dict[str, Any]) -> dict[str, Any]:
    step = "compose_ml_dashboard"
    try:
        unexpected = sorted(set(args) - {"inspection_ref", "report_context_ref", "inspection_refs", "synthesis", "uid", "title", "output_mode", "delivery_status", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        output_mode = args.get("output_mode", "ref")
        if output_mode not in {"ref", "full"}:
            raise WorkflowContractError("output_mode must be ref or full")
        delivery_status = args.get("delivery_status", "standard")
        if delivery_status not in {"standard", "partial"}:
            raise WorkflowContractError("delivery_status must be standard or partial; it is not an analysis completion claim")
        args = _inspection_arguments(args)
        context, report_context_ref, report_context = _read_report_context(args)
        coverage, view_modes, modes = _inspection_coverage(context, report_context_ref, report_context, args.get("inspection_refs"))
        analysis_coverage = _analysis_coverage(context, report_context["execution_ref"], report_context.get("report_manifest_ref"), report_context["manifest"])
        figure_errors = ml_report_contract.presentation_errors(report_context["manifest"])
        if figure_errors:
            delivery_status = "partial"
        if report_context.get("analysis_coverage") is not None and report_context["analysis_coverage"] != analysis_coverage:
            raise WorkflowContractError("retained analysis coverage changed after report preparation")
        if analysis_coverage["status"] == "incomplete" and delivery_status != "partial":
            if analysis_coverage["gaps"][0]["code"] in {"COMPARISON_FACTS_UNAVAILABLE", "PLANNED_OBJECTIVE_NOT_RECORDED"}:
                return error_response(
                    step=step, error="comparison facts are unavailable in this report", recoverable=True,
                    instruction="Inspect retained results and repair their trusted report evidence export; do not rerun analysis or query merely to repair a report. Use fresh report refs after repair. If no authorized repair capability is available, disclose the evidence-export limitation or explicitly choose delivery_status='partial' for supported findings; do not retry unchanged inputs or fabricate facts.",
                    evidence={"repair_kind": "report_evidence", "analysis_coverage": analysis_coverage},
                )
            return error_response(
                step=step, error="planned analysis evidence is incomplete", recoverable=True,
                instruction="Keep successful query/profile outputs. Supply missing analysis evidence only through existing approval and budget gates, then prepare its fresh report ref; do not retry this unchanged composition or rerun the query. If the scope must change, request confirmation. If evidence cannot be obtained, explicitly choose delivery_status='partial' to render supported findings with a host-generated limitation notice, without marking the analysis complete.",
                evidence={"repair_kind": "missing_analysis_evidence", "analysis_coverage": analysis_coverage},
            )
    except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; the report context or retained analysis identity is invalid or unauthorized. Correct the reference or investigate its provenance; do not rerun query or analysis.", evidence={"repair_kind": "report_identity", "automatic_retry": False})
    try:
        required_coverage = {
            item["artifact_id"]: {view["view_id"] for view in item["figure_spec"]["views"]}
            for item in report_context["artifacts"]
        }
        missing_artifacts = sorted(artifact_id for artifact_id, view_ids in required_coverage.items() if not view_ids.issubset(coverage.get(artifact_id, set())))
        if missing_artifacts:
            return error_response(
                step=step, error="whole-report inspection is incomplete: " + ", ".join(missing_artifacts), recoverable=True,
                instruction="Continue inspect_report_artifacts with the latest inspection_ref, or use legacy context/artifact_ids for bounded missing-artifact batches. Read that evidence before composing; rewriting synthesis cannot replace inspection. Do not rerun query or analysis.",
                evidence={"repair_kind": "report_inspection", "missing_artifact_ids": missing_artifacts, "automatic_retry": False},
            )
        synthesis = args.get("synthesis")
        if not isinstance(synthesis, dict):
            raise WorkflowContractError("synthesis is required")
        validated = ml_report_contract.validate_report_synthesis(report_context["manifest"], synthesis)
        cited_facts = {item["fact_ref"] for item in validated["thesis_evidence"]}
        for section in validated["sections"]:
            cited_facts.update(item["fact_ref"] for block in section["narrative_blocks"] for item in block["evidence"])
            for panel in section["panels"]:
                cited_facts.update(item["fact_ref"] for item in panel["evidence"])
                cited_facts.update(item["fact_ref"] for view in panel["view_narratives"] for item in view["evidence"])
                available_views = required_coverage.get(panel["artifact_id"], set())
                if not set(panel["view_ids"]).issubset(available_views):
                    raise WorkflowContractError("synthesis references an unknown or uninspected view")
                for narrative in panel["view_narratives"]:
                    visual_observation = narrative["visual_observation"]
                    inspected_modes = view_modes.get((panel["artifact_id"], narrative["view_id"]), set())
                    if "vision" not in inspected_modes and visual_observation is not None:
                        raise WorkflowContractError("spec-only view cannot claim a visual observation")
        missing_facts = sorted(set(analysis_coverage.get("evidence_fact_refs", [])) - cited_facts)
        if missing_facts:
            return error_response(
                step=step, error="report omits available comparison evidence", recoverable=True,
                instruction="Cite the existing comparison fact refs in the report's evidence blocks, in a location appropriate to the question. Revise only the synthesis; do not rerun analysis or query. A valid input-row count cannot replace comparison evidence.",
                evidence={"repair_kind": "report_synthesis", "missing_fact_refs": missing_facts, "analysis_coverage": analysis_coverage, "automatic_retry": False},
            )
        missing_uncertainty_fact_refs = sorted(set(analysis_coverage.get("uncertainty", {}).get("fact_refs", [])) - cited_facts)
        if missing_uncertainty_fact_refs:
            return error_response(
                step=step, error="report omits available uncertainty evidence", recoverable=True,
                instruction="Cite the existing uncertainty interval fact refs in the report's evidence blocks and disclose their fixed-holdout limitations. Revise only the synthesis; do not rerun analysis or query.",
                evidence={"repair_kind": "report_synthesis", "missing_uncertainty_fact_refs": missing_uncertainty_fact_refs, "analysis_coverage": analysis_coverage, "automatic_retry": False},
            )
        retained_question = analysis_coverage.get("business_question")
        if not isinstance(retained_question, dict):
            retained_question = {}
        dashboard = ml_dashboard_compositor.compose_dashboard(
            report_context["manifest"], validated, execution_ref=report_context["execution_ref"], outputs=report_context["outputs"],
            report_manifest_ref=report_context.get("report_manifest_ref"),
            partial_notice=[gap["message"] for gap in analysis_coverage["gaps"]] + [item["artifact_id"] + ": " + item["message"] for item in figure_errors] if delivery_status == "partial" else None,
            business_question=retained_question.get("value") if isinstance(retained_question.get("value"), str) else None,
            business_question_source=retained_question.get("source") if isinstance(retained_question.get("source"), str) else None,
            analysis_status=analysis_coverage.get("status"),
            analysis_gaps=analysis_coverage.get("gaps"),
            uid=str(args.get("uid") or ""), title=str(args.get("title") or ""),
        )
        ml_dashboard_contract.validate_preview_dashboard(dashboard)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; report inspection evidence is unauthorized. Do not retry or rerun analysis to bypass authorization.", evidence={"repair_kind": "authorization", "automatic_retry": False})
    except (WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Revise only the report synthesis or missing dashboard arguments; do not rerun query or analysis.", evidence={"repair_kind": "report_synthesis", "automatic_retry": False})
    dashboard_run_id = ARTIFACTS.create_run(context)
    dashboard_ref = ARTIFACTS.write_json(context, dashboard_run_id, "dashboard", dashboard)
    result = success_response(
        step=step, run_id=dashboard_run_id, refs={"dashboard_ref": dashboard_ref, "report_context_ref": report_context_ref, "report_manifest_ref": report_context["report_manifest_ref"], **({"inspection_ref": args["inspection_ref"]} if "inspection_ref" in args else {})},
        instruction=("This is a partial report: disclose its limitations, not analytical completion. " if delivery_status == "partial" else "") + "The evidence-bound opaque dashboard is ready. Send {dashboard: {$dashboard_ref: dashboard_ref}} to the approved Grafana writer; the host resolves the ref and keeps the full dashboard out of the model context.",
        evidence={"sections": len(synthesis["sections"]), "artifacts": sum(len(section["panels"]) for section in synthesis["sections"]), "inspection_modes": sorted(modes), "analysis_coverage": analysis_coverage, "presentation_errors": figure_errors},
        dashboard_ref=dashboard_ref, delivery_status=delivery_status,
    )
    if output_mode == "full":
        result["dashboard"] = dashboard
    return result


EVIDENCE_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": ml_report_contract.MAX_EVIDENCE,
    "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "fact_ref": {"type": "string"},
            "format": {"type": "string", "enum": sorted(ml_report_contract.EVIDENCE_FORMATS)},
            "label": {"type": "string", "minLength": 1, "maxLength": ml_report_contract.MAX_TEXT},
        },
        "required": ["fact_ref", "format"],
    },
}
VIEW_NARRATIVE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "view_id": {"type": "string"},
        "headline": {"type": "string"},
        "data_observation": {"type": "string"},
        "visual_observation": {"type": ["string", "null"]},
        "interpretation": {"type": "string"},
        "limitation": {"type": "string"},
        "next_step": {"type": "string"},
        "evidence": EVIDENCE_SCHEMA,
    },
    "required": list(ml_report_contract.VIEW_REQUIRED_FIELDS),
}
PANEL_SYNTHESIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "artifact_id": {"type": "string"},
        "view_ids": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True, "items": {"type": "string"}},
        "view_narratives": {"type": "array", "maxItems": 12, "items": VIEW_NARRATIVE_SCHEMA, "default": [], "description": "Optional evidence-bound details for any selected views needing separate explanation. Do not repeat the panel narrative for every view."},
        "headline": {"type": "string"},
        "observation": {"type": "string"},
        "interpretation": {"type": "string"},
        "cross_chart_context": {"type": "string"},
        "limitation": {"type": "string"},
        "next_step": {"type": "string"},
        "evidence": EVIDENCE_SCHEMA,
        "priority": {"type": "string", "enum": sorted(ml_report_contract.PRIORITIES), "default": "supporting"},
        "preferred_width": {"type": "string", "enum": sorted(ml_report_contract.WIDTHS), "default": "full"},
    },
    "required": list(ml_report_contract.PANEL_REQUIRED_FIELDS),
}
NARRATIVE_BLOCK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "block_id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
        "evidence": EVIDENCE_SCHEMA,
        "priority": {"type": "string", "enum": sorted(ml_report_contract.PRIORITIES)},
    },
    "required": ["block_id", "title", "body", "evidence", "priority"],
}
SECTION_SYNTHESIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "section_id": {"type": "string"}, "title": {"type": "string"}, "purpose": {"type": "string"},
        "collapsed": {"type": "boolean", "default": False},
        "narrative_blocks": {"type": "array", "maxItems": 8, "items": NARRATIVE_BLOCK_SCHEMA, "default": []},
        "panels": {"type": "array", "items": PANEL_SYNTHESIS_SCHEMA},
    },
    "required": list(ml_report_contract.SECTION_REQUIRED_FIELDS),
}
REPORT_SYNTHESIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Exact evidence-bound report shape. Top-level keys are exactly format, report_title, thesis, thesis_evidence, and sections; pass uid and dashboard title as separate compose arguments. Narrative text must contain no digits; put every number in evidence using fact_ref and an allowed format. Explain each panel's observation, meaning and limitation in plain language. Cross-chart context, next steps and per-view details are optional when useful, not a repeated template.",
    "properties": {
        "format": {"const": ml_report_contract.REPORT_FORMAT},
        "report_title": {"type": "string"},
        "thesis": {"type": "string"},
        "thesis_evidence": EVIDENCE_SCHEMA,
        "sections": {"type": "array", "minItems": 1, "maxItems": ml_report_contract.MAX_SECTIONS, "items": SECTION_SYNTHESIS_SCHEMA},
    },
    "required": ["format", "report_title", "thesis", "thesis_evidence", "sections"],
}

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
    "name": "grant_artifact_reuse",
    "description": "After explicit user approval in the original source session, grant reuse of existing execution results to one user-specified session of the same org/user. Does not grant uploads, input datasets, or new computation.",
    "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"execution_ref": {"type": "string"}, "target_session_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{16,128}$"}}, "required": ["execution_ref", "target_session_id"]},
}, {
    "name": "prepare_ml_report",
    "description": "Legacy separate preparation; prefer inspect_report_artifacts(report_manifest_ref) to read facts and actual evidence together. Return a bounded artifact/fact catalog and analysis_coverage for one host-owned report-manifest-v1. Coverage checks trusted execution, comparison facts, and exact host-retained business-question/version/scope/result lineage; it does not prove scientific or operational completeness, and not_assessed is not a pass. Re-export an eligible pre-manifest trusted execution through Sandbox first; output indexes and manual legacy binding are rejected.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {"report_manifest_ref": {"type": "string", "description": "Required host-owned opaque report-manifest-v1 ref returned by Sandbox or trusted re-export."}},
        "required": ["report_manifest_ref"],
    },
}, {
    "name": "inspect_report_artifacts",
    "description": "Read report facts and actual artifact evidence directly from report_manifest_ref; no separate preparation is needed. Return up to eight pending artifacts plus inspection_ref and remaining_artifact_count. Continue with the latest inspection_ref until complete, then compose with that ref. Default spec is text evidence, not visual inspection; explicit vision returns PNG MCP images. Legacy report_context_ref/artifact_ids remain supported. No analysis execution or Grafana write.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "report_manifest_ref": {"type": "string", "description": "Start reading an authorized host report manifest."},
            "inspection_ref": {"type": "string", "description": "Continue from the last returned inspection receipt; do not collect ref arrays."},
            "report_context_ref": {"type": "string", "description": "Legacy prepared context."},
            "artifact_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string"}, "description": "Optional selection; omitted selects the next bounded pending batch."},
            "mode": {"type": "string", "enum": ["vision", "spec"], "default": "spec"},
        },
        "oneOf": [{"required": [key]} for key in ("report_manifest_ref", "inspection_ref", "report_context_ref")],
    },
}, {
    "name": "compose_ml_dashboard",
    "description": "Validate one whole-report LLM synthesis against deterministic facts/artifacts and retained analysis_coverage, then persist an opaque Grafana dashboard reference without choosing its flow. not_assessed means completeness was not evaluated, not missing work or failure; report known omissions separately. Incomplete coverage returns evidence.repair_kind: missing_analysis_evidence requires existing approvals/budgets; report_evidence means repair retained evidence, not rerun analysis; identity/authorization failures stop. Explicit delivery_status='partial' permits supported findings with a mandatory host-rendered limitation notice, not a completed analysis. Unchanged retries grant no authority. Dashboard construction is not proof the business question is answered.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "report_context_ref": {"type": "string"},
            "inspection_refs": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string"}},
            "inspection_ref": {"type": "string", "description": "Use the last complete inspection receipt; the host verifies its context and preceding batches. Do not also pass legacy context/ref arrays."},
            "synthesis": REPORT_SYNTHESIS_SCHEMA,
            "uid": {"type": "string", "maxLength": 40, "description": "A new unique Preview dashboard UID for this analysis session; include a short session/upload suffix, keep it at most 40 characters, and never reuse another session's UID."},
            "title": {"type": "string", "description": "Dashboard title; pass separately from synthesis."},
            "output_mode": {"type": "string", "enum": ["ref", "full"], "default": "ref", "description": "ref keeps the composed dashboard opaque and small; full is only for local contract tests."},
            "delivery_status": {"type": "string", "enum": ["standard", "partial"], "default": "standard", "description": "Use partial for known omissions in the delivered report, not merely because automated assessment is not_assessed. Standard reports can retain an unassessed completeness notice. Neither value certifies that the business question is fully answered or changes evidence, authorization or publication gates."},
        },
        "required": ["synthesis", "uid", "title"],
        "oneOf": [
            {"required": ["inspection_ref"], "not": {"anyOf": [{"required": ["report_context_ref"]}, {"required": ["inspection_refs"]}]}},
            {"required": ["report_context_ref", "inspection_refs"], "not": {"required": ["inspection_ref"]}},
        ],
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
            "grant_artifact_reuse": (grant_artifact_reuse, {"execution_ref", "target_session_id", "_server_context"}),
            "prepare_ml_report": (prepare_ml_report, {"report_manifest_ref", "_server_context"}),
            "inspect_report_artifacts": (inspect_report_artifacts, {"report_manifest_ref", "inspection_ref", "report_context_ref", "artifact_ids", "mode", "_server_context"}),
            "compose_ml_dashboard": (compose_ml_dashboard, {"inspection_ref", "report_context_ref", "inspection_refs", "synthesis", "uid", "title", "output_mode", "delivery_status", "_server_context"}),
        }
        if name not in handlers:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        handler, allowed = handlers[name]
        unexpected = sorted(set(arguments) - allowed)
        output = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Pass only declared tool arguments.") if unexpected else handler(arguments)
        image_content = output.get("_mcp_content") if isinstance(output, dict) else None
        text_output = {key: value for key, value in output.items() if key != "_mcp_content"}
        content = [{"type": "text", "text": json.dumps(text_output, ensure_ascii=False)}]
        if isinstance(image_content, list):
            content.extend(image_content)
        return rpc_result(rid, {"content": content, "isError": not bool(output.get("ok"))})
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
            {"type": ml_plotly_contract.PLOTLY_PLUGIN_ID, "options": {"renderMode": "image", "fallbackUrl": "$asset_url_plot"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_plot", "$execution_ref": execution_ref, "output_index": 0}]},
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
            "asset_url_resolved_without_panel_generation": image_result.get("ok") and "/assets/" in image_result.get("dashboard", {}).get("panels", [{}])[0].get("options", {}).get("fallbackUrl", "") and "askO11yAssetBindings" not in image_result.get("dashboard", {}).get("panels", [{}])[0],
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
