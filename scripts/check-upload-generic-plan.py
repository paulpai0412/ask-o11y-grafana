#!/usr/bin/env python3
"""Regression check: generic uploaded-data plans carry candidate identity and a valid plan hash."""
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


def main() -> int:
    uploads = load("generic_plan_uploads", ROOT / "uploaded_datasets.py")
    planner = load("generic_plan_planner", ROOT / "data-query-planner-mcp/server.py")
    query = load("generic_plan_query", ROOT / "grafana-query-mcp/server.py")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        upload_root = root / "uploads"
        artifacts = planner.ArtifactStore(root / "runs")
        for module in (uploads, planner.uploaded_datasets, query.uploaded_datasets):
            setattr(module, "UPLOAD_ROOT", upload_root)
        setattr(planner, "ARTIFACTS", artifacts)
        context = {"org_id": "1", "user_id": "9", "session_id": "generic-session"}
        uploaded = uploads.store_upload(context=context, session_id=context["session_id"], filename="regression.csv", raw=b"date,x,target\n2026-01-01,1,10\n2026-01-02,2,9\n")
        run_id = artifacts.create_run(context)
        fields = uploaded["fields"]
        metadata_ref = artifacts.write_json(context, run_id, "dataset-metadata", {
            "dataset_id": uploaded["id"], "session_id": context["session_id"],
            "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource", "fields": fields,
            "date_range": {"all_from": "2026-01-01", "all_to": "2026-01-02"},
            "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/upload.csv", "columns": [{"selector": item["name"]} for item in fields]},
        })
        planned = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": [item["name"] for item in fields], "minimum_rows": 1, "maximum_rows": 100, "_server_context": context})
        if not planned["ok"]:
            raise AssertionError(planned)
        plan = artifacts.read_json(context, planned["plan_ref"])
        if plan.get("ontology") != {"sha256": uploaded["source_sha256"], "snapshot_id": f"candidate:{uploaded['id']}"} or not plan.get("plan_sha256"):
            raise AssertionError(plan)
        query.verify_authorized_plan(context, plan)

    print("ok: generic upload plan candidate identity + hash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
