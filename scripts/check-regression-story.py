#!/usr/bin/env python3
"""TDD check for generic regression feature-set story contracts/templates."""
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


def main() -> int:
    ontology = load("story_ontology", ROOT / "ontology_contract.py")
    snapshot = ontology.load_snapshot(dataset_id="u1-operating-daily")
    identity = ontology.snapshot_identity(snapshot)
    dataset = ontology.find_dataset(snapshot, "u1-operating-daily")
    if dataset is None:
        raise AssertionError("U1 snapshot missing")
    approved = list(dataset["approved_features"])
    treatments = [field["physical_name"] for field in dataset["fields"] if field.get("analysis_role") == "treatment_candidate"]
    sets = [
        {"id": "approved_context", "features": approved, "controllable_fields": [], "context_fields": approved},
        {"id": "approved_plus_treatment", "features": [*approved, *treatments], "controllable_fields": treatments, "context_fields": approved},
        {"id": "treatment_only", "features": treatments, "controllable_fields": treatments, "context_fields": []},
    ]
    base = {
        "task_kind": "regression", "analysis_mode": "retrospective_association", "dataset_id": dataset["physical_id"],
        "target": dataset["target"], "target_direction": "minimize", "features": [*approved, *treatments],
        "controllable_fields": treatments, "context_fields": approved,
        "forbidden_fields": [], "include_treatment_candidates": True, "algorithms": ["dummy", "ridge"],
        "feature_sets": sets, "split": dataset["split_policy"], "seed": dataset["split_policy"]["seed"],
        "ontology_snapshot_sha256": identity["sha256"], "quality_filter": dataset["quality_policy"], "autotune": True,
        "objective": "mae", "search_budget": 4,
    }
    result = ontology.validate_analysis_contract(snapshot, base)
    assert result["conforms"], result
    assert result["feature_set_count"] == 3
    assert result["feature_set_ids"] == ["approved_context", "approved_plus_treatment", "treatment_only"]
    try:
        bad = json.loads(json.dumps(base))
    except (TypeError, json.JSONDecodeError) as exc:
        raise AssertionError("story fixture could not be cloned") from exc
    bad["feature_sets"][0]["features"].append("raw_coal_consumption_g")
    bad_result = ontology.validate_analysis_contract(snapshot, bad)
    assert not bad_result["conforms"] and "FIELD_ROLE_FORBIDDEN" in bad_result["rejection_codes"]

    server = load("story_server", ROOT / "sandbox-analysis-mcp/server.py")
    plan = {"analysis_contract": base, "analysis_input_contract": {"autoresearch": {"objective": "mae", "search_budget": 4}}}
    template = server.compose_regression_template(plan, base, 42)
    no_autotune_plan = {"analysis_contract": {**base, "autotune": False}, "analysis_input_contract": {}}
    no_autotune_template = server.compose_regression_template(no_autotune_plan, no_autotune_plan["analysis_contract"], 42)
    assert "BUDGET = 4" in no_autotune_template
    for literal in ("FEATURE_SETS", "feature_set_comparison", "feature_set_ids", "run_multi_model_regression", "sample_weight_fields"):
        assert literal in template, literal

    with tempfile.TemporaryDirectory() as tmp:
        setattr(server, "ARTIFACTS", server.ArtifactStore(Path(tmp) / "runs"))
        assert server.validate_ml_execution_contract({"execution_template": "ask_o11y_regression_v1", "preprocessing_fit_scope": "training_only", "autoresearch": {"objective": "mae", "search_budget": 4}}, base)["execution_template"] == "ask_o11y_regression_v1"

    planner = load("story_planner", ROOT / "data-query-planner-mcp/server.py")
    schema = next(tool["inputSchema"] for tool in planner.TOOLS if tool["name"] == "plan_query")["properties"]["analysis_contract"]
    assert "feature_sets" in schema["properties"]
    assert schema["properties"]["feature_sets"]["maxItems"] == 6

    print("ok: regression feature-set story contract/template")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
