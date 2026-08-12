#!/usr/bin/env python3
"""Real Grafana WFERP LLM-SQL planner/executor contract check."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
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
    os.environ.setdefault("GRAFANA_URL", "http://127.0.0.1:3000")
    os.environ.setdefault("GRAFANA_USER", "admin")
    os.environ.setdefault("GRAFANA_PASSWORD", "admin")
    planner = load("wferp_e2e_planner", ROOT / "data-query-planner-mcp" / "server.py")
    query = load("wferp_e2e_query", ROOT / "grafana-query-mcp" / "server.py")
    context = {"org_id": "1", "user_id": "wferp-e2e"}

    discovered = query.tool_discover_datasets({"_server_context": context})
    inspected = query.tool_inspect_dataset({"dataset_id": "wferp", "_server_context": context})
    if not discovered.get("ok") or not inspected.get("ok"):
        raise RuntimeError(json.dumps({"discover": discovered, "inspect": inspected}, ensure_ascii=False))
    metadata_ref = inspected["dataset_metadata_ref"]
    searched = planner.tool_search_wferp_schema({"dataset_metadata_ref": metadata_ref, "prompt": "查詢 ACTMK 2026 年工程預算明細 MK005 MK006 MK007", "top_k": 8, "_server_context": context})
    if not searched.get("ok") or "ACTMK" not in {table["id"] for table in searched["schema_context"]["tables"]}:
        raise RuntimeError(json.dumps(searched, ensure_ascii=False))

    cases = [
        (
            "single",
            "查詢 2026 年各期預算與已耗預算",
            "SELECT [MK].[MK005] AS [period], [MK].[MK006] AS [budget], [MK].[MK007] AS [used] FROM [wferp_test].[dbo].[ACTMK] AS MK WHERE [MK].[MK002] = '2026' ORDER BY [MK].[MK005]",
            ["period", "budget", "used"],
            12,
        ),
        (
            "join",
            "查詢 2026 年工程預算名稱與年度預算",
            "SELECT [MI].[MI002] AS [budget_name], [MJ].[MJ007] AS [annual_budget] FROM [wferp_test].[dbo].[ACTMJ] AS MJ JOIN [wferp_test].[dbo].[ACTMI] AS MI ON [MI].[MI001] = [MJ].[MJ001] WHERE [MJ].[MJ002] = '2026'",
            ["budget_name", "annual_budget"],
            1,
        ),
        (
            "aggregate",
            "彙總 2026 年預算與已耗預算",
            "SELECT SUM([MK].[MK006]) AS [total_budget], SUM([MK].[MK007]) AS [total_used] FROM [wferp_test].[dbo].[ACTMK] AS MK WHERE [MK].[MK002] = '2026'",
            ["total_budget", "total_used"],
            1,
        ),
    ]
    evidence = []
    for name, prompt, sql, fields, expected_rows in cases:
        planned = planner.tool_plan_wferp_query({"dataset_metadata_ref": metadata_ref, "prompt": prompt, "sql": sql, "output_fields": fields, "minimum_rows": expected_rows, "maximum_rows": expected_rows, "_server_context": context})
        if not planned.get("ok"):
            raise RuntimeError(json.dumps({name: planned}, ensure_ascii=False))
        executed = query.tool_execute_planned_query({"plan_ref": planned["plan_ref"], "_server_context": context})
        if not executed.get("ok") or executed.get("validation", {}).get("row_count") != expected_rows:
            raise RuntimeError(json.dumps({name: executed}, ensure_ascii=False))
        evidence.append({"case": name, "tables": planned["evidence"]["validated_tables"], "fields": executed["available_fields"], "rows": executed["validation"]["row_count"]})

    print(json.dumps({"ok": True, "dataset": "wferp", "sql_author": "Ask O11y LLM", "executed_by": "Grafana /api/ds/query", "cases": evidence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
