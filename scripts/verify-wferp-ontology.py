#!/usr/bin/env python3
"""Runnable verification report for the full WFERP ontology import and JOIN gate."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_json(path: Path):
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid verification JSON: {path}") from exc


def main() -> int:
    ontology = load_module("ontology_contract_verify", ROOT / "ontology_contract.py")
    wferp_sql = load_module("wferp_sql_verify", ROOT / "data-query-planner-mcp/wferp_sql.py")
    candidate = load_json(ROOT / "semantic/candidates/wferp.json")
    snapshot = ontology.load_snapshot(dataset_id="wferp")
    datasets = snapshot["registry"]["datasets"]
    relations = [relation for dataset in datasets for relation in dataset.get("relations", [])]
    approved_candidates = sum(item.get("status") == "approved" for item in candidate["datasets"]) + sum(field.get("status") == "approved" for item in candidate["datasets"] for field in item["fields"]) + sum(item.get("status") == "approved" for item in candidate["relations"])
    acpta = ontology.find_dataset(snapshot, "ACPTA")
    relation = ontology.find_relation(snapshot, "ACPTA", "ACPTB")
    if acpta is None or relation is None:
        raise RuntimeError("ACPTA/ACPTB ontology fixture is missing")
    single_sql = "SELECT [TA001], [TA003] FROM [wferp_test].[dbo].[ACPTA]"
    join_sql = "SELECT A.[TA001], B.[TB009] FROM [wferp_test].[dbo].[ACPTA] A JOIN [wferp_test].[dbo].[ACPTB] B ON A.[TA001]=B.[TB001] AND A.[TA002]=B.[TB002]"
    metadata = wferp_sql.load_metadata()
    single = wferp_sql.validate_llm_sql("ACPTA TA001 TA003", single_sql, metadata, snapshot)
    joined = wferp_sql.validate_llm_sql("ACPTA ACPTB TA001 TB009", join_sql, metadata, snapshot)
    checks = {
        "candidate_ir_counts": len(candidate["datasets"]) == 1369 and sum(len(item["fields"]) for item in candidate["datasets"]) == 32022 and len(candidate["relations"]) == 1178,
        "approved_candidates_zero": approved_candidates == 0,
        "snapshot_counts": len(datasets) == 1369 and sum(len(item["fields"]) for item in datasets) == 32022 and len(relations) >= len(candidate["relations"]),
        "approved_relation_scope_is_bounded": sum(relation.get("status") == "approved" and bool(relation.get("executable")) for relation in relations) == 2,
        "acpta_fields_resolve": {field["physical_name"] for field in acpta["fields"]}.issuperset({"TA001", "TA002", "TA003"}),
        "heuristic_relation_non_executable": relation["status"] == "proposed" and not bool(relation["executable"]),
        "single_table_ast_accepted": bool(single.get("ok")),
        "proposed_join_rejected": joined.get("code") == "JOIN_RELATION_NOT_APPROVED",
    }
    if not all(checks.values()):
        raise RuntimeError(json.dumps({"checks": checks, "single": single, "join": joined}, ensure_ascii=False))
    print(json.dumps({"ok": True, "snapshot": ontology.snapshot_identity(snapshot), "counts": {"datasets": len(datasets), "fields": sum(len(item["fields"]) for item in datasets), "relations": len(relations), "approved_candidates": approved_candidates}, "sample": {"dataset": "ACPTA", "field_ids": [field["physical_name"] for field in acpta["fields"][:5]], "relation": relation["canonical_id"]}, "planner_gate": {"single_table": "accepted", "proposed_join": joined["code"]}, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
