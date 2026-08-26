#!/usr/bin/env python3
"""Self-check for Workstream B: widened ML contracts and enforced fit scope."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("catboost", "random_forest_shap", "gradient_boosting", "logistic_regression", "xgboost")


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def research_estimator():
    research = load_module("kinds_autoresearch", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
    return research._estimator("random_forest_shap", 42)


def main() -> int:
    contract = load_module("ontology_contract", ROOT / "ontology_contract.py")
    planner = load_module("data_query_planner", ROOT / "data-query-planner-mcp/server.py")
    sandbox = load_module("sandbox_analysis", ROOT / "sandbox-analysis-mcp/server.py")
    snapshot = contract.load_snapshot(dataset_id="u1-operating-daily")
    identity = contract.snapshot_identity(snapshot)
    safe = {
        "kind": "random_forest_shap",
        "dataset_id": "u1-operating-daily",
        "target": "heat_rate",
        "features": ["avg_generation_mw", "main_steam_temp_c"],
        "as_of": "2026-07-28",
        "split": {
            "kind": "chronological_holdout",
            "time_field": "date",
            "test_fraction": 0.25,
            "preprocessing_fit_scope": "training_only",
        },
        "seed": 42,
        "ontology_snapshot_sha256": identity["sha256"],
    }

    for kind in KINDS:
        candidate = {**safe, "kind": kind}
        result = contract.validate_analysis_contract(snapshot, candidate)
        assert result["conforms"], f"{kind}: {result['rejection_codes']}"
        template = planner.execution_template_for_kind(kind)
        assert template == f"ask_o11y_{kind}_v1", template
        enforced = sandbox.validate_ml_execution_contract(
            {"execution_template": template, "preprocessing_fit_scope": "training_only"}, candidate,
        )
        assert enforced["preprocessing_fit_scope"] == "training_only"

    no_scope = {**safe, "split": {k: v for k, v in safe["split"].items() if k != "preprocessing_fit_scope"}}
    rejected = contract.validate_analysis_contract(snapshot, no_scope)
    assert not rejected["conforms"] and "SPLIT_POLICY_VIOLATION" in rejected["rejection_codes"]

    bad = contract.validate_analysis_contract(snapshot, {**safe, "kind": "arbitrary_python"})
    assert not bad["conforms"] and "ANALYSIS_CONTRACT_INVALID" in bad["rejection_codes"]
    try:
        sandbox.validate_ml_execution_contract({"execution_template": "ask_o11y_logistic_regression_v1"}, {**safe, "kind": "logistic_regression"})
    except sandbox.WorkflowContractError:
        pass
    else:
        raise AssertionError("sandbox accepted missing training-only fit scope")

    # Live MCP schema must advertise the same bounded enum.
    schema = next(tool["inputSchema"] for tool in planner.TOOLS if tool["name"] == "plan_query")
    contract_properties = schema["properties"]["analysis_contract"]["properties"]
    kind_schema = contract_properties["kind"]
    assert tuple(kind_schema["enum"]) == KINDS, kind_schema
    assert contract_properties["search_budget"]["maximum"] == 40
    split_schema = contract_properties["split"]
    assert split_schema["properties"]["preprocessing_fit_scope"]["const"] == "training_only"
    assert set(split_schema["required"]) == {"kind", "test_fraction", "preprocessing_fit_scope"}
    # RF estimator smoke test (requires numpy deps)
    # research_estimator() tested in check-cost-threshold.py with full deps

    enforced = sandbox.validate_ml_execution_contract({
        "execution_template": "ask_o11y_gradient_boosting_v1",
        "preprocessing_fit_scope": "training_only",
        "autoresearch": {"objective": "accuracy", "search_budget": 20},
    }, {**safe, "kind": "gradient_boosting"})
    assert enforced["autoresearch"]["search_budget"] == 20

    print("ok: contract kind enum + training-only fit scope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
