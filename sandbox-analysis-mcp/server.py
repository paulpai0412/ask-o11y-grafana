#!/usr/bin/env python3
"""Authenticated MCP boundary for ephemeral OpenSandbox Python analysis."""
from __future__ import annotations

import argparse
import ast
import base64
import contextlib
import csv
import queue
import hashlib
import io
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import tomllib
import urllib.parse
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


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
uploaded_datasets = load_module("uploaded_datasets", ROOT / "uploaded_datasets.py")
ontology_contract = load_module("ontology_contract", ROOT / "ontology_contract.py")
ml_plotly_contract = load_module("ml_plotly_contract", ROOT / "sandbox-analysis-mcp/ml_plotly_contract.py")
ml_report_contract = load_module("ml_report_contract", ROOT / "ml_report_contract.py")
ml_presentation = load_module("ml_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")
data_profile = load_module("data_profile", ROOT / "sandbox-analysis-mcp/data_profile.py")
ArtifactStore = artifact_store.ArtifactStore
ArtifactAuthError = artifact_store.ArtifactAuthError
WorkflowContractError = workflow_node.WorkflowContractError
authenticate_headers = mcp_security.authenticate_headers
error_response = workflow_node.error_response
parse_artifact_ref = workflow_node.parse_artifact_ref
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
success_response = workflow_node.success_response

try:
    PORT = int(os.environ.get("SANDBOX_ANALYSIS_MCP_PORT", "8777"))
except ValueError:
    PORT = 8777

SERVER_INFO = {"name": "sandbox-analysis-mcp", "version": "0.5.0"}
PROTOCOL = "2025-03-26"
MAX_CODE_BYTES = 32 * 1024
MAX_RPC_BODY_BYTES = 128 * 1024
MAX_INPUT_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024
MAX_INLINE_RESULT_BYTES = 32 * 1024
MAX_OUTPUT_FIELDS = 200
MAX_OUTPUT_ITEMS = 64
MAX_DERIVED_ROWS = 5_000
MAX_DERIVED_BYTES = 4 * 1024 * 1024
DERIVED_FRAME_MIME = "application/vnd.ask-o11y.dataframe+json"
DEFAULT_SEED = 42
DEFAULT_PRESENTATION_MODE = "plotly"
PRESENTATION_MODES = ("plotly", "image")
ARTIFACT_PUBLIC_BASE = os.environ.get("ARTIFACT_PUBLIC_BASE", "http://127.0.0.1:8777").rstrip("/")
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()


