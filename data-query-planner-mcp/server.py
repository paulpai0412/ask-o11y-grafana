#!/usr/bin/env python3
"""Data Query Planner MCP.

Plans datasource queries and validates metadata/profile boundaries. It never
executes datasource queries; Grafana Query owns execution.
"""
from __future__ import annotations

import argparse
import hashlib
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
wferp_sql = load_module("wferp_sql", HERE / "wferp_sql.py")
ontology_contract = load_module("ontology_contract", ROOT / "ontology_contract.py")
uploaded_datasets = load_module("uploaded_datasets", ROOT / "uploaded_datasets.py")
upload_semantics = load_module("upload_semantics", ROOT / "upload_semantics.py")
WFERP_METADATA = wferp_sql.load_metadata()
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
MAX_PLAN_ROWS = 100_000
MAX_PLAN_FIELDS = 200
MAX_PLAN_RESPONSE_BYTES = 50 * 1024 * 1024
MAX_BUSINESS_QUESTION_BYTES = 2048
ANALYSIS_KINDS = ("catboost", "random_forest_shap", "gradient_boosting", "logistic_regression", "xgboost")


def normalize_business_question(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_BUSINESS_QUESTION_BYTES:
        raise ValueError("business_question must be a bounded non-empty string")
    lowered = value.lower()
    if any(token in lowered for token in ("<", ">", "http://", "https://", "javascript:")):
        raise ValueError("business_question contains unsafe content")
    return value.strip()


def execution_template_for_kind(kind: str) -> str:
    if kind not in ANALYSIS_KINDS:
        raise ValueError(f"unsupported analysis kind: {kind}")
    return f"ask_o11y_{kind}_v1"


def execution_template_for_contract(contract: dict[str, Any]) -> str:
    if contract.get("task_kind") == "regression":
        return "ask_o11y_regression_v1"
    return execution_template_for_kind(str(contract.get("kind")))
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()



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
        return {"org_id": str(raw_context["org_id"]), "user_id": str(raw_context["user_id"]), "session_id": str(raw_context.get("session_id") or "")}
    raise workflow_node.WorkflowContractError("verified artifact context is required")


def bounded_metadata_time_range(metadata: dict[str, Any]) -> dict[str, str]:
    raw = metadata.get("date_range")
    if isinstance(raw, dict) and raw.get("kind") == "unbounded" and metadata.get("query_kind") == "uploaded_csv":
        return {"from": "now-367d", "to": "now"}
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
    unexpected = sorted(set(args) - {"dataset_metadata_ref", "selected_fields", "minimum_rows", "maximum_rows", "refId", "analysis_contract", "business_question", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported planner arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; Planner accepts only opaque metadata refs and explicit projection options.")
    metadata_ref = args.get("dataset_metadata_ref")
    selected_fields = args.get("selected_fields")
    try:
        business_question = normalize_business_question(args.get("business_question"))
    except ValueError as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; retain the exact confirmed business question in a new bounded plan.")
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
        semantic_validation = None
        generic_upload_snapshot = None
        upload_metadata: dict[str, Any] = {}
        field_views = [
            {
                key: field[key]
                for key in ("name", "physical_name", "type", "data_type", "semantic_kind", "unit", "analysis_role", "status")
                if key in field and field[key] is not None
            }
            for field in available.values()
        ]
        population_filter: dict[str, Any] = {}
        metadata_dataset_id = str(metadata.get("dataset_id") or "")
        if metadata_dataset_id.startswith("upload_"):
            upload_metadata = uploaded_datasets.inspect_upload(context, metadata_dataset_id, context.get("session_id"))
            generic_upload_snapshot = {"sha256": upload_metadata["source_sha256"], "snapshot_id": f"candidate:{metadata_dataset_id}"}
        analysis_contract = args.get("analysis_contract")
        if analysis_contract is not None:
            if not isinstance(analysis_contract, dict):
                raise workflow_node.WorkflowContractError("analysis_contract must be an object")
            try:
                missing_value_policy = ontology_contract.normalize_missing_target_split_policy(
                    analysis_contract.get("missing_value_policy"),
                    allow_drop=analysis_contract.get("task_kind") == "regression",
                )
            except ValueError as exc:
                raise workflow_node.WorkflowContractError(str(exc)) from exc
            analysis_contract = {**analysis_contract, "missing_value_policy": missing_value_policy}
            if analysis_contract.get("autotune"):
                budget = analysis_contract.get("search_budget", 20)
                regression = analysis_contract.get("task_kind") == "regression"
                objective = analysis_contract.get("objective", "mae" if regression else "roc_auc")
                minimum = analysis_contract.get("objective_minimum")
                objectives = {"mae"} if regression else {"accuracy", "roc_auc", "pr_auc"}
                maximum = None if regression else 1
                if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 40 or objective not in objectives or (minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or minimum < 0 or (maximum is not None and minimum > maximum))):
                    raise workflow_node.WorkflowContractError("autoresearch contract bounds are invalid")
            contract_dataset_id = str(analysis_contract.get("dataset_id"))
            if contract_dataset_id != metadata_dataset_id:
                raise workflow_node.WorkflowContractError("analysis dataset must match the authorized metadata")
            if contract_dataset_id.startswith("upload_"):
                upload_metadata = uploaded_datasets.inspect_upload(context, contract_dataset_id, context.get("session_id"))
                hints = upload_semantics.load_hints(uploaded_datasets.UPLOAD_ROOT / contract_dataset_id)
                semantic_validation = upload_semantics.validate_analysis_contract(hints, analysis_contract)
                semantic_validation["snapshot"].update({"sha256": upload_metadata["source_sha256"], "snapshot_id": f"candidate:{contract_dataset_id}"})
                if analysis_contract.get("ontology_snapshot_sha256") != upload_metadata["source_sha256"]:
                    semantic_validation["conforms"] = False
                    semantic_validation["rejection_codes"].append("SNAPSHOT_HASH_MISMATCH")
                required_projection = {str(analysis_contract.get("target")), *semantic_validation["included_fields"]}
                split = analysis_contract.get("split") or {}
                split_field = split.get("time_field") if split.get("kind") == "chronological_holdout" else split.get("group_field")
                if isinstance(split_field, str):
                    required_projection.add(split_field)
                population_filter = analysis_contract.get("population_filter") or {}
                quality_fields: set[str] = set()
                minimum_valid_rows = int(hints["quality_policy"]["minimum_valid_rows"])
            else:
                snapshot = ontology_contract.load_snapshot(dataset_id=contract_dataset_id)
                semantic_validation = ontology_contract.validate_analysis_contract(snapshot, analysis_contract)
                dataset = ontology_contract.find_dataset(snapshot, str(metadata.get("dataset_id")))
                if dataset is None:
                    raise workflow_node.WorkflowContractError("ontology dataset does not match authorized metadata")
                required_projection = {dataset["time_identity"], dataset["target"], *semantic_validation["included_fields"]}
                population_filter = analysis_contract.get("population_filter") or {}
                quality_fields = {field["physical_name"] for field in dataset["fields"] if field["status"] == "approved" and field["analysis_role"] == "quality"}
                minimum_valid_rows = int(dataset["quality_policy"]["minimum_valid_rows"])
            if not semantic_validation["conforms"]:
                return error_response(
                    step=step,
                    error="ontology semantic gate rejected the analysis contract",
                    recoverable=False,
                    instruction="Stop before Grafana Query; resolve every semantic rejection and create a new preview/plan hash.",
                    evidence={"rejection_codes": semantic_validation["rejection_codes"], "failed_rules": semantic_validation["failed_rules"], "limitations": semantic_validation.get("limitations", []), "downstream_call_counts": {"grafana_query": 0, "sandbox": 0, "dashboard_write": 0}},
                )
            filter_names = set(population_filter) if isinstance(population_filter, dict) else set()
            requested_projection = set(requested_fields) - quality_fields - filter_names
            if requested_projection != required_projection:
                return error_response(
                    step=step,
                    error="selected_fields do not match the approved ontology analysis projection",
                    recoverable=False,
                    instruction="Stop before Grafana Query; use exactly the target and approved feature fields returned by the semantic gate.",
                    evidence={"rejection_codes": ["ANALYSIS_PROJECTION_MISMATCH"], "approved_projection": sorted(required_projection), "downstream_call_counts": {"grafana_query": 0, "sandbox": 0, "dashboard_write": 0}},
                )
            if minimum_rows < minimum_valid_rows:
                return error_response(
                    step=step,
                    error="minimum_rows is below the approved ontology quality policy",
                    recoverable=False,
                    instruction="Stop before Grafana Query; use the approved minimum valid row requirement.",
                    evidence={"rejection_codes": ["QUALITY_POLICY_VIOLATION"], "downstream_call_counts": {"grafana_query": 0, "sandbox": 0, "dashboard_write": 0}},
                )
        filter_fields = list(population_filter) if isinstance(population_filter, dict) else []
        for field_name in filter_fields:
            if field_name not in selected_fields:
                selected_fields.append(field_name)
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
        # A new plan revision must never overwrite the plan of an existing frame.
        run_id = ARTIFACTS.create_run(context)
        plan = {
            "dataset_id": metadata.get("dataset_id"),
            "field_views": field_views,
            "datasource_uid": metadata.get("datasource_uid"),
            "datasource_type": metadata.get("datasource_type"),
            "query_language": "csv",
            "upload_session_id": metadata.get("session_id") if str(metadata.get("dataset_id") or "").startswith("upload_") else None,
            "selected_fields": selected_fields,
            "grafana_query": query,
            "time_range": time_range,
            "analysis_input_contract": {"required_fields": selected_fields, "optional_fields": [], "validity_rules": validity_rules, "minimum_rows": minimum_rows, "maximum_rows": maximum_rows, "maximum_fields": MAX_PLAN_FIELDS, "maximum_response_bytes": MAX_PLAN_RESPONSE_BYTES},
            "provenance": {"dataset_metadata_ref": metadata_ref, "dataset_id": metadata.get("dataset_id"), "datasource_uid": metadata.get("datasource_uid"), "requested_fields": requested_fields, "selected_fields": selected_fields, "time_range": time_range},
        }
        if business_question is not None:
            plan["business_question"] = business_question
            plan["provenance"]["business_question"] = business_question
        if generic_upload_snapshot is not None:
            expected_rows = upload_metadata["rows"]
            if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or not minimum_rows <= expected_rows <= maximum_rows:
                raise workflow_node.WorkflowContractError("row limits cannot cover the complete uploaded dataset")
            plan["analysis_input_contract"]["expected_rows"] = expected_rows
        if semantic_validation is not None and isinstance(analysis_contract, dict):
            plan["ontology"] = semantic_validation["snapshot"]
            plan["analysis_contract"] = {
                **analysis_contract,
                "included_fields": semantic_validation["included_fields"],
                "excluded_fields": semantic_validation["excluded_fields"],
                "field_views": semantic_validation.get("field_views", []),
                "interpretation": "predictive_association_not_causation",
            }
            plan["field_views"] = semantic_validation.get("field_views", field_views)
            plan["analysis_input_contract"]["ontology_snapshot_sha256"] = semantic_validation["snapshot"]["sha256"]
            plan["analysis_input_contract"]["analysis_kind"] = analysis_contract.get("task_kind") or analysis_contract.get("kind")
            plan["analysis_input_contract"]["execution_template"] = execution_template_for_contract(analysis_contract)
            plan["analysis_input_contract"]["preprocessing_fit_scope"] = analysis_contract["split"]["preprocessing_fit_scope"]
            plan["analysis_input_contract"]["missing_value_policy"] = dict(semantic_validation.get("missing_value_policy") or {"mode": "reject", "approved": False})
            if analysis_contract.get("autotune") or analysis_contract.get("search_budget") is not None:
                plan["analysis_input_contract"]["autoresearch"] = {
                    "objective": analysis_contract.get("objective", "mae" if analysis_contract.get("task_kind") == "regression" else "roc_auc"),
                    "objective_minimum": analysis_contract.get("objective_minimum"),
                    "search_budget": analysis_contract.get("search_budget", 20),
                    "max_search_budget": 40,
                }
            plan["provenance"].update({"ontology_snapshot_id": semantic_validation["snapshot"]["snapshot_id"], "ontology_snapshot_sha256": semantic_validation["snapshot"]["sha256"]})
            plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        elif generic_upload_snapshot is not None:
            plan["ontology"] = generic_upload_snapshot
            plan["analysis_input_contract"]["ontology_snapshot_sha256"] = generic_upload_snapshot["sha256"]
            plan["provenance"].update({"ontology_snapshot_id": generic_upload_snapshot["snapshot_id"], "ontology_snapshot_sha256": generic_upload_snapshot["sha256"]})
            plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        plan_ref = ARTIFACTS.write_json(context, run_id, "query-plan", plan)
    except ArtifactAuthError as exc:
        return error_response(step=step, error=f"unauthorized artifact access: {exc}", recoverable=False, instruction="Stop; dataset metadata context mismatch.")
    except (workflow_node.WorkflowContractError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; query plan validation failed.")
    return success_response(step=step, run_id=run_id, refs={"dataset_metadata_ref": metadata_ref, "plan_ref": plan_ref}, instruction="The safe bounded query plan is ready. Execute it through Grafana Query only after the user confirms the analysis preview; there is no domain-analysis next step in this plan.", evidence={"datasource_query_executed": False, "selected_fields": selected_fields, "ontology": plan.get("ontology"), "plan_sha256": plan.get("plan_sha256")}, plan_ref=plan_ref, dataset_id=plan["dataset_id"], datasource_uid=plan["datasource_uid"], selected_fields=selected_fields, validation={"ok": True, "minimum_rows": minimum_rows, "maximum_rows": maximum_rows, "time_range": time_range})


def _wferp_dataset_metadata(args: dict[str, Any]) -> tuple[dict[str, str], str, dict[str, Any]]:
    context = context_from_args(args)
    metadata_ref = args.get("dataset_metadata_ref")
    if not isinstance(metadata_ref, str):
        raise workflow_node.WorkflowContractError("dataset_metadata_ref is required")
    run_id, parts = parse_artifact_ref(metadata_ref)
    if parts != ("dataset-metadata",):
        raise workflow_node.WorkflowContractError("dataset_metadata_ref must reference dataset-metadata")
    metadata = ARTIFACTS.read_json(context, metadata_ref)
    if not isinstance(metadata, dict) or metadata.get("dataset_id") != "wferp" or metadata.get("query_kind") != "wferp_llm_sql":
        raise workflow_node.WorkflowContractError("dataset_metadata_ref is not an authorized WFERP dataset")
    return context, run_id, metadata


def tool_search_wferp_schema(args: dict[str, Any]) -> dict[str, Any]:
    step = "search_wferp_schema"
    try:
        context, _, metadata = _wferp_dataset_metadata(args)
        top_k = int(args.get("top_k", wferp_sql.MAX_CONTEXT_TABLES))
        ontology_snapshot = ontology_contract.load_snapshot(dataset_id="wferp")
        schema_context = wferp_sql.build_context(str(args.get("prompt") or ""), WFERP_METADATA, top_k=top_k, ontology_snapshot=ontology_snapshot)
        run_id = ARTIFACTS.create_run(context)
        context_ref = ARTIFACTS.write_json(context, run_id, "schema-context", schema_context)
    except (ArtifactAuthError, workflow_node.WorkflowContractError, RuntimeError, ValueError, TypeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=True, instruction="Revise the ERP search terms or inspect the authorized WFERP dataset first.")
    return success_response(
        step=step,
        run_id=run_id,
        refs={"schema_context_ref": context_ref},
        instruction="Author one legacy-compatible MSSQL SELECT using only this bounded schema context, then submit it to plan_wferp_query. Do not execute it directly.",
        evidence={"datasource_query_executed": False, "candidate_table_count": len(schema_context["tables"]), "lexical_seed_tables": schema_context["lexical_seed_tables"], "approved_relation_count": len(schema_context["relationships"])},
        dataset_id=metadata["dataset_id"],
        schema_context=schema_context,
    )


def tool_plan_wferp_query(args: dict[str, Any]) -> dict[str, Any]:
    step = "plan_wferp_query"
    prompt, sql, output_fields = args.get("prompt"), args.get("sql"), args.get("output_fields")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > wferp_sql.MAX_PROMPT_BYTES:
        return error_response(step=step, error="prompt is required and must be bounded", recoverable=True, instruction="Resubmit the original bounded user request.")
    if not isinstance(sql, str):
        return error_response(step=step, error="sql is required", recoverable=True, instruction="Author one SELECT from the bounded WFERP schema context.")
    if not isinstance(output_fields, list) or not output_fields or len(output_fields) > MAX_PLAN_FIELDS or any(not isinstance(field, str) or not field for field in output_fields) or len(set(output_fields)) != len(output_fields):
        return error_response(step=step, error=f"output_fields must contain 1-{MAX_PLAN_FIELDS} unique result column names", recoverable=True, instruction="List the exact aliases/names returned by the SELECT projection.")
    try:
        minimum_rows = int(args.get("minimum_rows", 0))
        maximum_rows = int(args.get("maximum_rows", MAX_PLAN_ROWS))
    except (TypeError, ValueError):
        minimum_rows, maximum_rows = -1, -1
    if minimum_rows < 0 or maximum_rows < max(1, minimum_rows) or maximum_rows > MAX_PLAN_ROWS:
        return error_response(step=step, error=f"row bounds must satisfy 0 <= minimum_rows <= maximum_rows <= {MAX_PLAN_ROWS}", recoverable=True, instruction="Provide bounded result validation requirements.")
    try:
        context, _, metadata = _wferp_dataset_metadata(args)
        wferp_ontology = ontology_contract.load_snapshot(dataset_id="wferp")
        validation = wferp_sql.validate_llm_sql(prompt, sql, WFERP_METADATA, wferp_ontology)
        if not validation["ok"]:
            return error_response(step=step, error=validation["code"], recoverable=True, instruction=validation["repair_hint"], evidence={"datasource_query_executed": False})
        ref_id = str(args.get("refId") or "A")
        if not ref_id or len(ref_id) > 8:
            raise workflow_node.WorkflowContractError("refId is invalid")
        query = {
            "refId": ref_id,
            "datasource": {"uid": metadata["datasource_uid"], "type": metadata["datasource_type"]},
            "rawSql": sql.strip().rstrip(";"),
            "format": "table",
        }
        plan = {
            "dataset_id": "wferp",
            "business_question": prompt.strip(),
            "datasource_uid": metadata["datasource_uid"],
            "datasource_type": metadata["datasource_type"],
            "query_language": "mssql",
            "selected_fields": list(output_fields),
            "grafana_query": query,
            "time_range": {"from": "2000-01-01T00:00:00Z", "to": "2000-12-31T23:59:59Z"},
            "analysis_input_contract": {"required_fields": list(output_fields), "optional_fields": [], "validity_rules": [], "minimum_rows": minimum_rows, "maximum_rows": maximum_rows, "maximum_fields": MAX_PLAN_FIELDS, "maximum_response_bytes": MAX_PLAN_RESPONSE_BYTES},
            "ontology": ontology_contract.snapshot_identity(wferp_ontology),
            "provenance": {
                "dataset_metadata_ref": args["dataset_metadata_ref"],
                "dataset_id": "wferp",
                "datasource_uid": metadata["datasource_uid"],
                "sql_author": "ask-o11y-llm",
                "sql_sha256": hashlib.sha256(query["rawSql"].encode()).hexdigest(),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "business_question": prompt.strip(),
                "validated_tables": validation["tables"],
            },
            "validation_input": {"prompt": prompt},
        }
        plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        run_id = ARTIFACTS.create_run(context)
        plan_ref = ARTIFACTS.write_json(context, run_id, "query-plan", plan)
    except (ArtifactAuthError, workflow_node.WorkflowContractError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; WFERP query plan could not be authorized or persisted.")
    return success_response(
        step=step,
        run_id=run_id,
        refs={"dataset_metadata_ref": args["dataset_metadata_ref"], "plan_ref": plan_ref},
        instruction="Show the accepted SQL and validated tables in the Analysis Preview. Execute this opaque plan_ref through Grafana Query only after explicit user confirmation.",
        evidence={"datasource_query_executed": False, "validation": "OK", "validated_tables": validation["tables"]},
        plan_ref=plan_ref,
        dataset_id="wferp",
        datasource_uid=metadata["datasource_uid"],
        selected_fields=list(output_fields),
        accepted_sql=query["rawSql"],
    )


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
            elif plan.get("query_language") == "mssql" and plan.get("dataset_id") == "wferp":
                wferp_ontology = ontology_contract.load_snapshot(dataset_id="wferp")
                validation = wferp_sql.validate_llm_sql(str((plan.get("validation_input") or {}).get("prompt") or ""), str(q.get("rawSql") or ""), WFERP_METADATA, wferp_ontology)
                if not validation["ok"]:
                    problems.append(validation["code"])
                if q.get("datasource", {}).get("uid") != plan.get("datasource_uid") or q.get("datasource", {}).get("type") != "mssql":
                    problems.append("WFERP query datasource does not match the authorized plan")
            else:
                problems.append("unsupported planned query type")
        except (ArtifactAuthError, workflow_node.WorkflowContractError, OSError, TypeError, KeyError) as exc:
            problems.append(str(exc))
    else:
        problems.append("plan_ref is required")
    return {"ok": not problems, "errors": problems}


TOOLS = [
    {
    "name": "plan_query",
    "description": "Compile and validate a safe bounded Grafana query plan from an opaque inspected dataset_metadata_ref plus explicit fields and bounds. For ontology-assisted analysis, the deterministic gate validates the complete analysis_contract and pins its approved snapshot/hash before returning an executable plan.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dataset_metadata_ref": {
                "type": "string"
            },
            "selected_fields": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "minItems": 1,
                "maxItems": 200,
                "uniqueItems": True
            },
            "minimum_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000
            },
            "maximum_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000,
                "default": 100000
            },
            "business_question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "The exact confirmed decision question retained in the immutable plan; it is not a method or authorization token."
            },
            "refId": {
                "type": "string",
                "default": "A"
            },
            "analysis_contract": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_kind": {
                        "type": "string",
                        "enum": [
                            "binary_classification",
                            "regression"
                        ],
                        "default": "binary_classification"
                    },
                    "algorithms": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "enum": [
                                "dummy",
                                "ridge",
                                "random_forest",
                                "extra_trees",
                                "hist_gradient_boosting",
                                "logistic_regression",
                                "random_forest_shap",
                                "gradient_boosting",
                                "catboost",
                                "xgboost"
                            ]
                        }
                    },
                    "analysis_mode": {
                        "type": "string",
                        "enum": ["retrospective_association", "forward_prediction"]
                    },
                    "include_treatment_candidates": {
                        "type": "boolean",
                        "default": False,
                        "description": "Explicitly opt in to using ontology treatment_candidate fields as model features; this does not authorize operational changes."
                    },
                    "target_direction": {
                        "type": "string",
                        "enum": ["minimize", "maximize"],
                        "description": "Legacy business target direction, NOT the metric direction. Prefer optimization.direction; omit for pure prediction."
                    },
                    "optimization": {
                        "type": "object", "additionalProperties": False,
                        "description": "Only for explicitly requested business optimization, never inferred from the metric.",
                        "properties": {"direction": {"type": "string", "enum": ["minimize", "maximize"]}},
                        "required": ["direction"]
                    },
                    "controllable_fields": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string"}
                    },
                    "context_fields": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string"}
                    },
                    "forbidden_fields": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string"}
                    },
                    "population_filter": {
                        "type": "object",
                        "description": "Exact scalar equality filters applied to the authorized frame before split; filter fields are not model features.",
                        "additionalProperties": {"type": ["string", "number", "boolean"]}
                    },
                    "feature_sets": {
                        "type": "array",
                        "description": "Optional nested feature-set comparison. Every set shares the parent split and one global search budget across all sets/models. Select by training CV only; holdout is evaluated only for the locked winner.",
                        "minItems": 1,
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"},
                                "features": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
                                "controllable_fields": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                                "context_fields": {"type": "array", "uniqueItems": True, "items": {"type": "string"}}
                            },
                            "required": ["id", "features", "controllable_fields", "context_fields"]
                        }
                    },
                    "constrained_search": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "enabled": {"type": "boolean", "default": False},
                            "minimum_support": {"type": "integer", "minimum": 2, "maximum": 100000},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                            "fixed_context": {"type": "object"},
                            "support_group_fields": {
                                "type": "array",
                                "description": "Optional observed-support grouping fields. Every item must come from context_fields (for example source, position, or batch), never controllable_fields.",
                                "uniqueItems": True,
                                "items": {"type": "string"}
                            },
                            "bounds": {
                                "type": "object",
                                "description": "Observed or explicitly user-approved numeric bounds for controllable fields. Use an empty object when bounds are unavailable; never guess sentinel or infinite limits.",
                                "additionalProperties": {
                                    "type": "array",
                                    "prefixItems": [{"type": "number"}, {"type": "number"}],
                                    "minItems": 2,
                                    "maxItems": 2
                                }
                            }
                        },
                        "required": ["enabled", "minimum_support", "top_k", "bounds"]
                    },
                    "kind": {
                        "enum": [
                            "catboost",
                            "random_forest_shap",
                            "gradient_boosting",
                            "logistic_regression",
                            "xgboost"
                        ]
                    },
                    "dataset_id": {
                        "type": "string"
                    },
                    "target": {
                        "type": "string"
                    },
                    "features": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 200,
                        "uniqueItems": True,
                        "items": {
                            "type": "string"
                        }
                    },
                    "as_of": {
                        "type": "string",
                        "format": "date"
                    },
                    "split": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "stratified_holdout",
                                    "chronological_holdout",
                                    "grouped_holdout"
                                ]
                            },
                            "test_fraction": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 0.5
                            },
                            "preprocessing_fit_scope": {
                                "const": "training_only"
                            },
                            "time_field": {
                                "type": "string"
                            },
                            "group_field": {
                                "type": "string"
                            },
                            "seed": {
                                "type": "integer"
                            }
                        },
                        "required": [
                            "kind",
                            "test_fraction",
                            "preprocessing_fit_scope"
                        ]
                    },
                    "seed": {
                        "type": "integer"
                    },
                    "ontology_snapshot_sha256": {
                        "type": "string"
                    },
                    "quality_filter": {
                        "type": "object"
                    },
                    "class_imbalance_strategy": {
                        "type": "string",
                        "enum": [
                            "balanced",
                            "none"
                        ]
                    },
                    "sample_weight_fields": {"type": "array", "maxItems": 0, "description": "No sample-weight execution is supported; omit or pass an empty array."},
                    "positive_class": {
                        "type": "string",
                        "maxLength": 64
                    },
                    "purpose": {
                        "type": "string",
                        "maxLength": 512
                    },
                    "conclusion": {
                        "type": "string",
                        "maxLength": 512
                    },
                    "autotune": {
                        "type": "boolean",
                        "default": False
                    },
                    "objective": {
                        "type": "string",
                        "enum": [
                            "accuracy",
                            "roc_auc",
                            "pr_auc",
                            "mae"
                        ],
                        "default": "roc_auc"
                    },
                    "objective_minimum": {
                        "type": "number",
                        "minimum": 0
                    },
                    "search_budget": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 40,
                        "default": 20
                    },
                    "cost_matrix": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "false_negative": {
                                "type": "number",
                                "exclusiveMinimum": 0
                            },
                            "false_positive": {
                                "type": "number",
                                "exclusiveMinimum": 0
                            }
                        },
                        "required": [
                            "false_negative",
                            "false_positive"
                        ]
                    },
                    "cost_matrix_approved": {
                        "type": "boolean",
                        "default": False
                    },
                    "reporting_denominator": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000000,
                        "default": 1000
                    },
                    "minimum_recall": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1
                    },
                    "missing_value_policy": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "mode": {"type": "string", "enum": ["reject", "drop_invalid_target_split"]},
                            "approved": {"type": "boolean"}
                        },
                        "required": ["mode", "approved"],
                        "description": "Defaults to reject. Only {mode: drop_invalid_target_split, approved: true} may exclude rows invalid in the target or split field; never drops feature-only missing rows."
                    }
                },
                "required": [
                    "dataset_id",
                    "target",
                    "features",
                    "split",
                    "seed",
                    "ontology_snapshot_sha256"
                ],
                "allOf": [
                    {
                        "if": {"properties": {"task_kind": {"const": "regression"}}, "required": ["task_kind"]},
                        "then": {"required": ["algorithms", "controllable_fields", "context_fields", "forbidden_fields"]},
                        "else": {"required": ["kind", "as_of"]}
                    }
                ]
            }
        },
        "required": [
            "dataset_metadata_ref",
            "selected_fields"
        ]
    }
},
    {"name": "search_wferp_schema", "description": "Build a bounded multilingual WFERP table/column/relationship context from an authorized WFERP dataset_metadata_ref and the user's exact request. Ask O11y uses this context to author SQL; this tool does not generate or execute SQL.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_metadata_ref": {"type": "string"}, "prompt": {"type": "string", "minLength": 1, "maxLength": 8192}, "top_k": {"type": "integer", "minimum": 1, "maximum": 8, "default": 8}}, "required": ["dataset_metadata_ref", "prompt"]}},
    {"name": "plan_wferp_query", "description": "Validate one Ask O11y LLM-authored legacy MSSQL SELECT against the authorized WFERP schema, SQL policy, and explicit prompt constraints. On recoverable failure, revise the SQL using the returned repair hint. On success, returns an opaque plan_ref for later Grafana-only execution.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_metadata_ref": {"type": "string"}, "prompt": {"type": "string", "minLength": 1, "maxLength": 8192}, "sql": {"type": "string", "minLength": 1, "maxLength": 32768}, "output_fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200, "uniqueItems": True}, "minimum_rows": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0}, "maximum_rows": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 100000}, "refId": {"type": "string", "default": "A"}}, "required": ["dataset_metadata_ref", "prompt", "sql", "output_fields"]}},
    {"name": "validate_query", "description": "Revalidate an authorized opaque query plan without executing it.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"plan_ref": {"type": "string"}}, "required": ["plan_ref"]}},
]

HANDLERS = {"plan_query": tool_plan_query, "search_wferp_schema": tool_search_wferp_schema, "plan_wferp_query": tool_plan_wferp_query, "validate_query": tool_validate_query}


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
    # Keep dataset fixtures out of the production request handlers.
    import runpy

    runpy.run_path(str(ROOT / "scripts/check-query-planner.py"), run_name="__main__")

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
