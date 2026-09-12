#!/usr/bin/env python3
"""Regression: dated and undated upload plans execute with bounded, authorized queries."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

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


def main() -> int:
    # Isolate import-time cleanup as well as the test's artifact/upload writes.
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
        "ANALYSIS_ARTIFACT_ROOT": str(Path(tmp) / "runs"),
        "UPLOAD_DATASET_ROOT": str(Path(tmp) / "uploads"),
        "ANALYSIS_CSV_OUTPUT_DIR": str(Path(tmp) / "outputs"),
    }):
        uploads = load("generic_plan_uploads", ROOT / "uploaded_datasets.py")
        planner = load("generic_plan_planner", ROOT / "data-query-planner-mcp/server.py")
        query = load("generic_plan_query", ROOT / "grafana-query-mcp/server.py")
        artifacts = planner.ARTIFACTS
        context = {"org_id": "1", "user_id": "9", "session_id": "generic-session"}
        reference_time = datetime(2026, 9, 12, tzinfo=timezone.utc)
        for unit, seconds in {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}.items():
            if query.parse_time_bound(f"now-2{unit}", reference_time) != reference_time - timedelta(seconds=2 * seconds):
                raise AssertionError(f"incorrect relative unit: {unit}")
        for raw in (b"x,target\n1,setosa\n2,virginica\n", b"date,x,target\n2026-01-01,1,10\n2026-01-02,2,9\n"):
            uploaded = uploads.store_upload(context=context, session_id=context["session_id"], filename="regression.csv", raw=raw)
            run_id = artifacts.create_run(context)
            fields = uploaded["fields"]
            metadata_ref = artifacts.write_json(context, run_id, "dataset-metadata", {
                "dataset_id": uploaded["id"], "session_id": context["session_id"],
                "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource", "fields": fields,
                "query_kind": "uploaded_csv", "date_range": uploaded["date_range"],
                "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/upload.csv", "columns": [{"selector": item["name"]} for item in fields]},
            })
            planned = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": [item["name"] for item in fields], "minimum_rows": 1, "maximum_rows": 100, "_server_context": context})
            if not planned["ok"]:
                raise AssertionError(planned)
            plan = artifacts.read_json(context, planned["plan_ref"])
            if plan.get("ontology") != {"sha256": uploaded["source_sha256"], "snapshot_id": f"candidate:{uploaded['id']}"} or not plan.get("plan_sha256"):
                raise AssertionError(plan)
            query.verify_authorized_plan(context, plan)
            response = {"results": {"A": {"status": 200, "frames": [{"schema": {"fields": fields}, "data": {"values": [[1, 2] for _ in fields]}}]}}}
            clock = Mock(wraps=datetime)
            now = datetime(2026, 9, 12, tzinfo=timezone.utc)
            clock.now.side_effect = [now, now + timedelta(microseconds=1)]
            with patch.object(query, "datetime", clock), patch.object(query, "post_grafana", return_value=response) as post:
                executed = query.tool_execute_planned_query({"plan_ref": planned["plan_ref"], "_server_context": context, "_server_session_id": context["session_id"]})
                if not executed["ok"]:
                    raise AssertionError(executed)
                post.assert_called_once_with("/api/ds/query", {"queries": [plan["grafana_query"]], **plan["time_range"]}, query.MAX_RESPONSE_BYTES)
                if uploaded["date_range"].get("kind") == "unbounded":
                    clock.now.assert_called_once_with(timezone.utc)
                if artifacts.read_json(context, executed["frame_ref"]) != response["results"]["A"]["frames"]:
                    raise AssertionError("validated frame was not persisted")

            # Signed test plans isolate bounds checks without bypassing identity/hash validation.
            for bounds, maximum_bytes, error in (
                ({"from": "not-a-date", "to": "now"}, query.MAX_RESPONSE_BYTES, "plan bounds are invalid"),
                ({"from": "now-9999999999d", "to": "now"}, query.MAX_RESPONSE_BYTES, "plan bounds are invalid"),
                ({"from": "now-368d", "to": "now"}, query.MAX_RESPONSE_BYTES, "plan bounds exceed executor limits"),
                ({"from": "now", "to": "now-1d"}, query.MAX_RESPONSE_BYTES, "plan bounds exceed executor limits"),
                ({"from": "now-1d", "to": "now"}, query.MAX_RESPONSE_BYTES + 1, "plan bounds exceed executor limits"),
            ):
                invalid = copy.deepcopy(plan)
                invalid["time_range"] = bounds
                invalid["analysis_input_contract"]["maximum_response_bytes"] = maximum_bytes
                invalid.pop("plan_sha256")
                invalid["plan_sha256"] = hashlib.sha256(json.dumps(invalid, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                invalid_ref = artifacts.write_json(context, artifacts.create_run(context), "query-plan", invalid)
                with patch.object(query, "post_grafana") as post:
                    rejected = query.tool_execute_planned_query({"plan_ref": invalid_ref, "_server_context": context, "_server_session_id": context["session_id"]})
                    if rejected.get("ok") is not False or rejected.get("error") != error:
                        raise AssertionError(rejected)
                    post.assert_not_called()

    print("ok: dated + undated upload planner/executor, one clock sample, bounds rejection before I/O")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