def _plotly_capability_description() -> str:
    capability = ml_plotly_contract.capability_summary()
    return (
        " Installed native Plotly presentation: "
        + json.dumps(capability, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + ". A failed figure remains visible as an error while successful results stay available. Respect any user instruction to stop on failure; never silently drop a figure or retry computation. Corrected Python still needs exact-call approval."
    )


TOOLS = [
    {"name": "reconcile_operation", "description": "Recover a session-owned compute receipt from durable host completion evidence without running Python again. An indeterminate status is not success and never authorizes redispatch.", "inputSchema": {"type": "object", "additionalProperties": False, "required": ["operation_id"], "properties": {"operation_id": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}}},
    {"name": "get_ml_capabilities", "description": "Inspect actual imports and package versions in the configured sandbox image, without user data. Returns supported trusted task/split combinations and sequential global-budget limits; unsupported or unavailable algorithms are never substituted.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}}},
    {
        "name": "execute_python_analysis",
        "description": "Execute generated Python in a fresh network-denied OpenSandbox over one authorized Grafana frame after preview confirmation. Return the numbers, tables, text or charts needed to answer the question. Charts are optional; when needed, emit Plotly figures as *.json using the capability below. Only renderable outputs receive a fresh report_manifest_ref for dashboard synthesis. Use presentation_mode='image' only when the user explicitly requests a static PNG. The sandbox receives df, pd, np, display(value), and emit(value, name=None). Derived datasets and model-input chaining are disabled. Arbitrary Python outputs cannot claim verified ML; use execute_ml_contract for that. Name JSON results *.json for bounded inline return and DataFrame/string downloads *.csv for a signed URL. The offline image includes SciPy, Matplotlib, Seaborn, Plotly, scikit-learn, statsmodels, SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn, and Optuna." + _plotly_capability_description(),
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_ref": {"type": "string", "description": "Opaque authorized grafana-frame artifact ref."},
                "python_code": {"type": "string", "maxLength": MAX_CODE_BYTES, "description": "Python source executed only inside the isolated sandbox."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED},
                "presentation_mode": {"type": "string", "enum": list(PRESENTATION_MODES), "default": DEFAULT_PRESENTATION_MODE, "description": "Defaults to interactive Plotly; set to image only for an explicitly requested static PNG."},
            },
            "required": ["frame_ref", "python_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "profile_dataset",
        "description": "Deterministic full-data profile over one authorized Grafana frame. Uses every returned row and every returned field; produces bounded facts and visual-only aggregations for distributions, missingness, relationships, and temporal trend. It never samples, truncates, creates a derived dataset, or selects an ML method.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_ref": {"type": "string", "description": "Opaque authorized grafana-frame artifact ref."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED},
            },
            "required": ["frame_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reexport_trusted_report",
        "description": "Re-export one successful pre-manifest profile or trusted ML execution into a fresh host-owned report_manifest_ref without running Python again. Requires authenticated server provenance and rejects generic or untrusted outputs; use the returned fresh ref for prepare_ml_report.",
        "inputSchema": {
            "type": "object",
            "properties": {"execution_ref": {"type": "string", "description": "Opaque successful pre-manifest sandbox-execution ref."}},
            "required": ["execution_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "repair_generic_report",
        "description": "Repair only report persistence from same-session succeeded generic Python with retained, contract-valid renderables. Cannot change invalid charts or code. Existing indeterminate operations must be reconciled first. Contract rejection requires corrected Python on the original authorized frame, not this tool. Creates fresh refs without rerunning Python or changing original receipts; remains generic and untrusted.",
        "inputSchema": {
            "type": "object",
            "properties": {"execution_ref": {"type": "string", "description": "Opaque same-session generic sandbox-execution ref from a succeeded/rejected report receipt."}},
            "required": ["execution_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "execute_ml_contract",
        "description": "Structured supervised-ML executor over one authorized Grafana frame. Takes the Planner's opaque plan_ref as contract_ref; the trusted host composes a deterministic classification or chronological-regression pipeline from the pinned target, features, split, algorithms, budget, and objective. Regression compares Dummy, Ridge, Random Forest, Extra Trees, Histogram Gradient Boosting, and optional CatBoost/XGBoost on shared chronological folds, then evaluates holdout once. No model-authored Python.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_ref": {"type": "string", "description": "Opaque authorized grafana-frame artifact ref."},
                "contract_ref": {"type": "string", "description": "Opaque Planner query-plan artifact ref carrying the ontology-pinned analysis contract."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED},
            },
            "required": ["frame_ref", "contract_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "execute_python_preprocessing",
        "description": "Execute generated Python over one authorized original uploaded CSV/XLSX document in a fresh network-denied OpenSandbox after preview confirmation. The sandbox receives document_path, input_format, pd, np, emit, and emit_frame. emit_frame returns both a derived_frame_ref and a session-owned derived_dataset_id for later Sandbox or Grafana Query steps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_ref": {"type": "string", "description": "Opaque authorized uploaded-document artifact ref returned by Grafana Query inspect_dataset."},
                "python_code": {"type": "string", "maxLength": MAX_CODE_BYTES, "description": "Python source executed only inside the isolated sandbox."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED}
            },
            "required": ["document_ref", "python_code"],
            "additionalProperties": False
        }
    },
    {
        "name": "list_python_analyses",
        "description": "List the authenticated user's recent Sandbox Analysis revisions so a later conversation can rediscover opaque refs without raw data.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "inspect_python_analysis",
        "description": "Read one authorized Sandbox Analysis revision's generated Python, fields, output summary, and provenance for revision; never returns frame rows.",
        "inputSchema": {
            "type": "object",
            "properties": {"provenance_ref": {"type": "string", "description": "Opaque sandbox-provenance ref returned by execute/list/revise."}},
            "required": ["provenance_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "revise_python_analysis",
        "description": "Execute replacement Python against the same authorized persisted Grafana frame as an earlier Sandbox Analysis revision. Numbers, tables and text need no charts. Renderable replacement outputs receive a fresh report_manifest_ref; use it instead of legacy execution coordinates." + _plotly_capability_description(),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provenance_ref": {"type": "string", "description": "Opaque prior sandbox-provenance ref."},
                "python_code": {"type": "string", "maxLength": MAX_CODE_BYTES, "description": "Complete replacement Python source."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED},
                "presentation_mode": {"type": "string", "enum": list(PRESENTATION_MODES), "default": DEFAULT_PRESENTATION_MODE, "description": "Defaults to interactive Plotly; set to image only for an explicitly requested static PNG."},
            },
            "required": ["provenance_ref", "python_code"],
            "additionalProperties": False,
        },
    },
]


def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-Actor-User-Id") or headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    if org and user:
        return {"org_id": str(org), "user_id": str(user), "session_id": str(headers.get("X-Grafana-Session-Id") or "")}
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
        return {"org_id": str(raw["org_id"]), "user_id": str(raw["user_id"]), "session_id": str(raw.get("session_id") or "")}
    raise WorkflowContractError("verified artifact context is required")


def sandbox_policy() -> dict[str, Any]:
    return {
        "timeout_seconds": 3600,
        "ready_timeout_seconds": 3600,
        "resource": {"cpu": "4", "memory": "4Gi"},
        "network_default_action": "deny",
        "env": {},
        "volumes": [],
    }


def runtime_settings() -> dict[str, str]:
    image = os.environ.get("SANDBOX_IMAGE", "").strip()
    if not image:
        raise RuntimeError("SANDBOX_IMAGE is required")
    allow_unpinned = os.environ.get("SANDBOX_ALLOW_UNPINNED_IMAGE") == "1"
    if not re.fullmatch(r"(?:[^@\s]+@)?sha256:[a-f0-9]{64}", image) and not allow_unpinned:
        raise RuntimeError("SANDBOX_IMAGE must be digest-pinned; set SANDBOX_ALLOW_UNPINNED_IMAGE=1 only for local development")
    runtime_class = os.environ.get("SANDBOX_RUNTIME_CLASS", "").strip().lower()
    if runtime_class not in {"gvisor", "kata", "firecracker"}:
        if not (runtime_class == "runc" and os.environ.get("SANDBOX_ALLOW_RUNC") == "1"):
            raise RuntimeError("SANDBOX_RUNTIME_CLASS must be gvisor, kata, or firecracker; runc requires SANDBOX_ALLOW_RUNC=1 for local development")
    config_path = Path(os.environ.get("SANDBOX_SERVER_CONFIG", ""))
    if not config_path.is_file():
        raise RuntimeError("SANDBOX_SERVER_CONFIG must reference the OpenSandbox server TOML used by the control plane")
    try:
        config_bytes = config_path.read_bytes()
        server_config = tomllib.loads(config_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"cannot validate SANDBOX_SERVER_CONFIG: {exc}") from exc
    configured_type = str((server_config.get("secure_runtime") or {}).get("type") or "runc").lower()
    if configured_type != runtime_class:
        raise RuntimeError(f"SANDBOX_RUNTIME_CLASS {runtime_class!r} does not match control-plane secure_runtime {configured_type!r}")
    egress = server_config.get("egress") or {}
    if egress.get("mode") != "dns+nft" or not bool(egress.get("disable_ipv6")):
        raise RuntimeError("OpenSandbox control-plane config must enforce dns+nft egress with IPv6 disabled")
    protocol = os.environ.get("SANDBOX_PROTOCOL", "http").strip().lower()
    if protocol not in {"http", "https"}:
        raise RuntimeError("SANDBOX_PROTOCOL must be http or https")
    return {
        "image": image,
        "domain": os.environ.get("SANDBOX_DOMAIN", "localhost:8080").strip(),
        "protocol": protocol,
        "runtime_class": configured_type,
        "server_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
    }


def read_authorized_frame(context: dict[str, str], frame_ref: str) -> tuple[str, dict[str, Any]]:
    run_id, parts = parse_artifact_ref(frame_ref)
    if parts != ("grafana-frame",):
        raise WorkflowContractError("frame_ref must reference a grafana-frame artifact")
    frames = ARTIFACTS.read_json(context, frame_ref)
    if not isinstance(frames, list) or len(frames) != 1 or not isinstance(frames[0], dict):
        raise WorkflowContractError("grafana-frame artifact must contain exactly one frame")
    return run_id, frames[0]


def read_authorized_document(context: dict[str, str], document_ref: str) -> tuple[bytes, dict[str, Any]]:
    _run_id, parts = parse_artifact_ref(document_ref)
    if parts != ("uploaded-document",):
        raise WorkflowContractError("document_ref must reference an uploaded-document artifact")
    document = ARTIFACTS.read_json(context, document_ref)
    if not isinstance(document, dict) or not isinstance(document.get("upload_id"), str) or not isinstance(document.get("session_id"), str):
        raise WorkflowContractError("uploaded document artifact is invalid")
    path, metadata = uploaded_datasets.read_source(context, document["upload_id"], document["session_id"])
    if not 0 < path.stat().st_size <= uploaded_datasets.MAX_UPLOAD_BYTES:
        raise WorkflowContractError("uploaded document size is invalid")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != document.get("source_sha256") or metadata.get("source_format") != document.get("source_format"):
        raise WorkflowContractError("uploaded document hash/format mismatch")
    return raw, document


def validate_frame(frame: dict[str, Any]) -> tuple[list[str], int]:
    fields = frame.get("schema", {}).get("fields")
    values = frame.get("data", {}).get("values")
    if not isinstance(fields, list) or not isinstance(values, list) or len(fields) != len(values):
        raise WorkflowContractError("columnar frame fields/values must be equal-length arrays")
    names = [field.get("name") if isinstance(field, dict) else None for field in fields]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise WorkflowContractError("columnar frame field names must be unique non-empty strings")
    if any(not isinstance(column, list) for column in values):
        raise WorkflowContractError("columnar frame values must be arrays")
    lengths = {len(column) for column in values}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise WorkflowContractError("columnar frame columns must have one shared non-zero row count")
    return [str(name) for name in names], next(iter(lengths))


def validate_ml_execution_contract(contract: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    task_kind = analysis.get("task_kind", "binary_classification")
    allow_missing_row_drop = task_kind == "regression"
    try:
        analysis_missing_policy = ontology_contract.normalize_missing_target_split_policy(
            analysis.get("missing_value_policy"), allow_drop=allow_missing_row_drop
        )
        input_missing_policy = ontology_contract.normalize_missing_target_split_policy(
            contract.get("missing_value_policy"), allow_drop=allow_missing_row_drop
        )
    except ValueError as exc:
        raise WorkflowContractError(str(exc)) from exc
    if analysis_missing_policy != input_missing_policy:
        raise WorkflowContractError("missing_value_policy differs between analysis and execution contract")
    if analysis.get("sample_weight_fields", []) != []:
        raise WorkflowContractError("sample weights are not supported by this executor")
    split = analysis.get("split") or {}
    fraction = split.get("test_fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0.05 <= fraction <= 0.5:
        raise WorkflowContractError("split test_fraction must be between 0.05 and 0.5")
    if "seed" in analysis and "seed" in split and analysis["seed"] != split["seed"]:
        raise WorkflowContractError("analysis and split seeds must agree")
    kind = analysis.get("kind")
    if task_kind == "regression":
        algorithms = analysis.get("algorithms")
        supported = {"dummy", "ridge", "random_forest", "extra_trees", "hist_gradient_boosting", "catboost", "xgboost"}
        if not isinstance(algorithms, list) or not algorithms or any(item not in supported for item in algorithms):
            raise WorkflowContractError("unsupported regression algorithm")
        split = analysis.get("split") or {}
        split_kind = split.get("kind")
        split_field = split.get("time_field") if split_kind == "chronological_holdout" else split.get("group_field")
        if split_kind not in {"chronological_holdout", "grouped_holdout"} or not isinstance(split_field, str) or not split_field:
            raise WorkflowContractError("regression requires a chronological or grouped holdout field")
        constrained = analysis.get("constrained_search")
        if constrained is not None and (not isinstance(constrained, dict) or not isinstance(constrained.get("bounds"), dict)):
            raise WorkflowContractError("constrained regression search is invalid")
        expected_template = "ask_o11y_regression_v1"
    else:
        if task_kind != "binary_classification" or kind not in ontology_contract.ALLOWED_ANALYSIS_KINDS:
            raise WorkflowContractError("unsupported ML analysis kind")
        split = analysis.get("split") or {}
        if split.get("kind") != "stratified_holdout" or split.get("time_field") or split.get("group_field"):
            raise WorkflowContractError("classification currently supports stratified_holdout only; requested split was not executed")
        algorithms = analysis.get("algorithms") or [kind]
        if not isinstance(algorithms, list) or any(item not in ontology_contract.ALLOWED_ANALYSIS_KINDS for item in algorithms) or len(set(algorithms)) != len(algorithms):
            raise WorkflowContractError("unsupported or duplicate classification algorithms")
        if analysis.get("class_imbalance_strategy") not in {None, "none", "balanced"}:
            raise WorkflowContractError("unsupported class imbalance strategy")
        expected_template = f"ask_o11y_{kind}_v1"
    if contract.get("execution_template") != expected_template:
        raise WorkflowContractError("ML execution template does not match analysis kind")
    if contract.get("preprocessing_fit_scope") != "training_only":
        raise WorkflowContractError("ML preprocessing must be fit on training data only")
    result: dict[str, Any] = {
        "execution_template": expected_template,
        "preprocessing_fit_scope": "training_only",
        "missing_value_policy": analysis_missing_policy,
    }
    autoresearch = contract.get("autoresearch")
    objectives = {"mae"} if task_kind == "regression" else {"accuracy", "roc_auc", "pr_auc"}
    if autoresearch is not None:
        if not isinstance(autoresearch, dict) or autoresearch.get("objective") not in objectives:
            raise WorkflowContractError("autoresearch objective is invalid")
        budget = autoresearch.get("search_budget")
        if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 40:
            raise WorkflowContractError("autoresearch budget is invalid")
        result["autoresearch"] = autoresearch
    objective = (autoresearch or {}).get("objective", analysis.get("objective", "mae" if task_kind == "regression" else "roc_auc"))
    if not isinstance(objective, str) or objective not in objectives:
        raise WorkflowContractError("ML objective is invalid")
    if analysis.get("objective") is not None and analysis["objective"] != objective:
        raise WorkflowContractError("ML objective conflicts with the analysis contract")
    result["planned_objective"] = objective
    return result


def verify_plan_for_context(context: dict[str, str], plan: dict[str, Any]) -> None:
    dataset_id = str(plan.get("dataset_id") or "")
    if not dataset_id.startswith("upload_"):
        if plan.get("ontology") is None and isinstance(plan.get("plan_sha256"), str):
            payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
            actual = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if plan["plan_sha256"] != actual:
                raise WorkflowContractError("CONTRACT_HASH_MISMATCH")
        else:
            ontology_contract.verify_plan(plan)
        return
    claimed = plan.get("plan_sha256")
    ontology = plan.get("ontology")
    if not isinstance(claimed, str) or not isinstance(ontology, dict):
        raise WorkflowContractError("CONTRACT_HASH_MISMATCH")
    metadata = uploaded_datasets.inspect_upload(context, dataset_id, context.get("session_id"))
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    actual = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if claimed != actual or ontology.get("sha256") != metadata["source_sha256"] or ontology.get("snapshot_id") != f"candidate:{dataset_id}":
        raise WorkflowContractError("CONTRACT_HASH_MISMATCH")


def read_plan_contract(context: dict[str, str], source_run_id: str, field_names: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        plan = ARTIFACTS.read_json(context, f"artifact://{source_run_id}/query-plan")
    except WorkflowContractError:
        plan = {}
    if not isinstance(plan, dict):
        raise WorkflowContractError("query plan must be an object")
    try:
        verify_plan_for_context(context, plan)
    except (ValueError, WorkflowContractError) as exc:
        raise WorkflowContractError(str(exc)) from exc
    raw_contract = plan.get("analysis_input_contract") if isinstance(plan, dict) else {}
    contract: dict[str, Any] = raw_contract if isinstance(raw_contract, dict) else {}
    rules = contract.get("validity_rules", [])
    if not isinstance(rules, list):
        raise WorkflowContractError("query plan validity_rules must be an array")
    for rule in rules:
        if not isinstance(rule, dict):
            raise WorkflowContractError("query plan validity rule must be an object")
        field = rule.get("field")
        accepted = rule.get("accepted_values")
        if not isinstance(field, str) or field not in field_names or not isinstance(accepted, list) or not accepted:
            raise WorkflowContractError("query plan validity rule is incomplete")
    semantic = {key: plan.get(key) for key in ("ontology", "analysis_contract", "business_question", "plan_sha256") if plan.get(key) is not None}
    business_question = semantic.get("business_question")
    if business_question is not None and (not isinstance(business_question, str) or not business_question.strip() or len(business_question.encode("utf-8")) > 2048 or any(token in business_question.lower() for token in ("<", ">", "http://", "https://", "javascript:"))):
        raise WorkflowContractError("query plan business_question is invalid")
    semantic_core = set(semantic) - {"business_question"}
    if semantic_core and semantic_core not in ({"ontology", "analysis_contract", "plan_sha256"}, {"ontology", "plan_sha256"}, {"plan_sha256"}):
        raise WorkflowContractError("ontology analysis plan contract is incomplete")
    if semantic_core == {"ontology", "analysis_contract", "plan_sha256"}:
        analysis = semantic.get("analysis_contract")
        if not isinstance(analysis, dict):
            raise WorkflowContractError("ontology analysis contract must be an object")
        semantic.update(validate_ml_execution_contract(contract, analysis))
    return rules, semantic


def wrapped_code(python_code: str, seed: int) -> str:
    # The full traceback belongs in the authorized execution artifact; the
    # model-visible response is redacted separately in execute_python_analysis.
    return f"from capture import run\nrun({python_code!r}, '/tmp/input-frame.json', {seed})"


def wrapped_document_code(python_code: str, input_format: str, seed: int) -> str:
    return f"from capture import run_document\nrun_document({python_code!r}, '/tmp/input-document.{input_format}', {input_format!r}, {seed})"


POST_COMPLETE_GRACE_SECONDS = 10


def _is_execution_complete(line: Any) -> bool:
    raw = line.decode("utf-8", errors="ignore") if isinstance(line, bytes) else line
    if not isinstance(raw, str):
        return False
    if raw.startswith("data:"):
        raw = raw[5:].strip()
    try:
        return json.loads(raw).get("type") == "execution_complete"
    except (TypeError, ValueError, AttributeError):
        return False


class _ExecutionCompleteResponse:
    """Drain trailing SSE frames, then bound a stream that never closes."""

    def __init__(self, response: Any):
        self._response = response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def iter_lines(self):
        frames: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=128)

        def pump() -> None:
            try:
                for line in self._response.iter_lines():
                    frames.put(("line", line))
            except Exception as exc:
                frames.put(("error", exc))
            finally:
                frames.put(("done", None))

        reader = threading.Thread(target=pump, name="opensandbox-sse-reader", daemon=True)
        reader.start()
        complete_deadline = None
        while True:
            timeout = None if complete_deadline is None else max(0, complete_deadline - time.monotonic())
            try:
                kind, payload = frames.get(timeout=timeout)
            except queue.Empty:
                self._response.close()
                return
            if kind == "line":
                yield payload
                if _is_execution_complete(payload) and complete_deadline is None:
                    complete_deadline = time.monotonic() + POST_COMPLETE_GRACE_SECONDS
            elif kind == "error":
                if complete_deadline is None:
                    raise payload
                time.sleep(max(0, complete_deadline - time.monotonic()))
                return
            else:
                if complete_deadline is not None:
                    time.sleep(max(0, complete_deadline - time.monotonic()))
                return


class _ExecutionCompleteStream:
    def __init__(self, stream: Any):
        self._stream = stream

    def __enter__(self) -> _ExecutionCompleteResponse:
        return _ExecutionCompleteResponse(self._stream.__enter__())

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        return self._stream.__exit__(exc_type, exc_value, traceback)


class _ExecutionCompleteSSEClient:
    """Wrap the SDK client whose sync adapter waits for HTTP EOF after completion."""

    def __init__(self, client: Any):
        self._client = client

    def stream(self, *args: Any, **kwargs: Any) -> _ExecutionCompleteStream:
        return _ExecutionCompleteStream(self._client.stream(*args, **kwargs))


def serialize_execution(execution: Any) -> dict[str, Any]:
    results = []
    for result in execution.result:
        results.append({"text": result.text, "timestamp": result.timestamp, "mime": dict(result.extra_properties)})
    error = None
    if execution.error is not None:
        error = {
            "name": execution.error.name,
            "value": execution.error.value,
            "timestamp": execution.error.timestamp,
            "traceback": list(execution.error.traceback),
        }
    complete = None
    if execution.complete is not None:
        complete = {
            "timestamp": execution.complete.timestamp,
            "execution_time_in_millis": execution.complete.execution_time_in_millis,
        }
    return {
        "execution_id": execution.id,
        "execution_count": execution.execution_count,
        "exit_code": execution.exit_code,
        "results": results,
        "stdout": [{"text": item.text, "timestamp": item.timestamp} for item in execution.logs.stdout],
        "stderr": [{"text": item.text, "timestamp": item.timestamp} for item in execution.logs.stderr],
        "error": error,
        "complete": complete,
    }


def read_sandbox_bytes(filesystem: Any, path: str, range_header: str) -> bytes:
    last_error = None
    for attempt in range(30):
        try:
            return filesystem.read_bytes(path, range_header=range_header)
        except Exception as exc:
            last_error = exc
            if attempt == 29:
                raise
            time.sleep(0.1)
    if last_error is None:  # pragma: no cover
        raise RuntimeError(f"failed to read sandbox file: {path}")
    raise last_error


def read_captured_outputs(filesystem: Any) -> list[dict[str, Any]]:
    manifest_bytes = read_sandbox_bytes(filesystem, "/tmp/sandbox-output/manifest.json", f"bytes=0-{MAX_OUTPUT_BYTES}")
    if len(manifest_bytes) > MAX_OUTPUT_BYTES:
        raise WorkflowContractError("sandbox output manifest exceeds limit")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("sandbox output manifest is invalid") from exc
    if not isinstance(manifest, list) or len(manifest) > MAX_OUTPUT_ITEMS:
        raise WorkflowContractError(f"sandbox output manifest must contain at most {MAX_OUTPUT_ITEMS} items")
    allowed_mime = {"text/plain", "text/csv", "text/html", "image/png", "application/json", "application/vnd.plotly.v1+json", DERIVED_FRAME_MIME}
    outputs = []
    total = len(manifest_bytes)
    output_root = Path("/tmp/sandbox-output")
    for item in manifest:
        if not isinstance(item, dict) or set(item) != {"path", "mime_type", "display_name"}:
            raise WorkflowContractError("sandbox output manifest item is invalid")
        path = Path(str(item["path"]))
        mime_type = str(item["mime_type"])
        display_name = item["display_name"]
        if not isinstance(display_name, str) or not display_name or len(display_name) > 120:
            raise WorkflowContractError("sandbox output display name is invalid")
        if path.parent != output_root or mime_type not in allowed_mime:
            raise WorkflowContractError("sandbox output path or MIME type is not allowed")
        payload = read_sandbox_bytes(filesystem, str(path), f"bytes=0-{MAX_OUTPUT_BYTES}")
        total += len(payload)
        if total > MAX_OUTPUT_BYTES:
            raise WorkflowContractError("sandbox captured outputs exceed limit")
        if mime_type == "image/png":
            outputs.append({"text": None, "timestamp": 0, "mime": {mime_type: base64.b64encode(payload).decode("ascii")}, "display_name": display_name})
        else:
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkflowContractError("sandbox text output is not UTF-8") from exc
            outputs.append({"text": text if mime_type == "text/plain" else None, "timestamp": 0, "mime": {} if mime_type == "text/plain" else {mime_type: text}, "display_name": display_name})
    return outputs


def read_captured_logs(filesystem: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    logs = []
    for name in ("stdout", "stderr"):
        payload = read_sandbox_bytes(filesystem, f"/tmp/sandbox-output/{name}.txt", f"bytes=0-{MAX_LOG_BYTES}")
        if len(payload) > MAX_LOG_BYTES:
            raise WorkflowContractError(f"sandbox {name} exceeds limit")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowContractError(f"sandbox {name} is not UTF-8") from exc
        logs.append([] if not text else [{"text": text, "timestamp": 0}])
    return logs[0], logs[1]


def read_input_audit(filesystem: Any) -> dict[str, Any]:
    payload = read_sandbox_bytes(filesystem, "/tmp/sandbox-output/audit.json", "bytes=0-65535")
    try:
        audit = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("sandbox input audit is invalid") from exc
    required = {"input_rows", "valid_rows", "excluded_rows", "rules"}
    if not isinstance(audit, dict) or set(audit) != required:
        raise WorkflowContractError("sandbox input audit is incomplete")
    return audit


def execute_opensandbox_input(input_path: str, input_data: str | bytes, source: str, *, capture_required: bool = True) -> dict[str, Any]:
    from code_interpreter.models.code import SupportedLanguage
    from code_interpreter.sync.code_interpreter import CodeInterpreterSync
    from opensandbox.config import ConnectionConfigSync
    from opensandbox.models.execd_sync import ExecutionHandlersSync
    from opensandbox.models.filesystem import WriteEntry
    from opensandbox.models.sandboxes import NetworkPolicy
    from opensandbox.sync.sandbox import SandboxSync

    settings = runtime_settings()
    policy = sandbox_policy()
    connection = ConnectionConfigSync(
        domain=settings["domain"],
        protocol=settings["protocol"],
        api_key=os.environ.get("SANDBOX_API_KEY") or None,
        request_timeout=timedelta(seconds=policy["timeout_seconds"]),
    )
    sandbox = None
    try:
        sandbox = SandboxSync.create(
            settings["image"],
            timeout=timedelta(seconds=policy["timeout_seconds"]),
            ready_timeout=timedelta(seconds=policy["ready_timeout_seconds"]),
            env=policy["env"],
            metadata={"service": SERVER_INFO["name"]},
            resource=policy["resource"],
            network_policy=NetworkPolicy(defaultAction=policy["network_default_action"]),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            volumes=policy["volumes"],
            connection_config=connection,
        )
        sandbox.files.write_files([WriteEntry(path=input_path, data=input_data, mode=600)])
        code_service = CodeInterpreterSync.create(sandbox=sandbox).codes
        # opensandbox-code-interpreter 0.1.2 can leave the HTTP stream open after
        # emitting execution_complete; consume the terminal event and close it.
        sse_client = getattr(code_service, "_sse_client", None)
        if sse_client is not None:
            setattr(code_service, "_sse_client", _ExecutionCompleteSSEClient(sse_client))
        execution = code_service.run(
            source,
            language=SupportedLanguage.PYTHON,
            handlers=ExecutionHandlersSync(skip_accumulation=capture_required),
        )
        serialized = serialize_execution(execution)
        if not capture_required:
            return serialized
        try:
            serialized["input_audit"] = read_input_audit(sandbox.files)
            serialized["stdout"], serialized["stderr"] = read_captured_logs(sandbox.files)
            serialized["results"].extend(read_captured_outputs(sandbox.files))
        except Exception:
            if execution.error is None:
                raise
        return serialized
    finally:
        if sandbox is not None:
            with contextlib.suppress(Exception):
                sandbox.kill()
            sandbox.close()


def execute_opensandbox(frame_bundle_json: str, python_code: str, seed: int) -> dict[str, Any]:
    return execute_opensandbox_input("/tmp/input-frame.json", frame_bundle_json, wrapped_code(python_code, seed))


def execute_document_opensandbox(document_bytes: bytes, input_format: str, python_code: str, seed: int) -> dict[str, Any]:
    return execute_opensandbox_input(f"/tmp/input-document.{input_format}", document_bytes, wrapped_document_code(python_code, input_format, seed))


def output_asset_summary(execution: dict[str, Any], execution_ref: str) -> list[dict[str, Any]]:
    assets = []
    for index, result in enumerate(execution.get("results", [])):
        mime = result.get("mime") if isinstance(result, dict) else None
        if not isinstance(mime, dict) or not isinstance(mime.get("image/png"), str):
            continue
        assets.append({
            "output_index": index,
            "display_name": result.get("display_name") or f"Output {index + 1}",
            "mime_type": "image/png",
            "$execution_ref": execution_ref,
        })
    return assets


def inline_result_summary(execution: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    inline_results = []
    total = 0
    omitted = False
    for index, result in enumerate(execution.get("results", [])):
        if not isinstance(result, dict):
            continue
        mime = result.get("mime")
        text = result.get("text")
        if isinstance(text, str):
            mime_type, raw = "text/plain", text
        else:
            json_data = mime.get("application/json") if isinstance(mime, dict) else None
            if not isinstance(json_data, str):
                continue
            mime_type, raw = "application/json", json_data
        size = len(raw.encode("utf-8"))
        if size > MAX_INLINE_RESULT_BYTES - total:
            omitted = True
            continue
        try:
            value = raw if mime_type == "text/plain" else json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            omitted = True
            continue
        inline_results.append({"output_index": index, "display_name": result.get("display_name") or f"Output {index + 1}", "mime_type": mime_type, "value": value})
        total += size
    return inline_results, omitted


def output_download_summary(execution: dict[str, Any], execution_ref: str, context: dict[str, str]) -> list[dict[str, Any]]:
    try:
        expires_at = int(time.time()) + int(ARTIFACTS.retention_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorkflowContractError("artifact retention is invalid") from exc
    downloads = []
    for index, result in enumerate(execution.get("results", [])):
        mime = result.get("mime") if isinstance(result, dict) else None
        csv_data = mime.get("text/csv") if isinstance(mime, dict) else None
        if not isinstance(csv_data, str) or len(csv_data.encode("utf-8")) > artifact_assets.MAX_ASSET_BYTES:
            continue
        downloads.append({
            "output_index": index,
            "display_name": result.get("display_name") or f"Output {index + 1}",
            "mime_type": "text/csv",
            "url": artifact_assets.sign_output_url(public_base=ARTIFACT_PUBLIC_BASE, secret=os.environ.get("MCP_SHARED_TOKEN", ""), context=context, execution_ref=execution_ref, output_index=index, expires_at=expires_at),
            "expires_at": expires_at,
        })
    return downloads


def parse_derived_frame(execution: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    candidates = []
    for result in execution.get("results", []):
        mime = result.get("mime") if isinstance(result, dict) else None
        payload = mime.get(DERIVED_FRAME_MIME) if isinstance(mime, dict) else None
        if isinstance(payload, str):
            candidates.append((payload, result.get("display_name") or "derived-data.csv"))
    if not candidates:
        return None
    if len(candidates) != 1:
        raise WorkflowContractError("sandbox may emit exactly one derived frame")
    raw, display_name = candidates[0]
    if len(raw.encode("utf-8")) > MAX_DERIVED_BYTES:
        raise WorkflowContractError("derived frame exceeds bounded output limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowContractError("derived frame JSON is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"format", "columns", "types", "data"} or value.get("format") != "ask-o11y-dataframe-v1":
        raise WorkflowContractError("derived frame contract is invalid")
    columns, logical_types, rows = value["columns"], value["types"], value["data"]
    allowed_types = {"string", "number", "boolean", "time"}
    if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_OUTPUT_FIELDS or any(not isinstance(column, str) or not column for column in columns) or len(set(columns)) != len(columns):
        raise WorkflowContractError("derived frame fields are invalid")
    if not isinstance(logical_types, list) or len(logical_types) != len(columns) or any(item not in allowed_types for item in logical_types):
        raise WorkflowContractError("derived frame types are invalid")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_DERIVED_ROWS:
        raise WorkflowContractError("derived frame rows are invalid")
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise WorkflowContractError("derived frame row width is invalid")
        for cell in row:
            if cell is not None and (isinstance(cell, (dict, list)) or not isinstance(cell, (str, int, float, bool)) or isinstance(cell, float) and not math.isfinite(cell)):
                raise WorkflowContractError("derived frame contains unsupported cell values")
    fields = [{"name": name, "type": logical_type} for name, logical_type in zip(columns, logical_types, strict=True)]
    values = [[row[index] for row in rows] for index in range(len(columns))]
    return {"name": str(display_name), "schema": {"fields": fields}, "data": {"values": values}}, str(display_name)


def derived_frame_csv(frame: dict[str, Any]) -> bytes:
    fields = [field["name"] for field in frame["schema"]["fields"]]
    values = frame["data"]["values"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(fields)
    writer.writerows(zip(*values, strict=True))
    return output.getvalue().encode("utf-8")


def persist_derived_data(context: dict[str, str], execution: dict[str, Any], output_run_id: str, document: dict[str, Any] | None = None) -> tuple[dict[str, str], dict[str, Any] | None]:
    if any(DERIVED_FRAME_MIME in (result.get("mime") or {}) for result in execution.get("results", [])):
        raise WorkflowContractError("derived datasets are disabled; display artifacts cannot become model inputs")
    return {}, None


def output_summary(execution: dict[str, Any]) -> dict[str, Any]:
    mime_types = set()
    output_names = []
    tabular_outputs = []
    stdout_lines = 0
    for index, result in enumerate(execution["results"]):
        if result.get("text") is not None:
            mime_types.add("text/plain")
        mime = result.get("mime", {})
        mime_types.update(str(key) for key in mime)
        if isinstance(result.get("display_name"), str):
            output_names.append(result["display_name"])
        csv_data = mime.get("text/csv") if isinstance(mime, dict) else None
        if isinstance(csv_data, str):
            reader = csv.DictReader(io.StringIO(csv_data))
            rows = list(reader)
            fields = []
            for name in (reader.fieldnames or [])[:MAX_OUTPUT_FIELDS]:
                values = [row.get(name, "") for row in rows[:100] if row.get(name, "") != ""]
                logical_type = "string"
                if values:
                    try:
                        for value in values:
                            float(value)
                        logical_type = "number"
                    except ValueError:
                        try:
                            for value in values:
                                datetime.fromisoformat(value.replace("Z", "+00:00"))
                            logical_type = "time"
                        except ValueError:
                            logical_type = "string"
                fields.append({"name": name, "type": logical_type})
            tabular_outputs.append({"output_index": index, "display_name": result.get("display_name") or f"Output {index + 1}", "row_count": len(rows), "fields": fields})
    for item in execution["stdout"]:
        stdout_lines += len(str(item.get("text", "")).splitlines())
    inline_results, inline_results_truncated = inline_result_summary(execution)
    return {
        "result_count": len(execution["results"]),
        "inline_results": inline_results,
        "inline_results_truncated": inline_results_truncated,
        "mime_types": sorted(mime_types),
        "output_names": output_names,
        "tabular_outputs": tabular_outputs,
        "stdout_lines": stdout_lines,
        "stderr_lines": sum(len(str(item.get("text", "")).splitlines()) for item in execution["stderr"]),
    }


_GENERIC_REPORT_STEPS = frozenset({"execute_python_analysis", "revise_python_analysis"})


def _generic_report_segment(value: Any, fallback: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")[:80]
    if not segment:
        segment = fallback
    if segment[0].isdigit():
        segment = f"artifact_{segment}"
    return segment


def _generic_report_source(
    execution: dict[str, Any], validity: dict[str, Any], *, purpose: str | None = None,
) -> dict[str, Any] | None:
    """Bind captured generic renderables to a fresh host-owned report source.

    Generic Python is not trusted to declare report facts. The host binds only
    its verified input counts; figure output coordinates and digests are added by
    the existing report normalizer after this source is captured. Plotly/PNG
    outputs are paired only when their stem has exactly one of each MIME type.
    """
    facts = {
        "rows": {"kind": "number", "label": "Input rows", "value": validity["input_rows"]},
        "valid_rows": {"kind": "number", "label": "Valid rows", "value": validity["valid_rows"]},
        "excluded_rows": {"kind": "number", "label": "Excluded rows", "value": validity["excluded_rows"]},
    }
    renderables: list[tuple[int, str, str, str]] = []
    by_stem: dict[str, dict[str, list[tuple[int, str]]]] = {}
    for index, result in enumerate(execution.get("results", [])):
        if not isinstance(result, dict):
            continue
        display_name = result.get("display_name")
        mime = result.get("mime")
        if not isinstance(display_name, str) or not isinstance(mime, dict):
            continue
        is_plotly = isinstance(mime.get("application/vnd.plotly.v1+json"), str) or ml_report_contract._is_plotly_output(result)
        is_png = isinstance(mime.get("image/png"), str)
        if not is_plotly and not is_png:
            continue
        kind = "plotly" if is_plotly else "png"
        stem = Path(display_name).stem or f"output_{index + 1}"
        if stem.startswith("ml-plotly-"):
            stem = stem.removeprefix("ml-plotly-")
        by_stem.setdefault(stem, {}).setdefault(kind, []).append((index, display_name))
        renderables.append((index, stem, kind, display_name))

    if not renderables:
        return None
    groups: list[dict[str, Any]] = []
    paired: set[str] = set()
    for _index, stem, kind, display_name in renderables:
        if stem in paired:
            continue
        candidates = by_stem[stem]
        plots, pngs = candidates.get("plotly", []), candidates.get("png", [])
        if len(plots) == 1 and len(pngs) == 1:
            groups.append({"stem": stem, "plotly_output_name": plots[0][1], "png_output_name": pngs[0][1]})
            paired.add(stem)
            continue
        # Ambiguous stems are represented as separate artifacts in captured
        # output order; no renderable is silently dropped or overwritten.
        plot_output_names = {name for _output_index, name in plots}
        for output_index, output_name in sorted((*plots, *pngs)):
            groups.append({"stem": stem, "kind": "plotly" if output_name in plot_output_names else "png", "output_name": output_name, "output_index": output_index})
        paired.add(stem)

    artifacts = []
    used_ids: set[str] = set()
    for group in groups:
        artifact_id = _generic_report_segment(group["stem"], f"artifact_{len(artifacts) + 1}")
        suffix = 2
        while artifact_id in used_ids:
            artifact_id = f"{_generic_report_segment(group['stem'], 'artifact')}_{suffix}"
            suffix += 1
        used_ids.add(artifact_id)
        item = {"artifact_id": artifact_id, "fact_refs": ["rows", "valid_rows", "excluded_rows"]}
        if "plotly_output_name" in group:
            item.update({"plotly_output_name": group["plotly_output_name"], "png_output_name": group["png_output_name"]})
        elif group["kind"] == "plotly":
            item["plotly_output_name"] = group["output_name"]
        else:
            item["png_output_name"] = group["output_name"]
        artifacts.append(item)
    return {
        "format": ml_report_contract.REPORT_SOURCE_FORMAT,
        "purpose": purpose.strip() if isinstance(purpose, str) and purpose.strip() else "Bounded analysis outputs over the authorized Grafana frame.",
        "conclusion": "Evidence is descriptive and requires report synthesis before dashboard publication.",
        "facts": facts,
        "artifacts": artifacts,
    }


def _ensure_generic_report_source(
    execution: dict[str, Any], validity: dict[str, Any], step: str, *, purpose: str | None = None,
) -> bool:
    if step not in _GENERIC_REPORT_STEPS or execution.get("error"):
        return False
    for result in execution.get("results", []):
        if not isinstance(result, dict):
            continue
        display_name = result.get("display_name")
        payload = (result.get("mime") or {}).get("application/json") if isinstance(result.get("mime"), dict) else None
        if isinstance(display_name, str) and display_name.casefold() == "report-source.json":
            raise WorkflowContractError("generic Python cannot emit the reserved report-source.json; the host creates report bindings")
        if isinstance(payload, str):
            try:
                value = json.loads(payload)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict) and value.get("format") == ml_report_contract.REPORT_SOURCE_FORMAT:
                raise WorkflowContractError("generic Python cannot provide report-source-v1; the host creates report bindings")
    source = _generic_report_source(execution, validity, purpose=purpose)
    if source is None:
        return False
    execution.setdefault("results", []).append({
        "text": None,
        "timestamp": 0,
        "mime": {"application/json": json.dumps(source, ensure_ascii=False, separators=(",", ":"))},
        "display_name": "report-source.json",
    })
    return True


def _preflight_report(
    execution: dict[str, Any], validity: dict[str, Any], presentation_mode: str, step: str, *, purpose: str | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """Validate presentation on a copy before retaining the immutable receipt.

    Unsafe or oversized additions retain only the original execution. Once a
    bounded host source is created, retain it even when figure validation fails
    so repair can verify that source without reconstructing or rerunning it.
    """
    if execution.get("error"):
        return execution, "not_attempted", None
    try:
        candidate = json.loads(json.dumps(execution, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        return execution, "rejected", f"sandbox execution cannot be normalized as JSON: {exc}"
    output_mimes = {
        str(mime)
        for result in candidate.get("results", [])
        if isinstance(result, dict) and isinstance(result.get("mime"), dict)
        for mime in result["mime"]
    }
    try:
        source_present = _ensure_generic_report_source(candidate, validity, step, purpose=purpose)
    except (TypeError, ValueError, KeyError, WorkflowContractError) as exc:
        return execution, "rejected", f"generic report source rejected: {exc}"
    # Validate reserved report metadata before allowing standalone plain outputs.
    # A presentation preference is not a requirement to generate a chart.
    if step in _GENERIC_REPORT_STEPS and not source_present:
        if output_mimes <= {"application/json", "text/plain", "text/csv"}:
            return execution, "not_requested", None
        return execution, "rejected", "generic output must be bounded plain data or a named supported renderable"
    required_mime = "application/vnd.plotly.v1+json" if presentation_mode == "plotly" else "image/png"
    if required_mime not in output_mimes:
        return execution, "rejected", f"Plotly figure JSON presentation requires an output with MIME {required_mime}; PNG-only output is not accepted by default" if presentation_mode == "plotly" else f"image presentation requires an output with MIME {required_mime}"
    if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
        return execution, "rejected", f"sandbox output plus report metadata exceeds {MAX_OUTPUT_BYTES} bytes"
    if source_present:
        try:
            manifest = ml_report_contract.normalize_report_manifest(
                execution_ref="artifact://preflight/sandbox-execution", results=candidate.get("results", []),
            )
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return candidate, "rejected", f"report manifest rejected: {exc}"
        return candidate, "partial" if ml_report_contract.presentation_errors(manifest) else "accepted", None
    return candidate, "not_requested", None


def compose_regression_template(plan: dict[str, Any], contract: dict[str, Any], seed: int) -> str:
    """Trusted chronological-regression template; host-owned, never model-authored."""
    split = contract.get("split") or {}
    autoresearch = plan.get("analysis_input_contract", {}).get("autoresearch") or {}
    try:
        seed = int(seed)
        test_fraction = float(split.get("test_fraction", 0.2))
        budget = int(autoresearch.get("search_budget", contract.get("search_budget", 20)))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("regression seed, split, or search budget is invalid") from exc
    if not 0.05 <= test_fraction <= 0.5:
        raise WorkflowContractError("regression holdout fraction is outside bounds")
    algorithms = [str(kind) for kind in contract.get("algorithms") or []]
    target = str(contract["target"])
    features = [str(name) for name in contract["features"]]
    raw_feature_sets = contract.get("feature_sets")
    feature_sets = raw_feature_sets if isinstance(raw_feature_sets, list) and raw_feature_sets else [{"id": "selected", "features": features, "controllable_fields": list(contract.get("controllable_fields") or []), "context_fields": list(contract.get("context_fields") or [])}]
    split_kind = str(split["kind"])
    split_field = str(split["time_field"] if split_kind == "chronological_holdout" else split["group_field"])
    target_direction = ontology_contract.optimization_direction(contract)
    objective = str(autoresearch.get("objective") or "mae")
    plan_sha = str(plan.get("plan_sha256") or "")
    purpose = str(plan.get("business_question") or contract.get("purpose") or "比較迴歸模型並在觀察支持範圍內提出候選設定。")
    conclusion = str(contract.get("conclusion") or "模型與候選設定僅供受控試驗規劃，不代表因果最佳。")
    population_filter = contract.get("population_filter") or {}
    if not isinstance(population_filter, dict):
        raise WorkflowContractError("regression population_filter is invalid")
    try:
        missing_value_policy = ontology_contract.normalize_missing_target_split_policy(contract.get("missing_value_policy"), allow_drop=True)
    except ValueError as exc:
        raise WorkflowContractError(str(exc)) from exc
    constrained = contract.get("constrained_search") or {"enabled": False, "minimum_support": 5, "top_k": 5, "fixed_context": {}, "bounds": {}}
    return f'''# report-capture-stabilization-v6
import math
import matplotlib.pyplot as plt
import pandas as pd
from ml_plotly_contract import sanitize_figure
from ml_presentation import build_report_source
from ml_regression import apply_population_filter, run_multi_model_regression, evaluate_regression_candidate, search_candidate_settings
from ml_execution import outer_split, sum_fit_counts

TARGET = {target!r}
FEATURES = {features!r}
FEATURE_SETS = {feature_sets!r}
SPLIT_KIND = {split_kind!r}
SPLIT_FIELD = {split_field!r}
ALGORITHMS = {algorithms!r}
TARGET_DIRECTION = {target_direction!r}
OBJECTIVE = {objective!r}
SEED = {seed}
BUDGET = {budget}
TEST_FRACTION = {test_fraction!r}
PURPOSE = {purpose!r}
CONCLUSION = {conclusion!r}
CONSTRAINED = {constrained!r}
POPULATION_FILTER = {population_filter!r}
MISSING_VALUE_POLICY = {missing_value_policy!r}
PLAN_SHA = {plan_sha!r}

missing = [name for name in [SPLIT_FIELD, TARGET, *FEATURES, *POPULATION_FILTER] if name not in df.columns]
if missing:
    raise ValueError("authorized frame lacks planned regression fields: " + ", ".join(missing))
source_rows = int(len(df))
work = df[[SPLIT_FIELD, *FEATURES, TARGET, *POPULATION_FILTER]].copy()
work = apply_population_filter(work, POPULATION_FILTER)
filtered_rows = int(len(work))
work[TARGET] = pd.to_numeric(work[TARGET], errors="coerce")
if SPLIT_KIND == "chronological_holdout":
    work[SPLIT_FIELD] = pd.to_datetime(work[SPLIT_FIELD], errors="coerce")
invalid_target_split = work[[SPLIT_FIELD, TARGET]].isna().any(axis=1)
policy_excluded_rows = int(invalid_target_split.sum())
if policy_excluded_rows:
    if MISSING_VALUE_POLICY != {{"mode": "drop_invalid_target_split", "approved": True}}:
        raise ValueError("target/split contains missing or invalid values; approved missing_value_policy is required")
    work = work.loc[~invalid_target_split].copy()
feature_missing_rows = int(work[FEATURES].isna().to_numpy().any(axis=1).sum())
if feature_missing_rows:
    raise ValueError("feature fields contain missing values; feature imputation or feature-row dropping is not authorized")
work = work.sort_values(SPLIT_FIELD, kind="stable").reset_index(drop=True)
if work[TARGET].nunique() < 2:
    raise ValueError("regression target must be numeric, non-missing, and non-constant")
train_indices, holdout_indices, outer_receipt = outer_split(
    SPLIT_KIND, work[TARGET], TEST_FRACTION, SEED,
    times=work[SPLIT_FIELD] if SPLIT_KIND == "chronological_holdout" else None,
    groups=work[SPLIT_FIELD] if SPLIT_KIND == "grouped_holdout" else None,
)
train, holdout = work.iloc[train_indices], work.iloc[holdout_indices]
train_groups = train[SPLIT_FIELD].reset_index(drop=True) if SPLIT_KIND == "grouped_holdout" else None
train_times = train[SPLIT_FIELD].reset_index(drop=True) if SPLIT_KIND == "chronological_holdout" else None
cv_folds = 5 if train_groups is None else min(5, train_groups.nunique())
if cv_folds < 2 or len(train) < 10 or len(holdout) < 2:
    raise ValueError("regression split does not leave enough train and holdout rows")
feature_set_results = []
set_budget = BUDGET // len(FEATURE_SETS)
if set_budget < len(set(ALGORITHMS) | {{"dummy"}}):
    raise ValueError("global budget cannot cover the requested model/feature-set candidates")
for feature_set in FEATURE_SETS:
    set_features = [str(name) for name in feature_set["features"]]
    set_result = run_multi_model_regression(
        train[set_features], train[TARGET], holdout[set_features], holdout[TARGET],
        kinds=ALGORITHMS, seed=SEED, n_iter=set_budget, cv_folds=cv_folds, groups=train_groups, times=train_times, selection_only=True,
    )
    feature_set_results.append({{"id": str(feature_set["id"]), "features": set_features, "controllable_fields": [str(name) for name in feature_set.get("controllable_fields") or []], "context_fields": [str(name) for name in feature_set.get("context_fields") or []], "result": set_result}})
selected_feature_set = min(feature_set_results, key=lambda item: next(row["cv_mae_mean"] for row in item["result"]["comparison"] if row["kind"] == item["result"]["selected_kind"]))
result = evaluate_regression_candidate(selected_feature_set["result"], holdout[selected_feature_set["features"]], holdout[TARGET])
feature_set_comparison = [{{
    "id": item["id"], "feature_count": len(item["features"]), "selected_model": item["result"]["selected_kind"],
    "cv_mae": next(row["cv_mae_mean"] for row in item["result"]["comparison"] if row["kind"] == item["result"]["selected_kind"]),
    "holdout_metrics": result["holdout_metrics"] if item is selected_feature_set else None,
    "baseline_metrics": {{"cv_mae": item["result"]["baseline_cv_mae"]}},
    "fit_counts": item["result"]["fit_counts"],
    "guards": {{"selected_cv_beats_baseline": item["result"]["selected_cv_beats_baseline"]}},
}} for item in feature_set_results]
if CONSTRAINED.get("enabled") and result["can_run_constrained_search"] and selected_feature_set["controllable_fields"]:
    constrained_result = search_candidate_settings(
        result["selected_estimator"], train[selected_feature_set["features"]], beats_baseline=True,
        target_direction=TARGET_DIRECTION,
        controllable_fields=selected_feature_set["controllable_fields"],
        support_group_fields=CONSTRAINED.get("support_group_fields") or [],
        fixed_context=CONSTRAINED.get("fixed_context") or {{}}, bounds=CONSTRAINED.get("bounds") or {{}}, bounds_approved=True,
        minimum_support=int(CONSTRAINED.get("minimum_support", 5)), top_k=int(CONSTRAINED.get("top_k", 5)), seed=SEED,
    )
elif CONSTRAINED.get("enabled"):
    constrained_result = {{"status": "blocked_by_baseline", "candidate_settings": [], "reason": "selected model did not beat Dummy on both CV and holdout"}}
else:
    constrained_result = {{"status": "not_requested", "candidate_settings": []}}

comparison_figure, comparison_axis = plt.subplots(figsize=(9, 4.8))
comparison_axis.bar([row["kind"] for row in result["comparison"]], [row["cv_mae_mean"] for row in result["comparison"]])
comparison_axis.set(title="Selected feature-set model comparison", ylabel="CV MAE")
comparison_axis.tick_params(axis="x", rotation=25)
comparison_figure.tight_layout()
emit(comparison_figure, name="regression_model_comparison.png")
emit(sanitize_figure({{
    "data": [{{"type": "bar", "name": "CV MAE", "x": [row["kind"] for row in result["comparison"]], "y": [float(row["cv_mae_mean"]) for row in result["comparison"]], "marker": {{"color": "#4fd1c5"}}}}],
    "layout": {{"title": "Selected feature-set model comparison", "yaxis": {{"title": "CV MAE", "rangemode": "tozero"}}}},
    "config": {{"displaylogo": False, "responsive": True}},
}}), name="ml-plotly-regression_model_comparison.json")
plt.close(comparison_figure)

feature_set_figure, feature_set_axis = plt.subplots(figsize=(9, 4.8))
feature_set_positions = list(range(len(feature_set_comparison)))
feature_set_axis.bar(feature_set_positions, [row["cv_mae"] for row in feature_set_comparison], label="CV MAE")
feature_set_axis.set_xticks(feature_set_positions, [row["id"] for row in feature_set_comparison], rotation=25)
feature_set_axis.set(title="Nested regression feature-set comparison", ylabel="MAE")
feature_set_axis.legend()
feature_set_figure.tight_layout()
emit(feature_set_figure, name="regression_feature_set_comparison.png")
emit(sanitize_figure({{
    "data": [{{"type": "bar", "name": "CV MAE", "x": [str(row["id"]) for row in feature_set_comparison], "y": [float(row["cv_mae"]) for row in feature_set_comparison], "marker": {{"color": "#7fcaa6"}}}}],
    "layout": {{"title": "Nested regression feature-set comparison", "yaxis": {{"title": "MAE", "rangemode": "tozero"}}}},
    "config": {{"displaylogo": False, "responsive": True}},
}}), name="ml-plotly-regression_feature_set_comparison.json")
plt.close(feature_set_figure)

candidate_figure, candidate_axis = plt.subplots(figsize=(9, 4.8))
candidate_rows = constrained_result.get("candidate_settings") or []
if candidate_rows:
    positions = list(range(len(candidate_rows)))
    predicted = [row["predicted_target"] for row in candidate_rows]
    lower = [row["uncertainty"]["lower"] for row in candidate_rows]
    upper = [row["uncertainty"]["upper"] for row in candidate_rows]
    candidate_axis.errorbar(positions, predicted, yerr=[[value - low for value, low in zip(predicted, lower)], [high - value for value, high in zip(predicted, upper)]], fmt="o")
    candidate_axis.set_xticks(positions, [str(row["settings"]) for row in candidate_rows], rotation=25, ha="right")
    candidate_axis.set(ylabel="Predicted target")
else:
    candidate_axis.text(0.5, 0.5, constrained_result["status"], ha="center", va="center", transform=candidate_axis.transAxes)
candidate_axis.set_title("Observed-support candidate settings")
candidate_figure.tight_layout()
emit(candidate_figure, name="regression_candidate_settings.png")
candidate_plotly_x = list(range(len(candidate_rows))) if candidate_rows else ["status"]
candidate_plotly_y = [float(row["predicted_target"]) for row in candidate_rows] if candidate_rows else [0.0]
candidate_plotly_text = [str(row["settings"]) for row in candidate_rows] if candidate_rows else [str(constrained_result["status"])]
emit(sanitize_figure({{
    "data": [{{"type": "scatter", "mode": "markers", "name": "Predicted target", "x": candidate_plotly_x, "y": candidate_plotly_y, "text": candidate_plotly_text, "marker": {{"color": "#eda06a", "size": 8}}}}],
    "layout": {{"title": "Observed-support candidate settings", "yaxis": {{"title": "Predicted target"}}}},
    "config": {{"displaylogo": False, "responsive": True}},
}}), name="ml-plotly-regression_candidate_settings.json")
plt.close(candidate_figure)

selected_metrics = {{
    "mae": float(result["holdout_metrics"]["mae"]),
    "rmse": float(result["holdout_metrics"]["rmse"]),
    "r2": float(result["holdout_metrics"]["r2"]),
    "mae_interval": [float(value) for value in result["holdout_metrics"]["mae_interval"]],
}}
baseline_metrics = {{
    "cv_mae": float(result["baseline_cv_mae"]),
    "holdout_mae": float(result["baseline_holdout_mae"]),
    "holdout_mae_interval": [float(value) for value in (result.get("baseline_holdout_mae_interval") or [result["baseline_holdout_mae"], result["baseline_holdout_mae"]])],
}}
uncertainty = result["uncertainty"]
uncertainty_summary = {{
    "method": str(uncertainty["method"]),
    "confidence": float(uncertainty["confidence"]),
    "samples": int(uncertainty["samples"]),
    "limitations": {{str(key): str(value) for key, value in uncertainty["limitations"].items()}},
}}
candidate_facts = {{"candidate_count": int(len(candidate_rows)), "candidate_settings_available": bool(candidate_rows)}}
for index, row in enumerate(candidate_rows):
    candidate_facts["candidate_" + str(index) + "_predicted_target"] = float(row["predicted_target"])
    candidate_facts["candidate_" + str(index) + "_support"] = int(row["support"])
    candidate_facts["candidate_" + str(index) + "_uncertainty_lower"] = float(row["uncertainty"]["lower"])
    candidate_facts["candidate_" + str(index) + "_uncertainty_upper"] = float(row["uncertainty"]["upper"])
manifest = {{
    "format": "ask-o11y-ml-regression-v1",
    "schema_version": "ask-o11y.ml-regression/v1",
    "purpose": PURPOSE,
    "conclusion": CONCLUSION,
    "data": {{"rows": int(len(work)), "source_rows": source_rows, "used_rows": int(len(work)), "excluded_rows": policy_excluded_rows, "policy_excluded_rows": policy_excluded_rows, "population_filter_excluded_rows": source_rows - filtered_rows, "train_rows": int(len(train)), "holdout_rows": int(len(holdout)), "features": int(len(FEATURES))}},
    "process": {{"algorithms": [str(value) for value in ALGORITHMS], "search_budget": int(BUDGET), "completed_trials": int(sum(item["result"]["completed_trials"] for item in feature_set_results)), "preprocessing_fit_scope_training_only": True, "sample_weight_fields": [], "selected_feature_set": str(selected_feature_set["id"]), "missing_value_policy": MISSING_VALUE_POLICY, "missing_value_policy_approved": bool(MISSING_VALUE_POLICY["approved"]), "missing_target_split_rows_excluded": policy_excluded_rows, "uncertainty": uncertainty_summary}},
    "results": {{"selected": selected_metrics, "baseline": baseline_metrics}},
    "decision": candidate_facts,
    "guards": {{"selected_cv_beats_baseline": bool(result["selected_cv_beats_baseline"]), "selected_holdout_beats_baseline": bool(selected_metrics["mae"] < baseline_metrics["holdout_mae"]), "can_run_constrained_search": bool(result["can_run_constrained_search"]), "constrained_search_blocked_by_baseline": constrained_result.get("status") == "blocked_by_baseline", "constrained_search_insufficient_support": constrained_result.get("status") == "insufficient_support", "selection_holdout_separation": True, "causal_identification_established": False}},
    "selected_metrics": selected_metrics,
    "baseline_metrics": baseline_metrics,
    "artifacts": [
        {{"name": "regression_model_comparison.png", "caption": "相同切分與預算下的模型比較", "alt_text": "各候選模型的交叉驗證平均絕對誤差長條圖"}},
        {{"name": "regression_feature_set_comparison.png", "caption": "不同 nested feature sets 的泛化比較", "alt_text": "不同 feature set 的交叉驗證誤差比較"}},
        {{"name": "regression_candidate_settings.png", "caption": "觀察支持範圍內的候選設定與不確定區間", "alt_text": "候選設定預測值與 bootstrap 不確定區間"}},
    ],
    "limitations": [
        "Only target/split invalid rows may be excluded, and only under the exact approved missing_value_policy; feature-only missing values are never row-dropped by this policy.",
        "Excluded rows are reported counts, not imputed or silently removed; results remain bounded to the authorized source frame and approved split/model guards.",
    ],
}}
emit({{"source_rows": source_rows, "used_rows": int(len(work)), "eligible_rows": int(len(work)), "excluded_rows": policy_excluded_rows, "policy_excluded_rows": policy_excluded_rows, "population_filter_excluded_rows": source_rows - filtered_rows, "train_rows": int(len(train)), "test_rows": int(len(holdout)), "explained_rows": int(len(holdout)), "target": TARGET, "missing_value_policy": MISSING_VALUE_POLICY, "plan_sha256": PLAN_SHA}}, name="dataset-summary.json")
emit(build_report_source(manifest, plotly_names={"regression_model_comparison", "regression_feature_set_comparison", "regression_candidate_settings"}), name="report-source.json")
emit(manifest, name="ml-regression.json")
'''


def compose_profile_template(plan: dict[str, Any], seed: int) -> str:
    """Compose the host-owned profile program from the authorized plan only."""
    try:
        seed_value = int(seed)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("profile seed is invalid") from exc
    if not 0 <= seed_value <= 4294967295:
        raise WorkflowContractError("profile seed is outside bounds")
    analysis_value = plan.get("analysis_contract")
    analysis_contract: dict[str, Any] = analysis_value if isinstance(analysis_value, dict) else {}
    field_views_value = plan.get("field_views")
    raw_field_views = field_views_value if isinstance(field_views_value, list) else analysis_contract.get("field_views") or []
    if not isinstance(raw_field_views, list):
        raise WorkflowContractError("profile field views are invalid")
    field_views = []
    for item in raw_field_views[:data_profile.MAX_FIELDS]:
        if not isinstance(item, dict):
            raise WorkflowContractError("profile field view is invalid")
        name = item.get("physical_name") or item.get("name")
        if not isinstance(name, str) or not name:
            raise WorkflowContractError("profile field view has no physical name")
        field_views.append({
            key: item[key]
            for key in ("physical_name", "name", "type", "data_type", "semantic_kind", "unit", "analysis_role", "metadata_source")
            if key in item and item[key] is not None
        })
    ontology_value = plan.get("ontology")
    ontology: dict[str, Any] = ontology_value if isinstance(ontology_value, dict) else {}
    identity = {
        "dataset_id": plan.get("dataset_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "ontology_snapshot_id": ontology.get("snapshot_id"),
        "ontology_status": ontology.get("status", "inferred"),
    }
    purpose = str(analysis_contract.get("purpose") or "了解授權資料的完整形狀、缺失、關係與時間結構。")
    conclusion = str(analysis_contract.get("conclusion") or "這是描述性資料概況；任何預測或因果判斷都必須另行取得使用者意圖與核准契約。")
    return f'''from pathlib import Path
from data_profile import build_profile_manifest, build_profile_plotly_figures, build_profile_report_source, profile_dataframe, render_profile_assets

FIELDS_VIEW = {field_views!r}
ONTOLOGY_STATUS = {str(ontology.get("status") or "inferred")!r}
IDENTITY = {identity!r}
PURPOSE = {purpose!r}
CONCLUSION = {conclusion!r}

profile = profile_dataframe(df, fields_view=FIELDS_VIEW, ontology_status=ONTOLOGY_STATUS)
manifest = build_profile_manifest(profile, identity=IDENTITY, purpose=PURPOSE, conclusion=CONCLUSION)
render_profile_assets(manifest, Path("/tmp/data-profile"), frame=df, emit_figure=emit)
plotly_figures = build_profile_plotly_figures(manifest, frame=df)
for plotly_name, plotly_figure in plotly_figures.items():
    emit(plotly_figure, name=f"ml-plotly-{{plotly_name}}.json")
emit(build_profile_report_source(manifest, plotly_names=set(plotly_figures)), name="report-source.json")
emit(manifest, name="data-profile.json")
'''


def compose_ml_template(plan: dict[str, Any], contract: dict[str, Any], seed: int) -> str:
    """Deterministic trusted template for standard supervised ML; host-owned, never model-authored."""
    if contract.get("task_kind") == "regression":
        return compose_regression_template(plan, contract, seed)
    kind = str(contract["kind"])
    try:
        seed = int(seed)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("execution seed is invalid") from exc
    target = str(contract["target"])
    features = [str(name) for name in contract["features"]]
    split = contract.get("split") or {}
    autoresearch = plan.get("analysis_input_contract", {}).get("autoresearch") or {}
    try:
        budget = int(autoresearch.get("search_budget", 20))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("autoresearch budget is invalid") from exc
    objective = str(autoresearch.get("objective", contract.get("objective", "roc_auc")))
    minimum = autoresearch.get("objective_minimum")
    positive = contract.get("positive_class")
    algorithms = contract.get("algorithms") or [kind]
    purpose = str(plan.get("business_question") or contract.get("purpose") or "依核准的分析契約預測目標，供主管判斷是否可試用。")
    conclusion = str(contract.get("conclusion") or "模型驗證完成；營運門檻與成本確認前不建議直接部署。")
    dataset_id = str(plan.get("dataset_id") or "")
    ontology = plan.get("ontology") or {}
    raw_field_views = (plan.get("analysis_contract") or {}).get("field_views") or []
    fields_view = [
        {
            "name": str(item.get("physical_name") or item.get("name")),
            "semantic_kind": str(item.get("semantic_kind") or "unregistered"),
            "unit": item.get("unit"),
            "analysis_role": str(item.get("analysis_role") or "unknown"),
            **({"reason": str(item["reason"])} if item.get("reason") else {}),
        }
        for item in raw_field_views
        if isinstance(item, dict) and (item.get("physical_name") or item.get("name"))
    ][:24]
    try:
        test_fraction = float(split.get("test_fraction", 0.2))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("split test_fraction is invalid") from exc
    imbalance = contract.get("class_imbalance_strategy")
    cost_matrix = contract.get("cost_matrix")
    cost_approved = contract.get("cost_matrix_approved", False)
    if not isinstance(cost_approved, bool) or (cost_approved and not isinstance(cost_matrix, dict)):
        raise WorkflowContractError("cost_matrix_approved requires an explicit cost matrix")
    try:
        reporting_denominator = int(contract.get("reporting_denominator", 1000))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("reporting_denominator is invalid") from exc
    if not 1 <= reporting_denominator <= 1_000_000:
        raise WorkflowContractError("reporting_denominator is outside bounds")
    minimum_recall = contract.get("minimum_recall")
    template = f'''import json
from pathlib import Path
import numpy as np
import pandas as pd
from ml_execution import outer_split
from ml_autoresearch import run_multi_model_comparison
from ml_presentation import build_manifest, build_report_source, render_assets, render_shap_summary, render_model_comparison, build_plotly_figures
import shap

TARGET = {target!r}
FEATURES = {features!r}
POSITIVE = {positive!r}
SEED = {seed}
BUDGET = {budget}
OBJECTIVE = {objective!r}
TEST_FRACTION = {test_fraction!r}
IMBALANCE = {imbalance!r}
KIND = {kind!r}
ALGORITHMS = {algorithms!r}
DATASET_ID = {dataset_id!r}
SNAPSHOT_SHA = {str(ontology.get("sha256") or "")!r}
SNAPSHOT_ID = {str(ontology.get("snapshot_id") or "")!r}
PLAN_SHA = {str(plan.get("plan_sha256") or "")!r}
MINIMUM = {minimum!r}
COST_MATRIX = {cost_matrix!r}
COST_APPROVED = {cost_approved!r}
REPORTING_DENOMINATOR = {reporting_denominator}
MIN_RECALL = {minimum_recall!r}
PURPOSE = {purpose!r}
CONCLUSION = {conclusion!r}
FIELDS_VIEW = {fields_view!r}
PLOTLY_FIELDS_VIEW = FIELDS_VIEW

if TARGET not in df.columns:
    raise ValueError("target column missing from authorized frame")
missing = [name for name in FEATURES if name not in df.columns]
if missing:
    raise ValueError("authorized frame lacks planned features: " + ", ".join(missing))

raw_target = df[TARGET]
if POSITIVE is None:
    y_all = pd.to_numeric(raw_target, errors="coerce")
    if y_all.isna().any() or not set(y_all.unique()) <= {{0, 1}}:
        raise ValueError("binary target requires complete 0/1 values or an explicit positive class; no rows were dropped")
    work = df.copy()
    y = y_all.astype(int)
else:
    labels = raw_target.astype("string").str.strip()
    if labels.isna().any() or (labels == "").any() or labels.nunique() != 2 or str(POSITIVE) not in set(labels):
        raise ValueError("positive class must match one of exactly two non-missing labels")
    y = (labels == str(POSITIVE)).astype(int)
    work = df.copy()
X = work[FEATURES]
if y.nunique() != 2:
    raise ValueError("target must be binary after contract mapping")
train_indices, holdout_indices, outer_receipt = outer_split("stratified_holdout", y, TEST_FRACTION, SEED)
X_train, X_hold = X.iloc[train_indices], X.iloc[holdout_indices]
y_train, y_hold = y.iloc[train_indices], y.iloc[holdout_indices]
comparison = run_multi_model_comparison(X_train, y_train, X_hold, y_hold, kinds=ALGORITHMS, objective=OBJECTIVE, seed=SEED, n_iter=BUDGET, cv_folds=5, objective_minimum=MINIMUM, cost_matrix=COST_MATRIX, minimum_recall=MIN_RECALL, imbalance_strategy=IMBALANCE)
result = comparison["best_result"]
KIND = comparison["best_kind"]

positive_rate = float(y_hold.mean())
probs = result["calibrated_probabilities"]
preds = [1 if value >= result["operating_threshold"] else 0 for value in probs]
tp = sum(1 for actual, predicted in zip(y_hold.tolist(), preds) if actual == 1 and predicted == 1)
fn = sum(1 for actual, predicted in zip(y_hold.tolist(), preds) if actual == 1 and predicted == 0)
fp = sum(1 for actual, predicted in zip(y_hold.tolist(), preds) if actual == 0 and predicted == 1)
tn = sum(1 for actual, predicted in zip(y_hold.tolist(), preds) if actual == 0 and predicted == 0)
errors_per_1000 = round((fn + fp) / len(y_hold) * 1000)

manifest = build_manifest(
    purpose=PURPOSE,
    conclusion=CONCLUSION,
    identity={{"run_id": "ml-contract", "dataset_id": DATASET_ID, "ontology_snapshot_id": SNAPSHOT_ID, "ontology_sha256": SNAPSHOT_SHA, "contract_sha256": PLAN_SHA, "seed": SEED}},
    objective={{"target": TARGET, "task_kind": "binary_classification", "primary_metric": OBJECTIVE, "positive_class": POSITIVE, "threshold": result["operating_threshold"], "threshold_cost_approved": COST_APPROVED, "cost_matrix": COST_MATRIX, "normalization_denominator": REPORTING_DENOMINATOR}},
    data={{"rows": int(len(work)), "source_rows": int(len(df)), "explained_rows": int(len(X_hold)), "features": len(FEATURES), "train_rows": int(len(X_train)), "holdout_rows": int(len(X_hold)), "split_kind": "stratified_holdout", "minority_rate": positive_rate, "excluded_fields": [{{"name": name, "reason": "未納入模型特徵"}} for name in df.columns if name not in FEATURES and name != TARGET]}},
    process={{"model_family": KIND, "search_budget": BUDGET, "completed_trials": comparison["completed_trials"], "comparison": comparison["comparison"], "fit_counts": comparison["fit_counts"], "fit_count_unit": "successful estimator/calibrator fits; excludes preprocessing; holdout reuses fitted winner", "outer_split": outer_receipt, "cv_receipts": comparison["cv_receipts"], "execution_mode": "sequential", "cv_folds": 5, "preprocessing_fit_scope": "training_only", "calibration_method": "isotonic", "best_params": result["best_params"], "uncertainty": comparison["uncertainty"]}},

    baseline_metrics=comparison["baseline_metrics"],
    selected_metrics=result["metrics"],
    guards={{**result["guards"], "verdict": result["verdict"], "assumptions": {{"selection_holdout_separation": True, "preprocessing_fit_scope_training_only": True, "independence_assumption_verified": False, "causal_identification_established": False, "multiplicity_adjusted_inference": False}}}},
    trials=result["trials"],
    features=[{{"name": item["name"], "importance": item["importance"], "explanation": item["name"] + " 是模型參考的資料；重要不代表因果"}} for item in result["top_features"]],
    limitations=["觀察性資料，不能解讀為因果。", comparison["uncertainty"]["limitations"]["model_selection"], comparison["uncertainty"]["limitations"]["calibration"], comparison["uncertainty"]["limitations"]["holdout"]] + ([] if COST_APPROVED else ["成本矩陣與營運門檻尚未獲業務核准，不能直接部署。"]),
)
manifest["operating_scenarios"] = result["operating_scenarios"]
manifest["model_comparison"] = [{{**row, "is_best": row["kind"] == KIND}} for row in comparison["comparison"]]
render_assets(manifest, Path("/tmp/ml-presentation"), y_true=y_hold.tolist(), probabilities=probs, emit_figure=emit, frame=work[FEATURES + [TARGET]], target=TARGET, fields_view=FIELDS_VIEW, evaluation_frame=X_hold, target_values=y.tolist())
render_model_comparison(manifest, Path("/tmp/ml-presentation"), emit_figure=emit)
PLOTLY_EVALUATION_FRAME = X_hold
PLOTLY_TARGET_VALUES = y.tolist()
plotly_figures = build_plotly_figures(manifest, y_true=y_hold.tolist(), probabilities=probs, frame=work[FEATURES + [TARGET]], target=TARGET, fields_view=PLOTLY_FIELDS_VIEW, evaluation_frame=PLOTLY_EVALUATION_FRAME, target_values=PLOTLY_TARGET_VALUES)
for plotly_name, plotly_figure in plotly_figures.items():
    emit(plotly_figure, name=f"ml-plotly-{{plotly_name}}.json")
emit({{"source_rows": int(len(df)), "eligible_rows": int(len(work)), "train_rows": int(len(X_train)), "test_rows": int(len(X_hold)), "explained_rows": int(len(X_hold)), "target": TARGET, "plan_sha256": PLAN_SHA}}, name="dataset-summary.json")

'''

    shap_block = """
if KIND in ("catboost", "gradient_boosting", "random_forest_shap", "xgboost"):
    estimator = result["estimator"]
    preprocess = estimator.named_steps["preprocess"]
    model_only = estimator.named_steps["model"]
    X_sample = X_hold
    transformed = preprocess.transform(X_sample)
    if hasattr(transformed, "toarray"):
        transformed = transformed.toarray()
    shap_model = model_only.model_ if KIND == "catboost" else model_only
    raw_shap = shap.TreeExplainer(shap_model).shap_values(transformed)
    if isinstance(raw_shap, list):
        raw_shap = raw_shap[-1]
    shap_values = np.asarray(raw_shap)
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, -1]
    transformed_names = [str(name).split("__", 1)[-1] for name in preprocess.get_feature_names_out()]
    shap_by_column = {}
    for column_index, column_name in enumerate(transformed_names):
        target_column = next((f for f in sorted(FEATURES, key=len, reverse=True) if column_name == f or column_name.startswith(f + "_")), column_name)
        if target_column in shap_by_column:
            shap_by_column[target_column] = shap_by_column[target_column] + shap_values[:, column_index]
        else:
            shap_by_column[target_column] = shap_values[:, column_index].copy()
    render_shap_summary(manifest, shap_values, transformed_names, transformed, Path("/tmp/ml-presentation"), emit_figure=emit)
    shap_figures = build_plotly_figures(manifest, y_true=y_hold.tolist(), probabilities=probs, frame=work[FEATURES + [TARGET]], target=TARGET, shap_values=shap_values, feature_names=transformed_names, sample_values=transformed, fields_view=FIELDS_VIEW, evaluation_frame=X_hold, target_values=y.tolist())
    for plotly_name, plotly_figure in shap_figures.items():
        if plotly_name not in plotly_figures:
            emit(plotly_figure, name=f"ml-plotly-{{plotly_name}}.json")
    plotly_figures.update(shap_figures)
emit(build_report_source(manifest, plotly_names=set(plotly_figures)), name="report-source.json")
emit(manifest, name="ml-presentation.json")
"""
    return template + shap_block


_TRUSTED_REEXPORT_EXECUTORS = frozenset({"execute_ml_contract", "profile_dataset"})
_TRUSTED_REEXPORT_FORMATS = frozenset({"ask-o11y-ml-presentation-v1", "ask-o11y-ml-regression-v1", data_profile.PROFILE_FORMAT})


def _verify_trusted_lineage(context: dict[str, str], execution_ref: str, provenance: dict[str, Any]) -> None:
    """Bind re-export eligibility to the authorized source packet, not hashes alone."""
    execution_run_id, _ = parse_artifact_ref(execution_ref)
    code_ref = provenance.get("code_ref")
    if not isinstance(code_ref, str):
        raise WorkflowContractError("trusted execution provenance has no source code artifact")
    code_run_id, code_parts = parse_artifact_ref(code_ref)
    if code_run_id != execution_run_id or code_parts != ("sandbox-code",):
        raise WorkflowContractError("trusted execution code lineage is not paired")
    code = ARTIFACTS.read_json(context, code_ref)
    if not isinstance(code, dict) or not isinstance(code.get("source"), str):
        raise WorkflowContractError("trusted execution code artifact is invalid")
    code_digest = hashlib.sha256(code["source"].encode("utf-8")).hexdigest()
    if code_digest != provenance.get("code_sha256") or code.get("sha256") != provenance.get("code_sha256"):
        raise WorkflowContractError("trusted execution code lineage does not match")

    frame_ref = provenance.get("input_frame_ref")
    if not isinstance(frame_ref, str):
        raise WorkflowContractError("trusted execution provenance has no valid source frame")
    frame_run_id, frame = read_authorized_frame(context, frame_ref)
    frame_digest = hashlib.sha256(json.dumps(frame, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if frame_digest != provenance.get("input_frame_sha256"):
        raise WorkflowContractError("trusted execution frame lineage does not match")
    field_names, _ = validate_frame(frame)
    plan_ref = f"artifact://{frame_run_id}/query-plan"
    plan = ARTIFACTS.read_json(context, plan_ref)
    if not isinstance(plan, dict):
        raise WorkflowContractError("trusted execution plan lineage is unavailable")
    if plan.get("ontology") is not None:
        verify_plan_for_context(context, plan)
    elif not isinstance(plan.get("plan_sha256"), str):
        raise WorkflowContractError("trusted execution plan lineage is incomplete")
    plan_digest = hashlib.sha256(json.dumps({key: value for key, value in plan.items() if key != "plan_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if plan_digest != provenance.get("plan_sha256") or plan.get("plan_sha256") != provenance.get("plan_sha256"):
        raise WorkflowContractError("trusted execution plan lineage does not match")
    read_plan_contract(context, frame_run_id, field_names)
    if provenance.get("business_question") != plan.get("business_question"):
        raise WorkflowContractError("trusted execution question lineage does not match")
    if provenance.get("executor_kind") == "execute_ml_contract" and provenance.get("analysis_contract") != plan.get("analysis_contract"):
        raise WorkflowContractError("trusted execution analysis lineage does not match")


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


def _is_report_source_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    value = _result_json(result)
    return result.get("display_name") == "report-source.json" or (value or {}).get("format") == ml_report_contract.REPORT_SOURCE_FORMAT


def _is_reexport_source_result(result: Any) -> bool:
    value = _result_json(result)
    return _is_report_source_result(result) or (value or {}).get("format") in _TRUSTED_REEXPORT_FORMATS


def _canonical_results_digest(results: list[Any], *, include_report_source: bool) -> str:
    selected = [result for result in results if include_report_source or not _is_reexport_source_result(result)]
    return hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _verify_reexport_parent(
    context: dict[str, str], execution_ref: str, execution: dict[str, Any], provenance_ref: str, provenance: dict[str, Any]
) -> dict[str, Any] | None:
    """Authenticate the one permitted source execution for a trusted re-export."""
    if "reexport_of" in execution or "source_provenance_ref" in execution:
        raise WorkflowContractError("trusted re-export chains are unsupported")
    source_run_id, source_parts = parse_artifact_ref(execution_ref)
    if source_parts != ("sandbox-execution",) or parse_artifact_ref(provenance_ref)[0] != source_run_id:
        raise WorkflowContractError("trusted re-export source provenance is not paired")
    source_provenance = ARTIFACTS.read_json(context, provenance_ref)
    if not isinstance(source_provenance, dict) or source_provenance.get("report_manifest_ref"):
        raise WorkflowContractError("trusted re-export source provenance is not a fresh pre-manifest receipt")
    if "reexport_of" in source_provenance or "source_provenance_ref" in source_provenance:
        raise WorkflowContractError("trusted re-export chains are unsupported")
    if source_provenance.get("computation_status") != "succeeded":
        raise WorkflowContractError("trusted re-export source computation is not verified")
    if source_provenance.get("report_status") not in {"rejected", "not_attempted"}:
        raise WorkflowContractError("trusted re-export source report state is incompatible")
    _verify_trusted_lineage(context, execution_ref, source_provenance)
    source_results = execution.get("results")
    if not isinstance(source_results, list) or not source_results:
        raise WorkflowContractError("trusted re-export source results are unavailable")
    return {"provenance": source_provenance, "source_results_digest": _canonical_results_digest(source_results, include_report_source=False)}


def _canonical_provenance_digest(provenance: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _repair_operation_id(context: dict[str, str], inputs: dict[str, Any]) -> str:
    actor = {key: str(context.get(key) or "") for key in ("org_id", "user_id", "session_id")}
    encoded = json.dumps([actor, "repair_generic_report", inputs], sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _verify_generic_repair_source(
    context: dict[str, str], execution_ref: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Verify the one same-session generic receipt eligible for repair."""
    run_id, parts = parse_artifact_ref(execution_ref)
    if parts != ("sandbox-execution",):
        raise WorkflowContractError("execution_ref must reference sandbox-execution")
    provenance_ref = f"artifact://{run_id}/sandbox-provenance"
    try:
        metadata = json.loads(ARTIFACTS._metadata_path(run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactAuthError("generic repair source metadata is unavailable") from exc
    if any(str(metadata.get(key, "")) != str(context.get(key, "")) for key in ("org_id", "user_id", "session_id")):
        raise ArtifactAuthError("generic repair requires the original source session")
    execution = ARTIFACTS.read_json(context, execution_ref)
    provenance = ARTIFACTS.read_json(context, provenance_ref)
    if not isinstance(execution, dict) or "error" not in execution or execution.get("error") is not None or not isinstance(execution.get("results"), list) or not execution["results"]:
        raise WorkflowContractError("generic repair requires a concrete retained execution")
    if not isinstance(provenance, dict) or provenance.get("report_manifest_ref") not in (None, ""):
        raise WorkflowContractError("generic repair requires a pre-manifest receipt")
    if provenance.get("computation_status") != "succeeded" or provenance.get("report_status") != "rejected":
        raise WorkflowContractError("generic repair requires succeeded computation and rejected report status")
    if provenance.get("executor_kind") not in _GENERIC_REPORT_STEPS or provenance.get("trusted_ml_contract") is not False:
        raise WorkflowContractError("only untrusted generic Python receipts can be repaired")
    if any(key in execution or key in provenance for key in ("reexport_of", "source_provenance_ref", "generic_repair_of")):
        raise WorkflowContractError("generic repair chains are unsupported")
    question = provenance.get("business_question")
    if not isinstance(question, str) or not question.strip() or len(question.encode("utf-8")) > 2048 or any(token in question.lower() for token in ("<", ">", "http://", "https://", "javascript:")):
        raise WorkflowContractError("generic repair requires a valid retained business question")
    validity = provenance.get("validity")
    audit = execution.get("input_audit")
    if not isinstance(validity, dict) or audit != validity:
        raise WorkflowContractError("generic repair validity evidence does not match the retained input audit")
    source_results = [result for result in execution["results"] if _is_report_source_result(result)]
    captured_digest = provenance.get("captured_results_sha256")
    actual_digest = _canonical_results_digest(execution["results"], include_report_source=False)
    if not isinstance(captured_digest, str) or captured_digest != actual_digest:
        raise WorkflowContractError("generic repair captured-results digest is missing or invalid")
    if len(source_results) != 1:
        raise WorkflowContractError("generic repair requires exactly one host-created report source")
    host_source_digest = provenance.get("host_report_source_sha256")
    source_value = _result_json(source_results[0])
    if not isinstance(source_value, dict) or source_value.get("format") != ml_report_contract.REPORT_SOURCE_FORMAT or not isinstance(host_source_digest, str):
        raise WorkflowContractError("generic repair rejects reserved or unverified report sources")
    actual_source_digest = hashlib.sha256(json.dumps(source_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if host_source_digest != actual_source_digest:
        raise WorkflowContractError("generic repair host report source digest is invalid")
    expected_source = _generic_report_source(execution, validity, purpose=question.strip())
    if expected_source is None or source_value != expected_source:
        raise WorkflowContractError("generic repair report source is not host-generated from retained evidence")
    _verify_trusted_lineage(context, execution_ref, provenance)
    presentation_mode = provenance.get("presentation_mode")
    if presentation_mode not in PRESENTATION_MODES:
        raise WorkflowContractError("generic repair presentation mode is invalid")
    required_mime = "application/vnd.plotly.v1+json" if presentation_mode == "plotly" else "image/png"
    if not any(isinstance(result, dict) and isinstance(result.get("mime"), dict) and required_mime in result["mime"] for result in execution["results"]):
        raise WorkflowContractError("generic repair presentation output is missing")
    return execution, provenance, provenance_ref, question.strip()


def _report_recovery_evidence(frame_ref: str, *, correction: bool) -> dict[str, Any]:
    evidence = {
        "frame_ref": frame_ref, "computation_status": "succeeded", "report_status": "rejected",
        "correction_required": correction, "automatic_retry": False,
        "error_code": "report_contract_rejected" if correction else "report_persistence_failed",
        "recovery_action": "correct_python_same_frame" if correction else "repair_generic_report",
    }
    if correction:
        evidence["plotly_capability"] = ml_plotly_contract.capability_summary()
    return evidence


def _validated_generic_repair_candidate(execution: dict[str, Any], provenance: dict[str, Any], question: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = _generic_report_source(execution, provenance["validity"], purpose=question)
    if source is None:
        raise WorkflowContractError("generic repair requires at least one renderable output")
    try:
        captured_results = json.loads(json.dumps(execution["results"], ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("retained generic results are not valid JSON") from exc
    clean_results = [result for result in captured_results if not _is_report_source_result(result)]
    clean_results.append({
        "text": None, "timestamp": 0,
        "mime": {"application/json": json.dumps(source, ensure_ascii=False, separators=(",", ":"))},
        "display_name": "report-source.json",
    })
    if len(json.dumps(clean_results, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise WorkflowContractError("generic repair exceeds the captured output limit")
    # Validate before reserving an effect. A contract rejection is deterministic,
    # not an indeterminate persistence outcome.
    candidate = ml_report_contract.normalize_report_manifest(execution_ref="artifact://generic-repair-preflight/sandbox-execution", results=clean_results)
    if ml_report_contract.presentation_errors(candidate):
        raise WorkflowContractError("retained figures still cannot be presented; generic repair cannot correct Python or claim a complete report")
    return source, clean_results


def _repair_generic_report(context: dict[str, str], execution_ref: str, operation_id: str) -> dict[str, Any]:
    execution, provenance, provenance_ref, question = _verify_generic_repair_source(context, execution_ref)
    source, clean_results = _validated_generic_repair_candidate(execution, provenance, question)
    output_run_id: str | None = None
    fresh_execution_ref: str | None = None
    report_manifest_ref: str | None = None
    try:
        output_run_id = ARTIFACTS.create_run(context)
        fresh_execution_ref = f"artifact://{output_run_id}/sandbox-execution"
        fresh_execution = {
            "results": clean_results, "error": None, "generic_repair_of": execution_ref,
            "source_provenance_ref": provenance_ref,
            "generic_repair_source_results_sha256": provenance["captured_results_sha256"],
        }
        ARTIFACTS.write_json(context, output_run_id, "sandbox-execution", fresh_execution)
        # Re-normalize with the actual fresh ref; the source/result bytes remain unchanged.
        report_manifest = ml_report_contract.normalize_report_manifest(execution_ref=fresh_execution_ref, results=clean_results)
        report_manifest_ref = ARTIFACTS.write_json(context, output_run_id, "report-manifest", report_manifest)
        fresh_provenance = dict(provenance)
        fresh_provenance.update({
            "report_manifest_ref": report_manifest_ref, "computation_status": "succeeded", "report_status": "accepted",
            "generic_repair_of": execution_ref, "source_provenance_ref": provenance_ref,
            "generic_repair_kind": "host_report_manifest", "generic_repair_source_results_sha256": provenance["captured_results_sha256"],
            "generic_repair_source_provenance_sha256": _canonical_provenance_digest(provenance),
            "generic_repair_report_source_sha256": hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        })
        fresh_provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", fresh_provenance)
    except Exception:
        # The run reservation is already durable. Keep its identity and any
        # paired refs visible, but never claim a partial repair succeeded or
        # retry persistence (which could duplicate an effect).
        return error_response(
            step="repair_generic_report", error="generic repair persistence is indeterminate; reconcile the operation before retrying",
            recoverable=False,
            instruction="Keep the original generic receipt; reconcile this operation and do not rerun computation or repeat repair persistence.",
            evidence={
                "operation_id": operation_id, "effect_outcome": "indeterminate", "automatic_retry": False,
                "source_execution_ref": execution_ref, "source_provenance_ref": provenance_ref,
                "execution_ref": fresh_execution_ref, "report_manifest_ref": report_manifest_ref,
                "computation_status": "succeeded", "report_status": "indeterminate", "trusted_ml_contract": False,
            },
        )
    return success_response(
        step="repair_generic_report", run_id=output_run_id,
        refs={"execution_ref": fresh_execution_ref, "provenance_ref": fresh_provenance_ref, "report_manifest_ref": report_manifest_ref},
        instruction="Use the fresh report_manifest_ref for prepare_ml_report; retained generic evidence remains untrusted and no Python was rerun.",
        evidence={"source_execution_ref": execution_ref, "source_provenance_ref": provenance_ref, "source_results_sha256": provenance["captured_results_sha256"], "report_manifest_ref": report_manifest_ref, "trusted_ml_contract": False},
    )


def repair_generic_report(args: dict[str, Any]) -> dict[str, Any]:
    step = "repair_generic_report"
    try:
        unexpected = sorted(set(args) - {"execution_ref", "context", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        execution_ref = args.get("execution_ref")
        if not isinstance(execution_ref, str):
            raise WorkflowContractError("execution_ref is required")
        context = context_from_args(args)
        # Bind replay identity to the authenticated source receipt and its
        # immutable digests, then re-read all artifacts inside the callback.
        execution, provenance, provenance_ref, question = _verify_generic_repair_source(context, execution_ref)
        inputs = {"execution_ref": execution_ref, "source_results_sha256": provenance["captured_results_sha256"], "source_provenance_sha256": _canonical_provenance_digest(provenance)}
        operation_id = _repair_operation_id(context, inputs)
        if (ARTIFACTS.root / "operations" / operation_id).exists():
            # A changed validator must not hide an older unresolved operation.
            reconciliation_status = "indeterminate"
            try:
                existing = ARTIFACTS.reconcile_operation(context, operation_id)
                if existing["status"] in {"completed", "failed"}:
                    return existing["result"]
            except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError):
                reconciliation_status = "unavailable"  # Never permission to redispatch.
            return error_response(
                step=step, error="generic repair operation is indeterminate; reconcile it before any correction or retry", recoverable=False,
                instruction="Reconcile this operation first. Do not rerun computation, repair, or corrected code while its outcome is unknown.",
                evidence={"operation_id": operation_id, "effect_outcome": "indeterminate", "automatic_retry": False,
                          "reconciliation_status": reconciliation_status,
                          "source_execution_ref": execution_ref, "source_provenance_ref": provenance_ref,
                          "frame_ref": provenance["input_frame_ref"], "original_report_error": provenance.get("report_error"),
                          "computation_status": "succeeded", "report_status": "indeterminate", "recovery_action": "reconcile_operation"},
            )
        try:
            _validated_generic_repair_candidate(execution, provenance, question)
        except (ValueError, TypeError, KeyError, WorkflowContractError) as exc:
            return error_response(
                step=step,
                error=f"generic repair cannot accept the retained renderable: {exc}",
                recoverable=False,
                instruction="Do not retry generic repair. Reuse the original frame_ref and execute corrected python_code; do not rerun the query.",
                evidence={"source_execution_ref": execution_ref, "source_provenance_ref": provenance_ref,
                          "original_report_error": provenance.get("report_error"),
                          **_report_recovery_evidence(provenance["input_frame_ref"], correction=True)},
            )
        try:
            return ARTIFACTS.run_once(context, step, inputs, lambda: _repair_generic_report(context, execution_ref, operation_id))
        except Exception:
            return error_response(
                step=step, error="generic repair operation is indeterminate; reconcile the operation before retrying", recoverable=False,
                instruction="Keep the original generic receipt; reconcile this operation and do not rerun computation or repeat repair persistence.",
                evidence={
                    "operation_id": operation_id, "effect_outcome": "indeterminate", "automatic_retry": False,
                    "source_execution_ref": execution_ref, "source_provenance_ref": provenance_ref,
                    "computation_status": "succeeded", "report_status": "indeterminate", "trusted_ml_contract": False,
                },
            )
    except (ArtifactAuthError, PermissionError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; retain the original generic receipt and do not rerun computation or fabricate a report source.")


def _reexport_trusted_report(context: dict[str, str], execution_ref: str) -> dict[str, Any]:
    run_id, parts = parse_artifact_ref(execution_ref)
    if parts != ("sandbox-execution",):
        raise WorkflowContractError("execution_ref must reference sandbox-execution")
    execution = ARTIFACTS.read_json(context, execution_ref)
    provenance_ref = f"artifact://{run_id}/sandbox-provenance"
    provenance = ARTIFACTS.read_json(context, provenance_ref)
    if not isinstance(provenance, dict) or provenance.get("report_manifest_ref"):
        raise WorkflowContractError("execution already has a report_manifest_ref or host provenance is invalid")
    executor_kind = provenance.get("executor_kind")
    if executor_kind not in _TRUSTED_REEXPORT_EXECUTORS:
        raise WorkflowContractError("generic Python cannot be re-exported as a trusted report")
    if not isinstance(execution, dict) or "error" not in execution or execution.get("error") is not None or not isinstance(execution.get("results"), list) or not execution["results"]:
        raise WorkflowContractError("trusted re-export requires a successful execution")
    if not isinstance(provenance.get("trusted_ml_contract"), bool) or provenance["trusted_ml_contract"] != (executor_kind == "execute_ml_contract"):
        raise WorkflowContractError("trusted execution provenance is invalid")
    computation_status = provenance.get("computation_status")
    if computation_status is not None and computation_status != "succeeded":
        raise WorkflowContractError("trusted re-export requires an explicitly succeeded computation")
    report_status = provenance.get("report_status")
    if report_status is not None and report_status not in {"rejected", "not_attempted"}:
        raise WorkflowContractError("trusted re-export requires a compatible rejected report state")
    source_receipt = _verify_reexport_parent(context, execution_ref, execution, provenance_ref, provenance)
    legacy_manifest: dict[str, Any] | None = None
    for result in execution["results"]:
        candidate = _result_json(result)
        if candidate is not None and candidate.get("format") in _TRUSTED_REEXPORT_FORMATS:
            legacy_manifest = candidate
            break
    if legacy_manifest is None:
        raise WorkflowContractError("no trusted pre-manifest report output is available")
    plotly_names = {
        Path(str(result.get("display_name"))).stem.removeprefix("ml-plotly-")
        for result in execution["results"]
        if isinstance(result, dict) and isinstance(result.get("display_name"), str) and (result.get("mime") or {}).get("application/vnd.plotly.v1+json") is not None
    }
    if legacy_manifest.get("format") == data_profile.PROFILE_FORMAT:
        source = data_profile.build_profile_report_source(legacy_manifest, plotly_names=plotly_names)
    else:
        source = ml_presentation.build_report_source(legacy_manifest, plotly_names=plotly_names)
    clean_results = [
        result for result in execution["results"]
        if not _is_report_source_result(result)
    ]
    clean_results.append({"text": None, "timestamp": 0, "mime": {"application/json": json.dumps(source, ensure_ascii=False, separators=(",", ":"))}, "display_name": "report-source.json"})
    encoded = json.dumps(clean_results, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise WorkflowContractError("trusted re-export exceeds the captured output limit")
    if source_receipt is None or _canonical_results_digest(clean_results, include_report_source=False) != source_receipt["source_results_digest"]:
        raise WorkflowContractError("trusted re-export changed retained outputs")
    output_run_id = ARTIFACTS.create_run(context)
    reexport_execution = {
        "results": clean_results, "error": None, "reexport_of": execution_ref, "source_provenance_ref": provenance_ref,
        "reexport_source_results_sha256": source_receipt["source_results_digest"],
    }
    fresh_execution_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-execution", reexport_execution)
    report_manifest = ml_report_contract.normalize_report_manifest(execution_ref=fresh_execution_ref, results=clean_results)
    report_manifest_ref = ARTIFACTS.write_json(context, output_run_id, "report-manifest", report_manifest)
    source_payload = json.dumps(legacy_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fresh_report_source = next((candidate for candidate in (_result_json(result) for result in clean_results) if candidate and candidate.get("format") == ml_report_contract.REPORT_SOURCE_FORMAT), None)
    if fresh_report_source is None:
        raise WorkflowContractError("trusted re-export did not produce a canonical report source")
    fresh_provenance = dict(provenance)
    fresh_provenance.update({
        "report_manifest_ref": report_manifest_ref, "computation_status": "succeeded", "report_status": "accepted",
        "reexport_of": execution_ref, "source_provenance_ref": provenance_ref, "reexport_kind": "trusted_host_report_manifest",
        "reexport_source_results_sha256": source_receipt["source_results_digest"],
        "reexport_source_manifest_sha256": hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
        "reexport_source_format": legacy_manifest.get("format"),
        "reexport_report_source_sha256": hashlib.sha256(json.dumps(fresh_report_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    })
    fresh_provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", fresh_provenance)
    return success_response(
        step="reexport_trusted_report", run_id=output_run_id,
        refs={"execution_ref": fresh_execution_ref, "report_manifest_ref": report_manifest_ref, "provenance_ref": fresh_provenance_ref},
        instruction="Use the fresh report_manifest_ref for prepare_ml_report; do not use the source execution's output indexes.",
        evidence={"source_execution_ref": execution_ref, "source_provenance_ref": provenance_ref, "executor_kind": executor_kind, "report_manifest_ref": report_manifest_ref},
    )


def reexport_trusted_report(args: dict[str, Any]) -> dict[str, Any]:
    step = "reexport_trusted_report"
    try:
        unexpected = sorted(set(args) - {"execution_ref", "context", "_server_context"})
        if unexpected:
            raise WorkflowContractError("unsupported tool arguments: " + ", ".join(unexpected))
        execution_ref = args.get("execution_ref")
        if not isinstance(execution_ref, str):
            raise WorkflowContractError("execution_ref is required")
        context = context_from_args(args)
        return ARTIFACTS.run_once(context, step, {"execution_ref": execution_ref}, lambda: _reexport_trusted_report(context, execution_ref))
    except (ArtifactAuthError, PermissionError, WorkflowContractError, OSError, ValueError, TypeError, KeyError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; only a successful host-trusted pre-manifest execution can be re-exported. Do not rerun analysis or fabricate a report source.")


def profile_dataset(args: dict[str, Any], executor: Callable[[str, str, int], dict[str, Any]] = execute_opensandbox) -> dict[str, Any]:
    step = "profile_dataset"
    unexpected = sorted(set(args) - {"frame_ref", "seed", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Pass only the opaque frame ref and seed.")
    frame_ref = args.get("frame_ref")
    seed = args.get("seed", DEFAULT_SEED)
    if not isinstance(frame_ref, str):
        return error_response(step=step, error="frame_ref is required", recoverable=False, instruction="Grafana Query must return a frame_ref first.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        return error_response(step=step, error="seed must be an integer from 0 to 4294967295", recoverable=False, instruction="Provide a valid deterministic seed.")
    try:
        context = context_from_args(args)
        source_run_id, parts = parse_artifact_ref(frame_ref)
        if parts != ("grafana-frame",):
            raise WorkflowContractError("frame_ref must reference a grafana-frame artifact")
        plan_ref = f"artifact://{source_run_id}/query-plan"
        plan = ARTIFACTS.read_json(context, plan_ref)
        verify_plan_for_context(context, plan)
        if not isinstance(plan, dict):
            raise WorkflowContractError("query plan is invalid")
        template = compose_profile_template(plan, seed)
        ast.parse(template)
    except (PermissionError, WorkflowContractError, ValueError, TypeError, KeyError, OSError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="The authorized profile plan is invalid or unavailable.")
    return execute_python_analysis({"frame_ref": frame_ref, "python_code": template, "seed": seed, "_server_context": context}, executor=executor, step=step)


def execute_ml_contract(args: dict[str, Any], executor: Callable[[str, str, int], dict[str, Any]] = execute_opensandbox) -> dict[str, Any]:
    step = "execute_ml_contract"
    unexpected = sorted(set(args) - {"frame_ref", "contract_ref", "seed", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only the opaque frame ref, plan contract ref, and seed.")
    frame_ref = args.get("frame_ref")
    contract_ref = args.get("contract_ref")
    seed = args.get("seed", DEFAULT_SEED)
    if not isinstance(frame_ref, str) or not isinstance(contract_ref, str):
        return error_response(step=step, error="frame_ref and contract_ref are required", recoverable=False, instruction="Stop; Grafana Query must return a frame_ref and the Planner a plan_ref first.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        return error_response(step=step, error="seed must be an integer from 0 to 4294967295", recoverable=False, instruction="Stop; provide a valid deterministic seed.")
    try:
        context = context_from_args(args)
        source_run_id, parts = parse_artifact_ref(contract_ref)
        if parts != ("query-plan",):
            raise WorkflowContractError("contract_ref must reference a query-plan")
        plan = ARTIFACTS.read_json(context, contract_ref)
        verify_plan_for_context(context, plan)
        frame_run_id, frame_parts = parse_artifact_ref(frame_ref)
        if frame_parts != ("grafana-frame",) or frame_run_id != source_run_id:
            raise WorkflowContractError("frame_ref and contract_ref must belong to the same query plan")
        contract = plan.get("analysis_contract")
        if not isinstance(contract, dict):
            raise WorkflowContractError("query plan has no analysis contract")
        planned_seed = contract.get("seed", (contract.get("split") or {}).get("seed", DEFAULT_SEED))
        if "seed" not in args:
            seed = planned_seed
        if isinstance(seed, bool) or not isinstance(seed, int) or seed != planned_seed or not 0 <= seed <= 4294967295:
            raise WorkflowContractError("execution seed must match the approved analysis contract")
        analysis_input = plan.get("analysis_input_contract") or {}
        validate_ml_execution_contract(analysis_input, contract)
        template = compose_ml_template(plan, contract, seed)
        ast.parse(template)
    except (PermissionError, WorkflowContractError, ValueError, TypeError, KeyError, OSError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; the analysis contract is invalid or not authorized.")
    return execute_python_analysis({"frame_ref": frame_ref, "python_code": template, "seed": seed, "_server_context": context}, executor=executor, step=step)


def execute_python_analysis(
    args: dict[str, Any],
    executor: Callable[[str, str, int], dict[str, Any]] = execute_opensandbox,
    *,
    step: str = "execute_python_analysis",
    parent_provenance_ref: str | None = None,
) -> dict[str, Any]:
    try:
        presentation_mode = args.get("presentation_mode", DEFAULT_PRESENTATION_MODE)
        if presentation_mode not in PRESENTATION_MODES:
            return error_response(step=step, error=f"presentation_mode must be one of: {', '.join(PRESENTATION_MODES)}", recoverable=False, instruction="Stop; use the default Plotly presentation or explicitly select image for a static PNG.")
        normalized_args = {**args, "presentation_mode": presentation_mode}
        context = context_from_args(normalized_args)
        inputs = {key: value for key, value in normalized_args.items() if key not in {"context", "_server_context"}}
        inputs.setdefault("seed", DEFAULT_SEED)
        return ARTIFACTS.run_once(context, step, inputs, lambda: _execute_python_analysis(normalized_args, executor, step=step, parent_provenance_ref=parent_provenance_ref))
    except (PermissionError, WorkflowContractError, OSError, TypeError, ValueError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Do not repeat an indeterminate operation; inspect its existing execution evidence.")


def _execute_python_analysis(
    args: dict[str, Any],
    executor: Callable[[str, str, int], dict[str, Any]],
    *,
    step: str,
    parent_provenance_ref: str | None,
) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"frame_ref", "python_code", "seed", "presentation_mode", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only the declared opaque frame ref, Python source, and seed.")
    frame_ref = args.get("frame_ref")
    python_code = args.get("python_code")
    seed = args.get("seed", DEFAULT_SEED)
    presentation_mode = args.get("presentation_mode", DEFAULT_PRESENTATION_MODE)
    if presentation_mode not in PRESENTATION_MODES:
        return error_response(step=step, error=f"presentation_mode must be one of: {', '.join(PRESENTATION_MODES)}", recoverable=False, instruction="Stop; use the default Plotly presentation or explicitly select image for a static PNG.")
    if not isinstance(frame_ref, str):
        return error_response(step=step, error="frame_ref is required", recoverable=False, instruction="Stop; Grafana Query must return a frame_ref first.")
    if not isinstance(python_code, str) or not python_code.strip():
        return error_response(step=step, error="python_code is required", recoverable=False, instruction="Stop; provide the confirmed Python analysis source.")
    code_bytes = python_code.encode("utf-8")
    if len(code_bytes) > MAX_CODE_BYTES:
        return error_response(step=step, error=f"python_code exceeds {MAX_CODE_BYTES} bytes", recoverable=False, instruction="Stop; reduce the code to the requested analysis only.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        return error_response(step=step, error="seed must be an integer from 0 to 4294967295", recoverable=False, instruction="Stop; provide a valid deterministic seed.")
    try:
        context = context_from_args(args)
        source_run_id, frame = read_authorized_frame(context, frame_ref)
        field_names, row_count = validate_frame(frame)
        validity_rules, semantic_contract = read_plan_contract(context, source_run_id, field_names)
        frame_bundle_json = json.dumps({"frame": frame, "validity_rules": validity_rules, "semantic_contract": semantic_contract}, ensure_ascii=False, separators=(",", ":"))
    except (PermissionError, WorkflowContractError, ValueError, TypeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; the input frame is invalid or not authorized.")
    if len(frame_bundle_json.encode("utf-8")) > MAX_INPUT_BUNDLE_BYTES:
        return error_response(step=step, error=f"authorized input exceeds {MAX_INPUT_BUNDLE_BYTES} bytes", recoverable=False, instruction="The complete authorized dataset exceeds the configured resource limit; do not sample, truncate, or silently change its scope.")
    code_sha256 = hashlib.sha256(code_bytes).hexdigest()
    try:
        execution = executor(frame_bundle_json, python_code, seed)
    except Exception as exc:
        return error_response(step=step, error=f"sandbox execution outcome unknown: {type(exc).__name__}", recoverable=False, instruction="Reconcile the existing execution before retrying; never rerun it blindly or execute on the MCP host.", evidence={"effect_outcome": "indeterminate"})
    encoded_execution = json.dumps(execution, ensure_ascii=False).encode("utf-8")
    if len(encoded_execution) > MAX_OUTPUT_BYTES:
        return error_response(step=step, error=f"sandbox output exceeds {MAX_OUTPUT_BYTES} bytes", recoverable=False, instruction="Stop; request smaller displayed outputs.")
    validity = execution.get("input_audit")
    audit_counts = [validity.get(key) for key in ("input_rows", "valid_rows", "excluded_rows")] if isinstance(validity, dict) else []
    if (
        not isinstance(validity, dict)
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in audit_counts)
        or validity.get("input_rows") != row_count
        or validity.get("valid_rows", 0) + validity.get("excluded_rows", 0) != row_count
        or validity.get("rules") != validity_rules
    ):
        return error_response(step=step, error="sandbox execution returned an invalid trusted input audit", recoverable=False, instruction="Stop; do not trust outputs without host-verified validity evidence.")
    captured_results_sha256 = _canonical_results_digest(execution.get("results", []), include_report_source=True)
    preflight_execution, report_status, report_error = _preflight_report(
        execution, validity, presentation_mode, step, purpose=semantic_contract.get("business_question"),
    )
    host_source_retained = preflight_execution is not execution and any(
        _is_report_source_result(result) for result in preflight_execution.get("results", [])
    )
    execution = preflight_execution
    settings = runtime_settings() if executor is execute_opensandbox else {"image": "self-check", "runtime_class": "fake"}
    summary = output_summary(execution)
    summary["presentation_mode"] = presentation_mode
    output_run_id = ARTIFACTS.create_run(context)
    code_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-code", {"sha256": code_sha256, "source": python_code})
    provenance = {
        "runtime": "opensandbox",
        "runtime_class": settings["runtime_class"],
        "server_config_sha256": settings.get("server_config_sha256"),
        "image": settings["image"],
        "code_sha256": code_sha256,
        "code_ref": code_ref,
        "input_frame_ref": frame_ref,
        "executor_kind": step,
        "presentation_mode": presentation_mode,
        "trusted_ml_contract": step == "execute_ml_contract",
        "input_frame_sha256": hashlib.sha256(json.dumps(frame, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "input_fields": field_names,
        "seed": seed,
        "network": "deny",
        "resource": sandbox_policy()["resource"],
        "limits": {
            "timeout_seconds": sandbox_policy()["timeout_seconds"],
            "source_bytes": MAX_CODE_BYTES,
            "rpc_body_bytes": MAX_RPC_BODY_BYTES,
            "input_bundle_bytes": MAX_INPUT_BUNDLE_BYTES,
            "captured_execution_bytes": MAX_OUTPUT_BYTES,
            "stdout_stderr_bytes_each": MAX_LOG_BYTES,
            "inline_result_bytes": MAX_INLINE_RESULT_BYTES,
            "output_fields": MAX_OUTPUT_FIELDS,
            "signed_download_bytes": artifact_assets.MAX_ASSET_BYTES,
        },
        "validity": validity,
        "ontology": semantic_contract.get("ontology"),
        "analysis_contract": semantic_contract.get("analysis_contract"),
        "business_question": semantic_contract.get("business_question"),
        "planned_objective": semantic_contract.get("planned_objective"),
        "plan_sha256": semantic_contract.get("plan_sha256"),
        "output_summary": summary,
        "parent_provenance_ref": parent_provenance_ref,
        "captured_results_sha256": captured_results_sha256,
        "host_report_source_sha256": (
            hashlib.sha256(json.dumps(_result_json(next(result for result in execution.get("results", []) if _is_report_source_result(result))), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if host_source_retained else None
        ),
    }
    execution_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-execution", execution)
    execution_error = execution.get("error")
    provenance["computation_status"] = "failed" if execution_error else "succeeded"
    provenance["report_status"] = "not_attempted" if execution_error else report_status
    summary["computation_status"] = provenance["computation_status"]
    summary["report_status"] = provenance["report_status"]
    if report_error is not None:
        provenance["report_manifest_ref"] = None
        provenance["report_error"] = report_error
        provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", provenance)
        trusted_repair = step in _TRUSTED_REEXPORT_EXECUTORS and report_status == "rejected"
        code_correction = step in _GENERIC_REPORT_STEPS and report_status == "rejected"
        instruction = (
            "Keep the successful execution and provenance; call reexport_trusted_report with the execution_ref. It may rebuild only the host report manifest without rerunning computation."
            if trusted_repair else
            "Keep the successful execution and provenance; do not call repair_generic_report. Reuse the same frame_ref and call execute_python_analysis again with corrected python_code; do not rerun the query."
            if code_correction else
            "Keep the successful execution and provenance; no report repair is authorized for this outcome. Do not rerun or fabricate a report source."
        )
        return error_response(
            step=step, error=report_error, recoverable=trusted_repair or code_correction,
            instruction=instruction,
            evidence={"execution_ref": execution_ref, "provenance_ref": provenance_ref,
                      **_report_recovery_evidence(frame_ref, correction=True),
                      "correction_required": code_correction,
                      "recovery_action": "reexport_trusted_report" if trusted_repair else "correct_python_same_frame" if code_correction else "stop"},
        )
    report_manifest_ref = None
    report_manifest: dict[str, Any] = {}
    if not execution_error:
        source_present = False
        for result in execution.get("results", []):
            display_name = result.get("display_name") if isinstance(result, dict) else None
            payload = result.get("mime", {}).get("application/json") if isinstance(result, dict) and isinstance(result.get("mime"), dict) else None
            if display_name == "report-source.json":
                source_present = True
                break
            if isinstance(payload, str):
                try:
                    value = json.loads(payload)
                except json.JSONDecodeError:
                    value = None
                if isinstance(value, dict) and value.get("format") == ml_report_contract.REPORT_SOURCE_FORMAT:
                    source_present = True
                    break
        if source_present:
            manifest_validated = False
            try:
                report_manifest = ml_report_contract.normalize_report_manifest(execution_ref=execution_ref, results=execution.get("results", []))
                manifest_validated = True
                report_manifest_ref = ARTIFACTS.write_json(context, output_run_id, "report-manifest", report_manifest)
            # pi-lens-ignore: no-boolean-in-except, ast-grep:no-boolean-in-except
            except (ValueError, TypeError, KeyError, OSError) as exc:
                provenance["report_manifest_ref"] = None
                provenance["report_status"] = "rejected"
                provenance["report_error"] = str(exc)
                provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", provenance)
                summary["report_status"] = "rejected"
                trusted_repair = step in _TRUSTED_REEXPORT_EXECUTORS
                deterministic_rejection = not manifest_validated
                generic_repair = step in _GENERIC_REPORT_STEPS and not deterministic_rejection
                instruction = (
                    "Keep the successful execution and provenance; call reexport_trusted_report with the execution_ref. It may rebuild only the host report manifest without rerunning computation."
                    if trusted_repair else
                    "Keep the successful execution and provenance; do not call repair_generic_report. Reuse the same frame_ref and call execute_python_analysis again with corrected python_code; do not rerun the query."
                    if deterministic_rejection else
                    "Keep the successful execution and provenance; call repair_generic_report with the execution_ref only after this manifest validation succeeded; the failure may be a persistence failure."
                    if generic_repair else
                    "Keep the successful execution and provenance; no report repair is authorized for this outcome. Do not rerun or fabricate a report source."
                )
                return error_response(step=step, error=f"report manifest rejected: {exc}", recoverable=trusted_repair or deterministic_rejection or generic_repair, instruction=instruction, evidence={"execution_ref": execution_ref, "provenance_ref": provenance_ref,
                    **_report_recovery_evidence(frame_ref, correction=deterministic_rejection),
                    "correction_required": deterministic_rejection and not trusted_repair,
                    "recovery_action": "reexport_trusted_report" if trusted_repair else "correct_python_same_frame" if deterministic_rejection else "repair_generic_report" if generic_repair else "stop"})
    provenance["report_manifest_ref"] = report_manifest_ref
    figure_errors = ml_report_contract.presentation_errors(report_manifest) if report_manifest_ref else []
    provenance["report_status"] = "partial" if figure_errors else "accepted" if report_manifest_ref else "not_requested"
    summary["report_status"] = provenance["report_status"]
    summary["presentation_errors"] = figure_errors
    summary["assets"] = output_asset_summary(execution, execution_ref)
    summary["downloads"] = output_download_summary(execution, execution_ref, context)
    try:
        derived_refs, derived_summary = ({}, None) if execution_error else persist_derived_data(context, execution, output_run_id)
    except (OSError, PermissionError, ValueError, WorkflowContractError) as exc:
        return error_response(step=step, error=f"derived frame rejected: {exc}", recoverable=False, instruction="Stop; the Sandbox output did not satisfy the derived-data contract.", evidence={"execution_ref": execution_ref})
    if derived_summary is not None:
        summary["derived_data"] = derived_summary
    provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", provenance)
    refs = {"execution_ref": execution_ref, "provenance_ref": provenance_ref, **derived_refs}
    if report_manifest_ref:
        refs["report_manifest_ref"] = report_manifest_ref
    if execution_error:
        error_name = execution_error.get("name") if isinstance(execution_error, dict) else None
        if error_name in {"SyntaxError", "IndentationError"}:
            return error_response(
                step=step,
                error=f"generated Python has a {error_name}; fix the syntax and resubmit complete corrected code",
                recoverable=True,
                instruction="Do not rerun the query; call execute_python_analysis once more with the same frame_ref and corrected python_code. Never expose exception values in prose.",
                evidence={"refs": refs, "code_sha256": code_sha256},
            )
        return error_response(
            step=step,
            error="sandbox Python failed; details retained only in the authorized execution artifact",
            recoverable=False,
            instruction="Stop and report the opaque execution_ref; do not expose exception values or silently execute replacement code.",
            evidence={"refs": refs, "code_sha256": code_sha256},
        )
    return success_response(
        step=step,
        run_id=output_run_id,
        refs=refs,
        instruction="Explain the bounded results and limitations in the user's language. A requested report still needs an evidence-backed answer, not just a computation receipt. Charts are optional; use report_manifest_ref only when returned for renderable outputs, never guess a manifest from execution indexes. A Grafana Dashboard exists only after the approved writer returns its URL.",
        evidence={"validity": validity, "presentation_errors": figure_errors},
        output_summary=summary,
        provenance={key: value for key, value in provenance.items() if key != "code_ref"},
    )


def execute_python_preprocessing(
    args: dict[str, Any],
    executor: Callable[[bytes, str, str, int], dict[str, Any]] = execute_document_opensandbox,
) -> dict[str, Any]:
    step = "execute_python_preprocessing"
    try:
        context = context_from_args(args)
        inputs = {key: value for key, value in args.items() if key not in {"context", "_server_context"}}
        inputs.setdefault("seed", DEFAULT_SEED)
        return ARTIFACTS.run_once(context, step, inputs, lambda: _execute_python_preprocessing(args, executor))
    except (PermissionError, WorkflowContractError, OSError, TypeError, ValueError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Reconcile any pending document operation before retrying.")


def _execute_python_preprocessing(args: dict[str, Any], executor: Callable[[bytes, str, str, int], dict[str, Any]]) -> dict[str, Any]:
    step = "execute_python_preprocessing"
    unexpected = sorted(set(args) - {"document_ref", "python_code", "seed", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only the declared document ref, Python source, and seed.")
    document_ref = args.get("document_ref")
    python_code = args.get("python_code")
    seed = args.get("seed", DEFAULT_SEED)
    if not isinstance(document_ref, str):
        return error_response(step=step, error="document_ref is required", recoverable=False, instruction="Stop; inspect an authorized uploaded dataset first.")
    if not isinstance(python_code, str) or not python_code.strip():
        return error_response(step=step, error="python_code is required", recoverable=False, instruction="Stop; provide the confirmed Python preprocessing source.")
    code_bytes = python_code.encode("utf-8")
    if len(code_bytes) > MAX_CODE_BYTES:
        return error_response(step=step, error=f"python_code exceeds {MAX_CODE_BYTES} bytes", recoverable=False, instruction="Stop; reduce the code to the requested preprocessing only.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        return error_response(step=step, error="seed must be an integer from 0 to 4294967295", recoverable=False, instruction="Stop; provide a valid deterministic seed.")
    try:
        context = context_from_args(args)
        document_bytes, document = read_authorized_document(context, document_ref)
        input_format = document.get("source_format")
        if input_format not in {"csv", "xlsx"}:
            raise WorkflowContractError("uploaded document format is unsupported")
    except (PermissionError, OSError, WorkflowContractError, ValueError, TypeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; the uploaded document is invalid or unauthorized.")
    code_sha256 = hashlib.sha256(code_bytes).hexdigest()
    try:
        execution = executor(document_bytes, input_format, python_code, seed)
    except Exception as exc:
        return error_response(step=step, error=f"sandbox execution outcome unknown: {type(exc).__name__}", recoverable=False, instruction="Reconcile the existing execution before retrying.", evidence={"effect_outcome": "indeterminate"})
    if len(json.dumps(execution, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
        return error_response(step=step, error=f"sandbox output exceeds {MAX_OUTPUT_BYTES} bytes", recoverable=False, instruction="Stop; request smaller displayed outputs.")
    validity = execution.get("input_audit")
    if validity != {"input_rows": 0, "valid_rows": 0, "excluded_rows": 0, "rules": []}:
        return error_response(step=step, error="sandbox execution returned an invalid trusted document audit", recoverable=False, instruction="Stop; do not trust outputs without host-verified input evidence.")
    settings = runtime_settings() if executor is execute_document_opensandbox else {"image": "self-check", "runtime_class": "fake"}
    summary = output_summary(execution)
    output_run_id = ARTIFACTS.create_run(context)
    code_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-code", {"sha256": code_sha256, "source": python_code})
    provenance = {
        "runtime": "opensandbox",
        "runtime_class": settings["runtime_class"],
        "server_config_sha256": settings.get("server_config_sha256"),
        "image": settings["image"],
        "code_sha256": code_sha256,
        "code_ref": code_ref,
        "input_document_ref": document_ref,
        "input_upload_id": document["upload_id"],
        "input_format": input_format,
        "input_fields": [],
        "seed": seed,
        "network": "deny",
        "resource": sandbox_policy()["resource"],
        "limits": {"timeout_seconds": sandbox_policy()["timeout_seconds"], "source_bytes": MAX_CODE_BYTES, "document_bytes": uploaded_datasets.MAX_UPLOAD_BYTES, "captured_execution_bytes": MAX_OUTPUT_BYTES, "derived_frame_bytes": MAX_DERIVED_BYTES, "derived_rows": MAX_DERIVED_ROWS, "derived_fields": MAX_OUTPUT_FIELDS},
        "validity": validity,
        "output_summary": summary,
        "parent_provenance_ref": None,
    }
    execution_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-execution", execution)
    summary["assets"] = output_asset_summary(execution, execution_ref)
    summary["downloads"] = output_download_summary(execution, execution_ref, context)
    execution_error = execution.get("error")
    try:
        derived_refs, derived_summary = ({}, None) if execution_error else persist_derived_data(context, execution, output_run_id, document)
    except (OSError, PermissionError, ValueError, WorkflowContractError) as exc:
        return error_response(step=step, error=f"derived frame rejected: {exc}", recoverable=False, instruction="Stop; the Sandbox output did not satisfy the derived-data contract.", evidence={"execution_ref": execution_ref})
    if derived_summary is not None:
        summary["derived_data"] = derived_summary
    provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", provenance)
    refs = {"execution_ref": execution_ref, "provenance_ref": provenance_ref, **derived_refs}
    if execution_error:
        error_name = execution_error.get("name") if isinstance(execution_error, dict) else None
        if error_name in {"SyntaxError", "IndentationError"}:
            return error_response(step=step, error=f"generated Python has a {error_name}; fix the syntax and resubmit complete corrected code", recoverable=True, instruction="Call execute_python_preprocessing again with the same document_ref and corrected complete code.", evidence={"refs": refs, "code_sha256": code_sha256})
        return error_response(step=step, error="sandbox Python failed; details retained only in the authorized execution artifact", recoverable=False, instruction="Stop and report the opaque execution_ref; do not expose exception values or silently execute replacement code.", evidence={"refs": refs, "code_sha256": code_sha256})
    return success_response(
        step=step,
        run_id=output_run_id,
        refs=refs,
        instruction="Reuse these display/download artifacts within this session. Derived datasets and verified ML from arbitrary document code are not supported.",
        evidence={"input_format": input_format, "source_sha256": document.get("source_sha256")},
        output_summary=summary,
        derived_frame_ref=derived_refs.get("derived_frame_ref"),
        derived_dataset_id=derived_summary.get("derived_dataset_id") if derived_summary else None,
        provenance={key: value for key, value in provenance.items() if key != "code_ref"},
    )


def read_provenance(context: dict[str, str], provenance_ref: str) -> dict[str, Any]:
    _run_id, parts = parse_artifact_ref(provenance_ref)
    if parts != ("sandbox-provenance",):
        raise WorkflowContractError("provenance_ref must reference sandbox-provenance")
    provenance = ARTIFACTS.read_json(context, provenance_ref)
    input_refs = [provenance.get("input_frame_ref"), provenance.get("input_document_ref")] if isinstance(provenance, dict) else []
    if not isinstance(provenance, dict) or sum(isinstance(value, str) for value in input_refs) != 1 or not isinstance(provenance.get("code_ref"), str):
        raise WorkflowContractError("sandbox provenance artifact is incomplete")
    return provenance


def list_python_analyses(args: dict[str, Any]) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"context", "_server_context"})
    if unexpected:
        return error_response(step="list_python_analyses", error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; this tool accepts no model-provided arguments.")
    try:
        context = context_from_args(args)
        refs = ARTIFACTS.list_refs(context, "sandbox-provenance", limit=20)
        analyses = []
        for ref in refs:
            provenance = read_provenance(context, ref)
            analyses.append({
                "provenance_ref": ref,
                "code_sha256": provenance.get("code_sha256"),
                "input_fields": provenance.get("input_fields", []),
                "output_summary": provenance.get("output_summary", {}),
                "report_manifest_ref": provenance.get("report_manifest_ref"),
                "computation_status": provenance.get("computation_status"),
                "report_status": provenance.get("report_status"),
                "parent_provenance_ref": provenance.get("parent_provenance_ref"),
            })
    except (PermissionError, WorkflowContractError, OSError) as exc:
        return error_response(step="list_python_analyses", error=str(exc), recoverable=False, instruction="Stop; recent analyses could not be listed securely.")
    return success_response(step="list_python_analyses", run_id="run_listing", instruction="Use inspect_python_analysis on the matching revision before generating replacement code.", analyses=analyses)


def inspect_python_analysis(args: dict[str, Any]) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"provenance_ref", "context", "_server_context"})
    if unexpected:
        return error_response(step="inspect_python_analysis", error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only provenance_ref.")
    provenance_ref = args.get("provenance_ref")
    if not isinstance(provenance_ref, str):
        return error_response(step="inspect_python_analysis", error="provenance_ref is required", recoverable=False, instruction="Stop; list recent Sandbox analyses first.")
    try:
        context = context_from_args(args)
        provenance = read_provenance(context, provenance_ref)
        code = ARTIFACTS.read_json(context, provenance["code_ref"])
        if not isinstance(code, dict) or not isinstance(code.get("source"), str):
            raise WorkflowContractError("sandbox code artifact is incomplete")
    except (PermissionError, WorkflowContractError) as exc:
        return error_response(step="inspect_python_analysis", error=str(exc), recoverable=False, instruction="Stop; the analysis revision is invalid or unauthorized.")
    return success_response(
        step="inspect_python_analysis",
        run_id=parse_artifact_ref(provenance_ref)[0],
        instruction="Inspect the retained source and outputs; raw input rows/document bytes are intentionally not returned.",
        python_code=code["source"],
        provenance_ref=provenance_ref,
        code_sha256=provenance.get("code_sha256"),
        input_fields=provenance.get("input_fields", []),
        output_summary=provenance.get("output_summary", {}),
        report_manifest_ref=provenance.get("report_manifest_ref"),
        computation_status=provenance.get("computation_status"),
        report_status=provenance.get("report_status"),
        parent_provenance_ref=provenance.get("parent_provenance_ref"),
    )


def revise_python_analysis(args: dict[str, Any], executor: Callable[[str, str, int], dict[str, Any]] = execute_opensandbox) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"provenance_ref", "python_code", "seed", "presentation_mode", "context", "_server_context"})
    if unexpected:
        return error_response(step="revise_python_analysis", error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only provenance_ref, replacement Python, and seed.")
    provenance_ref = args.get("provenance_ref")
    if not isinstance(provenance_ref, str):
        return error_response(step="revise_python_analysis", error="provenance_ref is required", recoverable=False, instruction="Stop; list and inspect a prior revision first.")
    try:
        context = context_from_args(args)
        provenance = read_provenance(context, provenance_ref)
    except (PermissionError, WorkflowContractError) as exc:
        return error_response(step="revise_python_analysis", error=str(exc), recoverable=False, instruction="Stop; the prior revision is invalid or unauthorized.")
    if not isinstance(provenance.get("input_frame_ref"), str):
        return error_response(step="revise_python_analysis", error="prior revision used a document input", recoverable=False, instruction="Call execute_python_preprocessing with the retained input_document_ref and complete replacement code.")
    return execute_python_analysis(
        {"frame_ref": provenance["input_frame_ref"], "python_code": args.get("python_code"), "seed": args.get("seed", DEFAULT_SEED), "presentation_mode": args.get("presentation_mode", DEFAULT_PRESENTATION_MODE), "_server_context": context},
        executor=executor,
        step="revise_python_analysis",
        parent_provenance_ref=provenance_ref,
    )


def rpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def reconcile_operation(args: dict[str, Any]) -> dict[str, Any]:
    try:
        context = context_from_args(args)
        if set(args) - {"operation_id", "_server_context", "context"}:
            raise WorkflowContractError("unsupported reconciliation arguments")
        operation_id = args.get("operation_id")
        if not isinstance(operation_id, str):
            raise WorkflowContractError("operation_id is required")
        reconciled = ARTIFACTS.reconcile_operation(context, operation_id)
        return {"ok": True, "step": "reconcile_operation", **reconciled,
                "instruction": "Only completed/failed receipts are final evidence. Indeterminate remains blocked; no compute was dispatched."}
    except (PermissionError, WorkflowContractError, OSError, ValueError) as exc:
        return error_response(step="reconcile_operation", error=str(exc), recoverable=False, instruction="Use only an operation ID returned to this authenticated session; never invent completion evidence.")


def get_ml_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    step = "get_ml_capabilities"
    try:
        if set(args) - {"_server_context"}:
            raise WorkflowContractError("capability inspection accepts no dataset or source code")
        context = context_from_args(args)
        image = runtime_settings()["image"]
        source = '''import importlib, json
packages = {}
for name in ("numpy", "pandas", "sklearn", "catboost", "xgboost", "lightgbm", "shap"):
    try:
        module = importlib.import_module(name)
        packages[name] = {"available": True, "version": str(module.__version__)}
    except Exception as exc:
        packages[name] = {"available": False, "error": type(exc).__name__}
print(json.dumps(packages))
'''
        def inspect() -> dict[str, Any]:
            execution = execute_opensandbox_input("/tmp/capability-probe.py", source, source, capture_required=False)
            if execution.get("error"):
                raise WorkflowContractError("sandbox capability inspection failed")
            raw = "".join(str(item.get("text", "")) for item in execution.get("stdout", []))
            packages = json.loads(raw)
            base = all(packages[name]["available"] for name in ("numpy", "pandas", "sklearn"))
            regression = ["dummy", "ridge", "random_forest", "extra_trees", "hist_gradient_boosting"] if base else []
            classification = ["logistic_regression", "random_forest_shap"] if base else []
            for name in ("catboost", "xgboost"):
                if base and packages[name]["available"]:
                    regression.append(name)
                    classification.append(name)
            if base and packages["lightgbm"]["available"]:
                classification.append("gradient_boosting")
            return success_response(step=step, run_id="run_" + uuid.uuid4().hex, refs={}, instruction="Select only supported task/split/algorithm combinations from this actual image. Use a single global search budget; package availability is not a guarantee for every dataset.", evidence={"image": image, "packages": packages}, capabilities={"regression": {"algorithms": regression, "splits": ["chronological_holdout", "grouped_holdout"]}, "binary_classification": {"algorithms": classification, "splits": ["stratified_holdout"]}, "execution_mode": "sequential", "max_global_search_trials": 40, "sample_weights": False, "derived_datasets": False})
        return ARTIFACTS.run_once(context, step, {"image": image, "probe_sha256": hashlib.sha256(source.encode()).hexdigest()}, inspect)
    except (OSError, ValueError, KeyError, TypeError, WorkflowContractError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Sandbox capability inspection is unavailable; do not infer support from installed host packages.")


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
        tool = next((item for item in TOOLS if item["name"] == name), None)
        if tool is None:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context"}
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            out = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only arguments declared by this tool schema.")
        else:
            handlers = {
                "profile_dataset": profile_dataset,
                "reexport_trusted_report": reexport_trusted_report,
                "repair_generic_report": repair_generic_report,
                "execute_ml_contract": execute_ml_contract,
                "get_ml_capabilities": get_ml_capabilities,
                "reconcile_operation": reconcile_operation,
                "execute_python_analysis": execute_python_analysis,
                "execute_python_preprocessing": execute_python_preprocessing,
                "list_python_analyses": list_python_analyses,
                "inspect_python_analysis": inspect_python_analysis,
                "revise_python_analysis": revise_python_analysis,
            }
            out = handlers[name](arguments)
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
        if self.path.startswith("/assets/"):
            token = self.path.removeprefix("/assets/").split("?", 1)[0]
            try:
                data, mime_type, _display_name = artifact_assets.read_signed_output(token, secret=os.environ.get("MCP_SHARED_TOKEN", ""), artifacts=ARTIFACTS)
            except (PermissionError, OSError, ValueError):
                return self._send(403, {"error": "artifact URL is invalid or expired"})
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            if mime_type == "text/csv":
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(_display_name, safe=""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", os.environ.get("GRAFANA_PUBLIC_ORIGIN", "http://localhost:3000"))
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(405 if self.path.rstrip("/") == "/mcp" else 404, {"error": "POST JSON-RPC to /mcp"})

    def do_DELETE(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            return self._send(404, {"error": "not found"})
        if authenticate_headers(self.headers) is None:
            return self._send(401, {"error": "authenticated MCP service identity is required"})
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return self._send(400, rpc_error(None, -32700, "invalid Content-Length"))
        if content_length < 0 or content_length > MAX_RPC_BODY_BYTES:
            return self._send(413, rpc_error(None, -32000, f"request body exceeds {MAX_RPC_BODY_BYTES} bytes"))
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, rpc_error(None, -32700, "parse error"))
        messages = payload if isinstance(payload, list) else [payload]
        replies = [reply for message in messages if (reply := handle_rpc(inject_header_context(message, self.headers))) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):  # noqa: A002
        sys.stderr.write("sandbox-analysis-mcp " + format % args + "\n")


def self_check() -> None:
    global ARTIFACTS
    original = ARTIFACTS
    original_upload_root = getattr(uploaded_datasets, "UPLOAD_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        setattr(uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        context = {"org_id": "1", "user_id": "self-check", "session_id": "session-self-check"}
        source_run = ARTIFACTS.create_run(context)
        frame_payload = [{"schema": {"fields": [{"name": "x"}, {"name": "heat_rate_valid"}]}, "data": {"values": [[1, 2, 3], [True, False, True]]}}]
        frame_ref = ARTIFACTS.write_json(context, source_run, "grafana-frame", frame_payload)
        plan_payload = {
            "business_question": "legacy trusted question",
            "analysis_input_contract": {"validity_rules": [{"field": "heat_rate_valid", "accepted_values": [True], "applies_to": ["x"]}]},
        }
        plan_payload["plan_sha256"] = hashlib.sha256(json.dumps(plan_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        ARTIFACTS.write_json(context, source_run, "query-plan", plan_payload)
        observed = {}
        png_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="

        def fake_executor(frame_bundle_json: str, code: str, seed: int) -> dict[str, Any]:
            try:
                bundle = json.loads(frame_bundle_json)
            except json.JSONDecodeError as exc:
                raise AssertionError("invalid test frame bundle") from exc
            observed.update({"bundle": bundle, "code": code, "seed": seed})
            input_rows = len(bundle["frame"]["data"]["values"][0])
            excluded_rows = 1 if bundle["validity_rules"] else 0
            return {
                "execution_id": "fake",
                "execution_count": 1,
                "exit_code": 0,
                "results": [
                    {"text": "mean=2", "timestamp": 1, "mime": {}, "display_name": "summary.txt"},
                    {"text": None, "timestamp": 1, "mime": {"application/json": "{\"mean\":2}"}, "display_name": "result.json"},
                    {"text": None, "timestamp": 1, "mime": {"text/csv": "x\n1\n2\n"}, "display_name": "result.csv"},
                    {"text": None, "timestamp": 1, "mime": {"text/html": "<table></table>", "image/png": png_1x1}, "display_name": "plot.png"},
                ],
                "stdout": [{"text": "done\n", "timestamp": 1}],
                "stderr": [],
                "error": None,
                "complete": {"timestamp": 2, "execution_time_in_millis": 1},
                "input_audit": {"input_rows": input_rows, "valid_rows": input_rows - excluded_rows, "excluded_rows": excluded_rows, "rules": bundle["validity_rules"]},
            }

        args = {"frame_ref": frame_ref, "python_code": "display(df)", "seed": 7, "presentation_mode": "image", "_server_context": context}
        result = execute_python_analysis(args, executor=fake_executor)
        assert result["ok"] and result["output_summary"]["mime_types"] == ["application/json", "image/png", "text/csv", "text/html", "text/plain"]
        assert result["output_summary"]["inline_results"][:2] == [
            {"output_index": 0, "display_name": "summary.txt", "mime_type": "text/plain", "value": "mean=2"},
            {"output_index": 1, "display_name": "result.json", "mime_type": "application/json", "value": {"mean": 2}},
        ]
        assert any(item["display_name"] == "report-source.json" for item in result["output_summary"]["inline_results"])
        assert len(result["output_summary"]["downloads"]) == 1 and result["output_summary"]["downloads"][0]["display_name"] == "result.csv"
        download_token = result["output_summary"]["downloads"][0]["url"].rsplit("/", 1)[1]
        download, download_mime, download_name = artifact_assets.read_signed_output(download_token, secret=os.environ.get("MCP_SHARED_TOKEN", ""), artifacts=ARTIFACTS)
        assert download == b"x\n1\n2\n" and download_mime == "text/csv" and download_name == "result.csv"
        wide_names = [f"field_{index}" for index in range(MAX_OUTPUT_FIELDS + 1)]
        wide_csv = ",".join(wide_names) + "\n" + ",".join("1" for _ in wide_names) + "\n"
        wide_summary = output_summary({"results": [{"text": None, "mime": {"text/csv": wide_csv}, "display_name": "wide.csv"}], "stdout": [], "stderr": []})
        assert len(wide_summary["tabular_outputs"][0]["fields"]) == MAX_OUTPUT_FIELDS
        oversized_inline = output_summary({"results": [{"text": "x" * (MAX_INLINE_RESULT_BYTES + 1), "mime": {}, "display_name": "summary.txt"}], "stdout": [], "stderr": []})
        assert oversized_inline["inline_results"] == [] and oversized_inline["inline_results_truncated"]
        assert result["provenance"]["limits"] == {"timeout_seconds": 3600, "source_bytes": MAX_CODE_BYTES, "rpc_body_bytes": MAX_RPC_BODY_BYTES, "input_bundle_bytes": MAX_INPUT_BUNDLE_BYTES, "captured_execution_bytes": MAX_OUTPUT_BYTES, "stdout_stderr_bytes_each": MAX_LOG_BYTES, "inline_result_bytes": MAX_INLINE_RESULT_BYTES, "output_fields": MAX_OUTPUT_FIELDS, "signed_download_bytes": artifact_assets.MAX_ASSET_BYTES}
        assert observed["seed"] == 7 and observed["bundle"]["frame"]["data"]["values"][0] == [1, 2, 3]
        assert observed["bundle"]["validity_rules"][0]["field"] == "heat_rate_valid"
        assert "display(df)" not in json.dumps(result) and '"values"' not in json.dumps(result)
        assert ARTIFACTS.read_json(context, result["refs"]["execution_ref"])["results"][3]["mime"]["image/png"] == png_1x1
        default_result = execute_python_analysis({"frame_ref": frame_ref, "python_code": "display(df)", "seed": 6, "_server_context": context}, executor=fake_executor)
        assert not default_result["ok"] and "Plotly figure JSON presentation requires" in default_result["error"]
        assert default_result["evidence"]["computation_status"] == "succeeded" and default_result["evidence"]["report_status"] == "rejected"
        assert ARTIFACTS.read_json(context, default_result["evidence"]["execution_ref"])["error"] is None
        assert ARTIFACTS.read_json(context, default_result["evidence"]["provenance_ref"])["report_status"] == "rejected"

        def fake_plotly_executor(frame_bundle_json: str, code: str, seed: int) -> dict[str, Any]:
            execution = fake_executor(frame_bundle_json, code, seed)
            execution["results"].append({"text": None, "timestamp": 1, "mime": {"application/vnd.plotly.v1+json": json.dumps({"data": [{"type": "scatter", "x": [1], "y": [2]}], "layout": {}, "config": {"displaylogo": False, "responsive": True}})}, "display_name": "figure.json"})
            return execution

        plotly_result = execute_python_analysis({"frame_ref": frame_ref, "python_code": "emit(figure, name='figure.json')", "seed": 10, "_server_context": context}, executor=fake_plotly_executor)
        assert plotly_result["ok"] and plotly_result["output_summary"]["presentation_mode"] == "plotly"
        plotly_manifest_ref = plotly_result["refs"].get("report_manifest_ref")
        assert isinstance(plotly_manifest_ref, str)
        plotly_manifest = ARTIFACTS.read_json(context, plotly_manifest_ref)
        assert plotly_manifest["execution_ref"] == plotly_result["refs"]["execution_ref"]
        assert plotly_result["provenance"]["report_manifest_ref"] == plotly_manifest_ref
        assert {item["artifact_id"] for item in plotly_manifest["artifacts"]} == {"plot", "figure"}
        assert {item["render"]["mode"] for item in plotly_manifest["artifacts"]} == {"image", "plotly"}

        def invalid_plotly_executor(frame_bundle_json: str, code: str, seed: int) -> dict[str, Any]:
            execution = fake_plotly_executor(frame_bundle_json, code, seed)
            invalid = {"data": [{"type": "scatter", "x": [1], "y": [2]}], "layout": {"xaxis": {"unsupported_axis_key": 1}}}
            execution["results"][-1]["mime"]["application/vnd.plotly.v1+json"] = json.dumps(invalid)
            return execution

        invalid_plotly = execute_python_analysis({"frame_ref": frame_ref, "python_code": "emit(figure)", "seed": 12, "_server_context": context}, executor=invalid_plotly_executor)
        assert not invalid_plotly["ok"] and invalid_plotly["evidence"]["correction_required"], invalid_plotly
        assert "same frame_ref" in invalid_plotly["instruction"] and "do not call repair_generic_report" in invalid_plotly["instruction"], invalid_plotly
        operation_root = ARTIFACTS.root / "operations"
        operations_before_repair = sorted(path.name for path in operation_root.iterdir()) if operation_root.exists() else []
        invalid_repair = repair_generic_report({"execution_ref": invalid_plotly["evidence"]["execution_ref"], "_server_context": context})
        assert not invalid_repair["ok"] and "cannot accept the retained renderable" in invalid_repair["error"], invalid_repair
        operations_after_repair = sorted(path.name for path in operation_root.iterdir()) if operation_root.exists() else []
        assert operations_after_repair == operations_before_repair, "deterministic repair rejection reserved an operation"
        try:
            ml_report_contract.normalize_report_manifest(
                execution_ref="artifact://run_without_source/sandbox-execution",
                results=[{"display_name": "figure.json", "mime": {"application/vnd.plotly.v1+json": json.dumps({"data": [], "layout": {}, "config": {"displaylogo": False, "responsive": True}})}}],
            )
        except ValueError as exc:
            if not str(exc).count("report source format is required"):
                raise AssertionError("unexpected missing-source error")
        else:
            raise AssertionError("missing report source must remain invalid")

        try:
            saved_execution = ARTIFACTS.read_json(context, plotly_result["refs"]["execution_ref"])
        except (OSError, TypeError, ValueError, KeyError) as exc:
            raise AssertionError("plotly execution fixture is unavailable") from exc
        unsafe_execution = {"error": None, "results": list(saved_execution["results"])}
        unsafe_execution["results"].insert(0, {"display_name": "result.json", "mime": {"application/json": json.dumps({"api_token": "secret", "customer_email": "user@example.invalid", "records": [{"value": 7}]})}})
        unsafe_source = _generic_report_source(unsafe_execution, {"input_rows": 3, "valid_rows": 2, "excluded_rows": 1})
        assert unsafe_source and set(unsafe_source["facts"]) == {"rows", "valid_rows", "excluded_rows"}
        for source_result in (
            {"display_name": "report-source.json", "mime": {"application/json": "{}"}},
            {"display_name": "REPORT-SOURCE.JSON", "mime": {"application/json": "{}"}},
            {"display_name": "custom.json", "mime": {"application/json": json.dumps({"format": ml_report_contract.REPORT_SOURCE_FORMAT})}},
        ):
            try:
                _ensure_generic_report_source({"error": None, "results": [*unsafe_execution["results"], source_result]}, {"input_rows": 3, "valid_rows": 2, "excluded_rows": 1}, "execute_python_analysis")
            except WorkflowContractError:
                continue
            raise AssertionError("generic Python report source injection was accepted")

        def fake_injected_report_source_executor(frame_bundle_json: str, code: str, seed: int) -> dict[str, Any]:
            execution = fake_plotly_executor(frame_bundle_json, code, seed)
            execution["results"].append({"display_name": "custom.json", "mime": {"application/json": json.dumps({"format": ml_report_contract.REPORT_SOURCE_FORMAT})}})
            return execution

        injected = execute_python_analysis({"frame_ref": frame_ref, "python_code": "emit(figure, name='figure.json')", "seed": 11, "_server_context": context}, executor=fake_injected_report_source_executor)
        assert not injected["ok"] and "generic report source rejected" in injected["error"]
        assert injected["evidence"]["computation_status"] == "succeeded" and injected["evidence"]["report_status"] == "rejected"
        assert ARTIFACTS.read_json(context, injected["evidence"]["execution_ref"])["error"] is None

        legacy_run = ARTIFACTS.create_run(context)
        legacy_execution = ARTIFACTS.write_json(context, legacy_run, "sandbox-execution", {"results": [
            {"text": None, "timestamp": 1, "mime": {"image/png": png_1x1}, "display_name": "legacy-chart.png"},
            {"text": None, "timestamp": 1, "mime": {"application/json": json.dumps({"format": "ask-o11y-ml-presentation-v1", "purpose": "legacy trusted question", "conclusion": "legacy trusted evidence", "data": {"rows": 3}, "artifacts": [{"name": "legacy-chart.png"}]})}, "display_name": "ml-presentation.json"},
        ], "error": None})
        legacy_code = "legacy trusted code"
        legacy_code_sha256 = hashlib.sha256(legacy_code.encode("utf-8")).hexdigest()
        legacy_code_ref = ARTIFACTS.write_json(context, legacy_run, "sandbox-code", {"sha256": legacy_code_sha256, "source": legacy_code})
        frame_sha256 = hashlib.sha256(json.dumps(frame_payload[0], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        legacy_provenance = ARTIFACTS.write_json(context, legacy_run, "sandbox-provenance", {"executor_kind": "execute_ml_contract", "trusted_ml_contract": True, "input_frame_ref": frame_ref, "input_frame_sha256": frame_sha256, "plan_sha256": plan_payload["plan_sha256"], "code_sha256": legacy_code_sha256, "code_ref": legacy_code_ref, "business_question": "legacy trusted question", "report_manifest_ref": None, "computation_status": "succeeded", "report_status": "rejected"})
        reexported = reexport_trusted_report({"execution_ref": legacy_execution, "_server_context": context})
        assert reexported["ok"] and reexported["refs"]["report_manifest_ref"] != legacy_execution
        fresh_manifest = ARTIFACTS.read_json(context, reexported["refs"]["report_manifest_ref"])
        fresh_provenance = ARTIFACTS.read_json(context, reexported["refs"]["provenance_ref"])
        assert fresh_manifest["format"] == ml_report_contract.REPORT_MANIFEST_FORMAT and fresh_manifest["execution_ref"] == reexported["refs"]["execution_ref"]
        assert fresh_provenance["report_manifest_ref"] == reexported["refs"]["report_manifest_ref"] and fresh_provenance["reexport_of"] == legacy_execution and fresh_provenance["source_provenance_ref"] == legacy_provenance
        assert fresh_provenance["computation_status"] == "succeeded" and fresh_provenance["report_status"] == "accepted"
        replayed_reexport = reexport_trusted_report({"execution_ref": legacy_execution, "_server_context": context})
        assert replayed_reexport["refs"] == reexported["refs"], replayed_reexport
        retained_lineage = ARTIFACTS.read_json(context, legacy_provenance)
        for identity_key in ("code_sha256", "input_frame_sha256", "plan_sha256", "business_question"):
            altered = dict(retained_lineage)
            altered[identity_key] = ("0" * 64) if identity_key != "business_question" else "foreign question"
            try:
                _verify_trusted_lineage(context, legacy_execution, altered)
            except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError):
                continue
            else:
                raise AssertionError(f"mismatched {identity_key} lineage was accepted")
        for missing_key in ("code_ref", "input_frame_ref"):
            altered = dict(retained_lineage)
            altered.pop(missing_key, None)
            try:
                _verify_trusted_lineage(context, legacy_execution, altered)
            except (ArtifactAuthError, WorkflowContractError, OSError, ValueError, TypeError, KeyError):
                continue
            else:
                raise AssertionError(f"missing {missing_key} lineage was accepted")
        indeterminate_run = ARTIFACTS.create_run(context)
        indeterminate_execution = ARTIFACTS.write_json(context, indeterminate_run, "sandbox-execution", {"results": ARTIFACTS.read_json(context, legacy_execution)["results"], "error": None})
        indeterminate_code_ref = ARTIFACTS.write_json(context, indeterminate_run, "sandbox-code", {"sha256": legacy_code_sha256, "source": legacy_code})
        indeterminate_provenance = ARTIFACTS.write_json(context, indeterminate_run, "sandbox-provenance", {**ARTIFACTS.read_json(context, legacy_provenance), "code_ref": indeterminate_code_ref, "computation_status": "indeterminate", "report_status": "rejected"})
        refused_indeterminate = reexport_trusted_report({"execution_ref": indeterminate_execution, "_server_context": context})
        assert not refused_indeterminate["ok"] and "succeeded computation" in refused_indeterminate["error"], refused_indeterminate
        untrusted_run = ARTIFACTS.create_run(context)
        untrusted_execution = ARTIFACTS.write_json(context, untrusted_run, "sandbox-execution", {"results": [], "error": None})
        ARTIFACTS.write_json(context, untrusted_run, "sandbox-provenance", {"executor_kind": "execute_python_analysis", "trusted_ml_contract": False, "input_frame_ref": frame_ref, "code_ref": "artifact://untrusted/sandbox-code", "report_manifest_ref": None})
        untrusted = reexport_trusted_report({"execution_ref": untrusted_execution, "_server_context": context})
        assert not untrusted["ok"] and "generic Python" in untrusted["error"]

        source = uploaded_datasets.store_upload(context=context, session_id="session-self-check", filename="source.csv", raw=b"old_a,old_b\n1,3\n2,4\n")
        document_run = ARTIFACTS.create_run(context)
        document_ref = ARTIFACTS.write_json(context, document_run, "uploaded-document", {"upload_id": source["id"], "session_id": source["session_id"], "filename": source["filename"], "sheet": None, "source_format": source["source_format"], "source_sha256": source["source_sha256"]})
        derived_payload = json.dumps({"format": "ask-o11y-dataframe-v1", "columns": ["clean_a", "clean_b"], "types": ["number", "number"], "data": [[1, 3], [2, 4]]})

        def fake_document_executor(document_bytes: bytes, input_format: str, code: str, seed: int) -> dict[str, Any]:
            assert document_bytes == b"old_a,old_b\n1,3\n2,4\n" and input_format == "csv" and code == "emit_frame(cleaned)" and seed == 9
            return {"execution_id": "document", "results": [{"text": None, "mime": {DERIVED_FRAME_MIME: derived_payload}, "display_name": "cleaned-data"}], "stdout": [], "stderr": [], "error": None, "complete": {}, "input_audit": {"input_rows": 0, "valid_rows": 0, "excluded_rows": 0, "rules": []}}

        preprocessed = execute_python_preprocessing({"document_ref": document_ref, "python_code": "emit_frame(cleaned)", "seed": 9, "_server_context": context}, executor=fake_document_executor)
        assert not preprocessed["ok"] and "derived frame rejected" in preprocessed["error"] and "derived datasets are disabled" in preprocessed["error"]
        foreign_document = execute_python_preprocessing({"document_ref": document_ref, "python_code": "emit_frame(cleaned)", "_server_context": {"org_id": "2", "user_id": "attacker"}}, executor=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
        assert not foreign_document["ok"]
        invalid_frame_execution = fake_document_executor(b"old_a,old_b\n1,3\n2,4\n", "csv", "emit_frame(cleaned)", 9)
        invalid_frame_execution["results"][0]["mime"][DERIVED_FRAME_MIME] = json.dumps({"format": "ask-o11y-dataframe-v1", "columns": ["x"], "types": ["number"], "data": [[1, 2]]})
        invalid_derived = execute_python_preprocessing({"document_ref": document_ref, "python_code": "emit_frame(cleaned)", "seed": 9, "_server_context": context}, executor=lambda *_: invalid_frame_execution)
        assert not invalid_derived["ok"] and "derived frame rejected" in invalid_derived["error"]

        listed = list_python_analyses({"_server_context": context})
        listed_result = next(item for item in listed["analyses"] if item["provenance_ref"] == result["refs"]["provenance_ref"])
        assert listed["ok"] and listed_result["report_manifest_ref"] == result["refs"].get("report_manifest_ref"), listed
        inspected = inspect_python_analysis({"provenance_ref": result["refs"]["provenance_ref"], "_server_context": context})
        assert inspected["ok"] and inspected["python_code"] == "display(df)" and "values" not in inspected
        assert inspected["report_manifest_ref"] == result["refs"].get("report_manifest_ref")
        revised = revise_python_analysis({"provenance_ref": result["refs"]["provenance_ref"], "python_code": "display(df.head())", "seed": 8, "presentation_mode": "image", "_server_context": context}, executor=fake_executor)
        assert revised["ok"] and revised["step"] == "revise_python_analysis" and revised["provenance"]["parent_provenance_ref"] == result["refs"]["provenance_ref"]
        revised_manifest_ref = revised["refs"].get("report_manifest_ref")
        assert isinstance(revised_manifest_ref, str) and revised_manifest_ref != result["refs"].get("report_manifest_ref")
        revised_manifest = ARTIFACTS.read_json(context, revised_manifest_ref)
        assert revised_manifest["execution_ref"] == revised["refs"]["execution_ref"]
        foreign = execute_python_analysis({**args, "_server_context": {"org_id": "2", "user_id": "attacker", "session_id": "attacker-session"}}, executor=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
        assert not foreign["ok"] and "mismatch" in foreign["error"]
        oversized = execute_python_analysis({**args, "python_code": "x" * (MAX_CODE_BYTES + 1)}, executor=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
        assert not oversized["ok"] and "exceeds" in oversized["error"]
        policy = sandbox_policy()
        assert policy["network_default_action"] == "deny" and policy["env"] == {} and policy["volumes"] == [] and policy["resource"] == {"cpu": "4", "memory": "4Gi"}

        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter(["data: {\"type\":\"init\"}", "data: {\"type\":\"execution_complete\"}", "data: {\"type\":\"stdout\"}"])

        class FakeStream:
            def __enter__(self):
                return FakeResponse()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class FakeClient:
            def stream(self, *args, **kwargs):
                return FakeStream()

        with _ExecutionCompleteSSEClient(FakeClient()).stream("POST", "/code") as response:
            assert list(response.iter_lines()) == ["data: {\"type\":\"init\"}", "data: {\"type\":\"execution_complete\"}", "data: {\"type\":\"stdout\"}"]
        raw = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "execute_python_analysis", "arguments": {**args, "frame": []}}})
        assert raw is not None
        try:
            payload = json.loads(raw["result"]["content"][0]["text"])
        except json.JSONDecodeError as exc:
            raise AssertionError("invalid test tool response") from exc
        assert not payload["ok"] and "unsupported tool arguments" in payload["error"]
    ARTIFACTS = original
    setattr(uploaded_datasets, "UPLOAD_ROOT", original_upload_root)
    print(json.dumps({"ok": True, "checks": ["authorized_frame_bundle", "trusted_validity_audit", "document_derived_output_rejected", "foreign_document_rejected", "invalid_derived_frame_rejected", "bounded_inline_results", "signed_csv_download", "200_field_output_summary", "opaque_mime_artifact", "cross_conversation_list_inspect_revise", "host_report_manifest_bindings", "trusted_report_reexport", "fresh_revision_report_ref", "foreign_context_rejected", "oversized_code_rejected", "deny_all_policy", "raw_frame_rejected"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    require_runtime_token()
    require_service_identity()
    runtime_settings()
    bind_host = runtime_bind_host()
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} on {bind_host}:{PORT}", file=sys.stderr)
    ThreadingHTTPServer((bind_host, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
