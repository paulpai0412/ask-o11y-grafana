#!/usr/bin/env python3
"""Behavior check for Planner regression contracts and upload governance."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def csv_bytes() -> bytes:
    rows = ["date,batch,setpoint,load,source,proxy_reason,constant,target,all_missing"]
    for index in range(30):
        rows.append(f"2026-01-{index + 1:02d},b{index // 5},{10 + index % 4},{100 + index},s{index % 3},after-outcome,60,{300 - index * 1.5},")
    return ("\n".join(rows) + "\n").encode()


def main() -> int:
    uploads = load("regression_contract_uploads", ROOT / "uploaded_datasets.py")
    planner = load("regression_contract_planner", ROOT / "data-query-planner-mcp/server.py")
    with tempfile.TemporaryDirectory() as tmp:
        upload_root = Path(tmp) / "uploads"
        artifacts = planner.ArtifactStore(Path(tmp) / "runs")
        setattr(uploads, "UPLOAD_ROOT", upload_root)
        setattr(planner.uploaded_datasets, "UPLOAD_ROOT", upload_root)
        setattr(planner, "ARTIFACTS", artifacts)
        context = {"org_id": "1", "user_id": "regression-contract", "session_id": "regression-contract-session"}
        uploaded = uploads.store_upload(context=context, session_id=context["session_id"], filename="regression.csv", raw=csv_bytes())
        dataset_id = uploaded["id"]
        hints = planner.upload_semantics.load_hints(upload_root / dataset_id)
        by_name = {field["physical_name"]: field for field in hints["fields"]}
        assert by_name["target"]["data_type"] in {"integer", "number"}
        assert by_name["target"]["distinct_count"] == 30
        assert by_name["setpoint"]["observed_min"] == 10.0 and by_name["setpoint"]["observed_max"] == 13.0
        assert by_name["constant"]["analysis_role"] == "constant"
        assert by_name["all_missing"]["missing_rate"] == 1.0

        run_id = artifacts.create_run(context)
        fields = uploaded["fields"]
        metadata_ref = artifacts.write_json(context, run_id, "dataset-metadata", {
            "dataset_id": dataset_id,
            "session_id": context["session_id"],
            "datasource_uid": "csv-poc",
            "datasource_type": "yesoreyeram-infinity-datasource",
            "fields": fields,
            "date_range": {"all_from": "2026-01-01", "all_to": "2026-01-30"},
            "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/upload.csv", "columns": [{"selector": field["name"]} for field in fields]},
        })
        base = {
            "task_kind": "regression",
            "algorithms": ["ridge", "extra_trees"],
            "dataset_id": dataset_id,
            "target": "target",
            "target_direction": "minimize",
            "features": ["setpoint", "load", "source"],
            "controllable_fields": ["setpoint"],
            "context_fields": ["date", "load", "source"],
            "forbidden_fields": ["target", "proxy_reason", "constant", "all_missing"],
            "split": {"kind": "chronological_holdout", "time_field": "date", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"},
            "seed": 42,
            "ontology_snapshot_sha256": uploaded["source_sha256"],
            "autotune": True,
            "objective": "mae",
            "search_budget": 6,
            "constrained_search": {"enabled": True, "minimum_support": 2, "top_k": 3, "support_group_fields": ["source"], "bounds": {"setpoint": [10, 13]}},
        }
        advisory = planner.upload_semantics.validate_analysis_contract(hints, base)
        assert advisory["target_resolution"] == {"field": "target", "analysis_role": "target", "source": "contract", "data_type": "number"}
        selected = ["date", "target", *base["features"]]
        planned = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": selected, "minimum_rows": 20, "analysis_contract": base, "_server_context": context})
        assert planned["ok"], planned
        plan = artifacts.read_json(context, planned["plan_ref"])
        assert plan["analysis_input_contract"]["execution_template"] == "ask_o11y_regression_v1"
        assert plan["analysis_input_contract"]["autoresearch"] == {"objective": "mae", "objective_minimum": None, "search_budget": 6, "max_search_budget": 40}
        assert plan["analysis_contract"]["interpretation"] == "predictive_association_not_causation"
        assert plan["analysis_contract"]["missing_value_policy"] == {"mode": "reject", "approved": False}
        assert plan["analysis_input_contract"]["missing_value_policy"] == {"mode": "reject", "approved": False}

        approved_policy = {"mode": "drop_invalid_target_split", "approved": True}
        approved_base = {**base, "missing_value_policy": approved_policy}
        approved_plan_result = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": selected, "minimum_rows": 20, "analysis_contract": approved_base, "_server_context": context})
        assert approved_plan_result["ok"], approved_plan_result
        approved_plan = artifacts.read_json(context, approved_plan_result["plan_ref"])
        assert approved_plan["analysis_contract"]["missing_value_policy"] == approved_policy
        assert approved_plan["analysis_input_contract"]["missing_value_policy"] == approved_policy
        assert approved_plan["plan_sha256"] != plan["plan_sha256"]

        for unsafe_policy in (
            {"mode": "drop_invalid_target_split", "approved": False},
            {"mode": "drop_invalid_target_split"},
            {"mode": "reject", "approved": True},
        ):
            unsafe = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": selected, "minimum_rows": 20, "analysis_contract": {**base, "missing_value_policy": unsafe_policy}, "_server_context": context})
            assert not unsafe["ok"] and "missing_value_policy" in unsafe["error"], unsafe

        filtered = {**base, "features": ["setpoint", "load"], "context_fields": ["date", "load"], "population_filter": {"source": "s0"}, "constrained_search": {"enabled": False, "minimum_support": 2, "top_k": 3, "support_group_fields": [], "bounds": {}}}
        filtered_plan = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["date", "target", "setpoint", "load"], "minimum_rows": 20, "analysis_contract": filtered, "_server_context": context})
        assert filtered_plan["ok"], filtered_plan
        filtered_artifact = artifacts.read_json(context, filtered_plan["plan_ref"])
        assert filtered_artifact["analysis_contract"]["population_filter"] == {"source": "s0"}
        assert "source" in filtered_artifact["selected_fields"] and "source" not in filtered_artifact["analysis_contract"]["features"]

        def rejected(contract: dict, fields: list[str], code: str) -> None:
            result = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": fields, "minimum_rows": 20, "analysis_contract": contract, "_server_context": context})
            assert not result["ok"] and code in result["evidence"]["rejection_codes"], result
            assert result["evidence"]["downstream_call_counts"] == {"grafana_query": 0, "sandbox": 0, "dashboard_write": 0}

        rejected({**filtered, "population_filter": {"source": "unseen"}}, ["date", "target", "setpoint", "load"], "POPULATION_FILTER_VALUE_UNSEEN")
        rejected({**base, "split": {**base["split"], "kind": "stratified_holdout"}}, selected, "SPLIT_POLICY_VIOLATION")
        rejected({**base, "features": [*base["features"], "proxy_reason"]}, [*selected, "proxy_reason"], "LEAKAGE_FIELD_FORBIDDEN")
        rejected({**base, "target": "constant"}, ["date", "constant", *base["features"]], "TARGET_CONSTANT")
        rejected({**base, "target": "all_missing"}, ["date", "all_missing", *base["features"]], "TARGET_ALL_MISSING")
        rejected({**base, "controllable_fields": ["constant"]}, selected, "CONTROLLABLE_FIELD_FORBIDDEN")
        rejected({**base, "constrained_search": {**base["constrained_search"], "bounds": {"setpoint": [-1, 999]}}}, selected, "CONSTRAINED_BOUNDS_OUTSIDE_SUPPORT")
        rejected({**base, "constrained_search": {**base["constrained_search"], "support_group_fields": ["setpoint"]}}, selected, "CONSTRAINED_SEARCH_INVALID")

        grouped = {**base, "context_fields": ["batch", "load", "source"], "split": {"kind": "grouped_holdout", "group_field": "batch", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"}}
        grouped_fields = ["batch", "target", *base["features"]]
        grouped_plan = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": grouped_fields, "minimum_rows": 20, "analysis_contract": grouped, "_server_context": context})
        assert grouped_plan["ok"], grouped_plan

    schema = next(tool["inputSchema"] for tool in planner.TOOLS if tool["name"] == "plan_query")["properties"]["analysis_contract"]
    properties = schema["properties"]
    for name in ("task_kind", "algorithms", "target_direction", "controllable_fields", "context_fields", "forbidden_fields", "constrained_search", "missing_value_policy"):
        assert name in properties
    assert properties["missing_value_policy"]["properties"]["mode"]["enum"] == ["reject", "drop_invalid_target_split"]
    assert properties["missing_value_policy"]["required"] == ["mode", "approved"]
    assert properties["task_kind"]["enum"] == ["binary_classification", "regression"]
    assert "support_group_fields" in properties["constrained_search"]["properties"]

    print("ok: regression contract + upload governance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
