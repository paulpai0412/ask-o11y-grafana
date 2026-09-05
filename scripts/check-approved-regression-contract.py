#!/usr/bin/env python3
"""TDD check for regression contracts on an approved ontology snapshot."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def contract(dataset: dict, *, model: str) -> dict:
    fields = {field["physical_name"]: field for field in dataset["fields"]}
    approved = list(dataset["approved_features"])
    treatments = [field["physical_name"] for field in dataset["fields"] if field.get("analysis_role") == "treatment_candidate"]
    if model == "A":
        features, controllable, context = approved, [], approved
        include_treatments = False
    elif model == "B":
        features, controllable, context = [*approved, *treatments], treatments, approved
        include_treatments = True
    else:
        features, controllable, context = treatments, treatments, []
        include_treatments = True
    return {
        "task_kind": "regression",
        "analysis_mode": "retrospective_association",
        "dataset_id": dataset["physical_id"],
        "target": dataset["target"],
        "target_direction": "minimize",
        "features": features,
        "controllable_fields": controllable,
        "context_fields": context,
        "forbidden_fields": [field["physical_name"] for field in dataset["fields"] if field["physical_name"] not in {dataset["target"], dataset["time_identity"], *features}],
        "include_treatment_candidates": include_treatments,
        "algorithms": ["dummy", "ridge", "extra_trees"],
        "split": dataset["split_policy"],
        "seed": dataset["split_policy"]["seed"],
        "ontology_snapshot_sha256": "__SNAPSHOT__",
        "quality_filter": dataset["quality_policy"],
        "autotune": True,
        "objective": "mae",
        "search_budget": 6,
    }


def main() -> int:
    ontology = load("approved_regression_ontology", ROOT / "ontology_contract.py")
    snapshot = ontology.load_snapshot(dataset_id="u1-operating-daily")
    identity = ontology.snapshot_identity(snapshot)
    dataset = ontology.find_dataset(snapshot, "u1-operating-daily")
    if dataset is None:
        raise AssertionError("approved U1 dataset missing")
    for name in ("A", "B", "C"):
        candidate = contract(dataset, model=name)
        candidate["ontology_snapshot_sha256"] = identity["sha256"]
        result = ontology.validate_analysis_contract(snapshot, candidate)
        assert result["conforms"], f"Model {name} rejected: {result['rejection_codes']}"
        assert set(result["included_fields"]) == set(candidate["features"]), name
        assert result["analysis_mode"] == "retrospective_association"
        assert result["treatment_feature_count"] == (0 if name == "A" else len(candidate["controllable_fields"])), name

    rejected = contract(dataset, model="A")
    rejected["ontology_snapshot_sha256"] = identity["sha256"]
    rejected["features"] = [*rejected["features"], "burner_angle"]
    rejected["context_fields"] = [*rejected["context_fields"], "burner_angle"]
    rejected["forbidden_fields"] = [field for field in rejected["forbidden_fields"] if field != "burner_angle"]
    bad = ontology.validate_analysis_contract(snapshot, rejected)
    assert not bad["conforms"] and "TREATMENT_FEATURE_OPT_IN_REQUIRED" in bad["rejection_codes"]

    forward = contract(dataset, model="B")
    forward["ontology_snapshot_sha256"] = identity["sha256"]
    forward["analysis_mode"] = "forward_prediction"
    bad_forward = ontology.validate_analysis_contract(snapshot, forward)
    assert not bad_forward["conforms"] and "AVAILABILITY_UNKNOWN" in bad_forward["rejection_codes"]

    bad_split = contract(dataset, model="A")
    bad_split["ontology_snapshot_sha256"] = identity["sha256"]
    bad_split["split"] = {**dataset["split_policy"], "kind": "grouped_holdout"}
    invalid_split = ontology.validate_analysis_contract(snapshot, bad_split)
    assert not invalid_split["conforms"] and "SPLIT_POLICY_VIOLATION" in invalid_split["rejection_codes"]

    schema = load("approved_regression_planner", ROOT / "data-query-planner-mcp/server.py")
    contract_schema = next(tool["inputSchema"] for tool in schema.TOOLS if tool["name"] == "plan_query")["properties"]["analysis_contract"]
    for field in ("task_kind", "analysis_mode", "include_treatment_candidates", "algorithms", "controllable_fields", "context_fields", "forbidden_fields"):
        assert field in contract_schema["properties"], field
    assert "mae" in contract_schema["properties"]["objective"]["enum"]

    with tempfile.TemporaryDirectory() as tmp:
        setattr(schema, "ARTIFACTS", schema.ArtifactStore(Path(tmp) / "runs"))
        context = {"org_id": "1", "user_id": "approved-regression", "session_id": "approved-regression-session"}
        try:
            metadata = json.loads((ROOT / "data-query-planner-mcp/metadata/csv-poc.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AssertionError("U1 metadata fixture is unavailable") from exc
        fields = metadata["fields"]
        run_id = schema.ARTIFACTS.create_run(context)
        metadata_ref = schema.ARTIFACTS.write_json(context, run_id, "dataset-metadata", {
            "dataset_id": "u1-operating-daily", "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource",
            "fields": fields, "date_range": metadata["date_range"], "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/u1.csv", "columns": [{"selector": field["name"], "text": field["name"], "type": "timestamp" if field["type"] == "date" else field["type"]} for field in fields]},
        })
        for name in ("A", "B", "C"):
            candidate = contract(dataset, model=name)
            candidate["ontology_snapshot_sha256"] = identity["sha256"]
            selected = [dataset["time_identity"], dataset["target"], *candidate["features"]]
            planned = schema.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": selected, "minimum_rows": 100, "analysis_contract": candidate, "_server_context": context})
            assert planned["ok"], f"Model {name} planner rejected: {planned}"
            assert planned["selected_fields"][:len(selected)] == selected, name
            assert dataset["quality_policy"]["field"] in planned["selected_fields"]

    print("ok: approved snapshot regression Models A/B/C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
