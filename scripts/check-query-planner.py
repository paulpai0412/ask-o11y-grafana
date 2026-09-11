#!/usr/bin/env python3
"""Isolated Planner checks; dataset-specific fixtures belong here, not in the server."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(planner):
    context = {"org_id": "1", "user_id": "planner-self-check", "session_id": "planner-self-check-session"}
    run_id = planner.ARTIFACTS.create_run(context)
    metadata_ref = planner.ARTIFACTS.write_json(context, run_id, "dataset-metadata", {"dataset_id": "self-check-dataset", "datasource_uid": "self-check", "datasource_type": "yesoreyeram-infinity-datasource", "fields": [{"name": "timestamp", "type": "date"}, {"name": "metric", "type": "number"}, {"name": "feature", "type": "number"}], "date_range": {"all_from": "2026-01-01", "all_to": "2026-12-31"}, "query_template": {"refId": "A", "datasource": {"uid": "self-check", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/data.csv", "parser": "backend", "columns": [{"selector": "timestamp", "text": "timestamp", "type": "timestamp"}, {"selector": "metric", "text": "metric", "type": "number"}, {"selector": "feature", "text": "feature", "type": "number"}]}})
    plan = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["timestamp", "metric", "feature"], "minimum_rows": 20, "business_question": "Compare the observed metric with its feature context", "_server_context": context})
    if not plan.get("ok") or plan.get("datasource_uid") != "self-check" or not plan.get("plan_ref", "").startswith("artifact://"):
        raise RuntimeError(str(plan))
    plan_artifact = planner.ARTIFACTS.read_json(context, plan["plan_ref"])
    if plan_artifact["analysis_input_contract"] != {"required_fields": ["timestamp", "metric", "feature"], "optional_fields": [], "validity_rules": [], "minimum_rows": 20, "maximum_rows": 100000, "maximum_fields": 200, "maximum_response_bytes": 52428800} or plan_artifact.get("time_range") != {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T23:59:59Z"} or plan_artifact.get("business_question") != "Compare the observed metric with its feature context" or plan_artifact.get("provenance", {}).get("business_question") != plan_artifact["business_question"]:
        raise RuntimeError(str(plan_artifact))
    if "next_step" in plan or "request" in plan_artifact:
        raise RuntimeError("query plan must not contain a fixed workflow or natural-language routing")
    invalid_field = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["missing"], "_server_context": context})
    natural_language = planner.tool_plan_query({"request": "fixed intent must not be routed", "_server_context": context})
    unsafe_question = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["timestamp"], "business_question": "<script>" + "x" * 10, "_server_context": context})
    if invalid_field.get("ok") or natural_language.get("ok") or unsafe_question.get("ok"):
        raise RuntimeError("invalid planner inputs must fail")
    validation = planner.tool_validate_query({"plan_ref": plan["plan_ref"], "_server_context": context})
    if not validation["ok"]:
        raise RuntimeError(str(validation))
    wide_names = [f"field_{index}" for index in range(planner.MAX_PLAN_FIELDS)]
    wide_run_id = planner.ARTIFACTS.create_run(context)
    wide_metadata_ref = planner.ARTIFACTS.write_json(context, wide_run_id, "dataset-metadata", {"dataset_id": "self-check-wide", "datasource_uid": "self-check", "datasource_type": "yesoreyeram-infinity-datasource", "fields": [{"name": name, "type": "number"} for name in wide_names], "date_range": {"all_from": "2000-01-01", "all_to": "2000-12-31"}, "query_template": {"refId": "A", "datasource": {"uid": "self-check", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/wide.csv", "parser": "backend", "columns": [{"selector": name, "text": name, "type": "number"} for name in wide_names]}})
    wide_plan = planner.tool_plan_query({"dataset_metadata_ref": wide_metadata_ref, "selected_fields": wide_names, "_server_context": context})
    too_wide = planner.tool_plan_query({"dataset_metadata_ref": wide_metadata_ref, "selected_fields": [*wide_names, "field_200"], "_server_context": context})
    if not wide_plan.get("ok") or too_wide.get("ok"):
        raise RuntimeError(f"200-field planner boundary failed: {wide_plan} {too_wide}")
    snapshot = planner.ontology_contract.load_snapshot(dataset_id="u1-operating-daily")
    identity = planner.ontology_contract.snapshot_identity(snapshot)
    u1_run_id = planner.ARTIFACTS.create_run(context)
    u1_names = ["date", "heat_rate", "avg_generation_mw", "main_steam_temp_c", "reheat_steam_temp_c", "scr_temp_c", "condenser_outlet_water_temp", "coal_avg_heat_value_kcal_kg", "raw_coal_consumption_g"]
    u1_fields = [{"name": name, "type": "date" if name == "date" else "number"} for name in u1_names] + [{"name": "heat_rate_valid", "type": "boolean", "validity_for": ["heat_rate"], "accepted_values": [True]}]
    u1_query_columns = [{"selector": item["name"], "text": item["name"], "type": "timestamp" if item["type"] == "date" else item["type"]} for item in u1_fields]
    u1_metadata_ref = planner.ARTIFACTS.write_json(context, u1_run_id, "dataset-metadata", {"dataset_id": "u1-operating-daily", "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource", "fields": u1_fields, "date_range": {"all_from": "2026-01-01", "all_to": "2026-12-31"}, "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/u1.csv", "parser": "backend", "columns": u1_query_columns}})
    u1_dataset = planner.ontology_contract.find_dataset(snapshot, "u1-operating-daily")
    if u1_dataset is None:
        raise RuntimeError("U1 ontology fixture is unavailable")
    safe_features = [name for name in u1_dataset["approved_features"] if name in u1_names]
    safe_contract = {
        "task_kind": "regression",
        "analysis_mode": "retrospective_association",
        "dataset_id": "u1-operating-daily",
        "target": "heat_rate",
        "target_direction": "minimize",
        "features": safe_features,
        "controllable_fields": [],
        "context_fields": ["date", *safe_features],
        "forbidden_fields": [field["physical_name"] for field in u1_dataset["fields"] if field["physical_name"] not in {"date", "heat_rate", *safe_features}],
        "include_treatment_candidates": False,
        "algorithms": ["dummy", "ridge"],
        "as_of": "2026-07-28",
        "split": {**u1_dataset["split_policy"], "preprocessing_fit_scope": "training_only"},
        "seed": 42,
        "ontology_snapshot_sha256": identity["sha256"],
        "quality_filter": u1_dataset["quality_policy"],
        "objective": "mae",
        "search_budget": 2,
    }
    safe_projection = ["date", "heat_rate", *safe_contract["features"]]
    safe_plan = planner.tool_plan_query({"dataset_metadata_ref": u1_metadata_ref, "selected_fields": safe_projection, "minimum_rows": 100, "analysis_contract": safe_contract, "business_question": "Which observed factors are associated with daily heat-rate differences?", "_server_context": context})
    if not safe_plan.get("ok"):
        raise RuntimeError(str(safe_plan))
    safe_artifact = planner.ARTIFACTS.read_json(context, safe_plan["plan_ref"])
    if safe_artifact.get("ontology", {}).get("sha256") != identity["sha256"] or not safe_artifact.get("plan_sha256") or safe_artifact.get("analysis_contract", {}).get("split", {}).get("kind") != "chronological_holdout" or safe_artifact.get("business_question") != "Which observed factors are associated with daily heat-rate differences?":
        raise RuntimeError("safe ontology plan did not pin the semantic contract or business question")
    unsafe = {
        "target_as_feature": {**safe_contract, "features": ["heat_rate"]},
        "unknown_feature": {**safe_contract, "features": ["missing_feature"]},
        "target_proxy": {**safe_contract, "features": ["raw_coal_consumption_g"]},
        "random_split": {**safe_contract, "split": {**safe_contract["split"], "kind": "random"}},
    }
    expected_codes = {"target_as_feature": "TARGET_USED_AS_FEATURE", "unknown_feature": "UNKNOWN_FIELD", "target_proxy": "TARGET_PROXY_UNRESOLVED", "random_split": "SPLIT_POLICY_VIOLATION"}
    negative_codes = {}
    for name, bad_contract in unsafe.items():
        result = planner.tool_plan_query({"dataset_metadata_ref": u1_metadata_ref, "selected_fields": safe_projection, "minimum_rows": 100, "analysis_contract": bad_contract, "_server_context": context})
        codes = result.get("evidence", {}).get("rejection_codes", [])
        if result.get("ok") or expected_codes[name] not in codes or result.get("evidence", {}).get("downstream_call_counts") != {"grafana_query": 0, "sandbox": 0, "dashboard_write": 0}:
            raise RuntimeError(f"unsafe semantic fixture escaped: {name} {result}")
        negative_codes[name] = codes
    wferp_context = planner.wferp_sql.build_context("科目/部門預算單身檔的已耗與可用預算", planner.WFERP_METADATA, top_k=8, ontology_snapshot=planner.ontology_contract.load_snapshot(dataset_id="wferp"))
    if not {"ACTMI", "ACTMJ", "ACTMK"}.issubset({table["id"] for table in wferp_context["tables"]}) or len(wferp_context["relationships"]) < 2:
        raise RuntimeError(str(wferp_context))
    wferp_ontology = planner.ontology_contract.load_snapshot(dataset_id="wferp")
    approved_relations = [relation for dataset in wferp_ontology["registry"]["datasets"] for relation in dataset.get("relations", []) if relation.get("status") == "approved" and bool(relation.get("executable"))]
    if not approved_relations:
        raise RuntimeError("WFERP ontology has no reviewed executable relation fixture")
    print(json.dumps({"ok": True, "generic_plan_ref": plan["plan_ref"], "ontology_plan_ref": safe_plan["plan_ref"], "ontology_snapshot_sha256": identity["sha256"], "runtime_tools": [tool["name"] for tool in planner.TOOLS], "wferp_context_tables": [table["id"] for table in wferp_context["tables"]], "wferp_ontology": {"snapshot": planner.ontology_contract.snapshot_identity(wferp_ontology), "datasets": len(wferp_ontology["registry"]["datasets"]), "approved_relations": len(approved_relations)}, "negative_checks": {"generic": ["invalid_field", "natural_language_routing"], "ontology": negative_codes}}, ensure_ascii=False, indent=2))


def main():
    source = (ROOT / "data-query-planner-mcp/server.py").read_text()
    for fixture_name in ("u1-operating-daily", "heat_rate", "ACTMI", "ACTMJ", "ACTMK"):
        if fixture_name in source:
            raise AssertionError(f"dataset-specific fixture leaked into Planner: {fixture_name}")
    spec = importlib.util.spec_from_file_location("planner_check", ROOT / "data-query-planner-mcp/server.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Planner module unavailable")
    planner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = planner
    spec.loader.exec_module(planner)
    with tempfile.TemporaryDirectory() as tmp:
        setattr(planner, "ARTIFACTS", planner.ArtifactStore(Path(tmp) / "runs"))
        check(planner)


if __name__ == "__main__":
    main()
