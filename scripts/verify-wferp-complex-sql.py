#!/usr/bin/env python3
"""Execute data-driven WFERP JOIN/complex SQL fixtures through Planner and Grafana Query."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data-query-planner-mcp/metadata/wferp/complex-sql-fixtures.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid verification JSON: {path}") from exc


def first_row(frame_path: Path) -> list[Any]:
    frames = load_json(frame_path)
    fields = frames[0]["schema"]["fields"]
    values = frames[0]["data"]["values"]
    if not fields or not values:
        return []
    return [column[0] for column in values]


def comparable(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        try:
            return int(value)
        except (OverflowError, ValueError):
            return value
    return value.rstrip() if isinstance(value, str) else value


def main() -> int:
    os.environ.setdefault("GRAFANA_URL", "http://127.0.0.1:3000")
    os.environ.setdefault("GRAFANA_USER", "admin")
    os.environ.setdefault("GRAFANA_PASSWORD", "admin")
    planner = load_module("planner_complex_verify", ROOT / "data-query-planner-mcp/server.py")
    grafana = load_module("grafana_complex_verify", ROOT / "grafana-query-mcp/server.py")
    fixtures = load_json(FIXTURES)["fixtures"]
    dataset = load_json(ROOT / "data-query-planner-mcp/metadata/wferp/dataset.json")
    context = {"org_id": "1", "user_id": "wferp-complex-sql-verifier"}
    metadata_run = planner.ARTIFACTS.create_run(context)
    metadata_ref = planner.ARTIFACTS.write_json(context, metadata_run, "dataset-metadata", {"dataset_id": "wferp", "datasource_uid": dataset["datasource_uid"], "datasource_type": dataset["datasource_type"], "query_kind": "wferp_llm_sql"})
    results = []
    for fixture in fixtures:
        plan = planner.tool_plan_wferp_query({"dataset_metadata_ref": metadata_ref, "prompt": fixture["prompt"], "sql": fixture["sql"], "output_fields": fixture["output_fields"], "minimum_rows": 0, "maximum_rows": 100, "_server_context": context})
        rejection = fixture.get("expected_rejection")
        if rejection:
            if plan.get("ok") or plan.get("error") != rejection:
                raise RuntimeError(f"negative fixture escaped: {fixture['id']} {plan}")
            results.append({"id": fixture["id"], "planner": "rejected", "code": rejection, "grafana_query_calls": 0})
            continue
        if not plan.get("ok"):
            raise RuntimeError(f"Planner rejected valid fixture: {fixture['id']} {plan}")
        execution = grafana.tool_execute_planned_query({"plan_ref": plan["plan_ref"], "_server_context": context})
        if not execution.get("ok"):
            raise RuntimeError(f"Grafana Query failed: {fixture['id']} {execution}")
        validation = execution["validation"]
        if validation["row_count"] != fixture["expected_rows"]:
            raise RuntimeError(f"row count mismatch: {fixture['id']} {validation}")
        run_id, _ = planner.parse_artifact_ref(plan["plan_ref"])
        actual = [comparable(value) for value in first_row(planner.ARTIFACTS.root / run_id / "grafana-frame.json")]
        expected = [comparable(value) for value in fixture["expected_first_row"]]
        if actual != expected:
            raise RuntimeError(f"result mismatch: {fixture['id']} expected={expected} actual={actual}")
        results.append({"id": fixture["id"], "planner": "accepted", "grafana_query_calls": 1, "row_count": validation["row_count"], "fields": validation["field_names"], "first_row": actual})
    print(json.dumps({"ok": True, "fixture_file": str(FIXTURES.relative_to(ROOT)), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
