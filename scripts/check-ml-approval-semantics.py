#!/usr/bin/env python3
"""TDD check: a supplied cost matrix is not equivalent to business approval."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sandbox-analysis-mcp"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def contract(approved: bool | None = None) -> dict:
    value = {
        "kind": "xgboost", "target": "churn", "features": ["tenure"], "positive_class": "Yes",
        "split": {"kind": "stratified_holdout", "test_fraction": 0.2},
        "cost_matrix": {"false_negative": 3.0, "false_positive": 1.0},
        "purpose": "测试", "conclusion": "测试完成",
    }
    if approved is not None:
        value["cost_matrix_approved"] = approved
    return value


def main() -> int:
    server = load("approval_server_check", ROOT / "sandbox-analysis-mcp/server.py")
    planner = load("approval_planner_check", ROOT / "data-query-planner-mcp/server.py")
    plan = {
        "dataset_id": "d", "plan_sha256": "b" * 64,
        "ontology": {"snapshot_id": "s", "sha256": "a" * 64},
        "analysis_contract": {"field_views": []},
        "analysis_input_contract": {"autoresearch": {"search_budget": 2, "objective": "pr_auc"}},
    }
    assumed = server.compose_ml_template(plan, contract(), 42)
    approved = server.compose_ml_template(plan, contract(True), 42)
    if "COST_APPROVED = False" not in assumed or "COST_APPROVED = True" not in approved:
        raise AssertionError("cost approval is not explicit in the trusted template")
    if '"threshold_cost_approved": COST_APPROVED' not in assumed or "bool(COST_MATRIX)" in assumed:
        raise AssertionError("template still equates cost presence with approval")

    tool = next(item for item in planner.TOOLS if item["name"] == "plan_query")
    analysis_contract = tool["inputSchema"]["properties"]["analysis_contract"]
    properties = analysis_contract["properties"]
    if properties.get("cost_matrix_approved") != {"type": "boolean", "default": False}:
        raise AssertionError("planner contract does not expose explicit cost approval")
    if properties.get("reporting_denominator") != {"type": "integer", "minimum": 1, "maximum": 1000000, "default": 1000}:
        raise AssertionError("planner contract lacks bounded reporting denominator")

    print("ok: cost assumption/approval and reporting denominator semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
