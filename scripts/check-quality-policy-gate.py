#!/usr/bin/env python3
"""Self-check for Workstream C: upload quality policy deterministic gate."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "upload_semantics.py"
    spec = importlib.util.spec_from_file_location("upload_semantics", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    semantics = load_module()
    hints = {
        "dataset_id": "upload_x",
        "fields": [
            {"physical_name": "row_id", "analysis_role": "identifier", "missing_rate": 0.0},
            {"physical_name": "sensor", "analysis_role": "feature", "missing_rate": 0.1},
            {"physical_name": "mostly_missing", "analysis_role": "feature", "missing_rate": 0.7},
            {"physical_name": "defect", "analysis_role": "target_candidate", "missing_rate": 0.0, "minority_rate": 0.02},
        ],
        "quality_policy": {"rare_positive_rate": 0.05, "missing_rate_max": 0.4, "minimum_valid_rows": 20},
    }
    base = {
        "kind": "gradient_boosting",
        "dataset_id": "upload_x",
        "target": "defect",
        "features": ["sensor", "mostly_missing"],
        "split": {"kind": "stratified_holdout", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"},
        "seed": 42,
    }

    rejected = semantics.validate_analysis_contract(hints, base)
    assert not rejected["conforms"]
    assert "IMBALANCE_STRATEGY_REQUIRED" in rejected["rejection_codes"]
    assert "mostly_missing" in rejected["limitations"]

    accepted = semantics.validate_analysis_contract(hints, {**base, "class_imbalance_strategy": "scale_pos_weight"})
    assert accepted["conforms"], accepted
    assert accepted["included_fields"] == ["sensor", "mostly_missing"]
    assert "row_id" in accepted["excluded_fields"]

    identifier = semantics.validate_analysis_contract(hints, {**base, "features": ["row_id"], "class_imbalance_strategy": "balanced"})
    assert not identifier["conforms"] and "FIELD_ROLE_FORBIDDEN" in identifier["rejection_codes"]

    # Integration: Data Query Planner independently enforces the upload policy.
    planner_path = ROOT / "data-query-planner-mcp/server.py"
    spec = importlib.util.spec_from_file_location("quality_planner", planner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {planner_path}")
    planner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = planner
    spec.loader.exec_module(planner)
    with tempfile.TemporaryDirectory() as tmp:
        artifacts = planner.ArtifactStore(Path(tmp) / "runs")
        setattr(planner, "ARTIFACTS", artifacts)
        setattr(planner.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        rows = ["x,row_id,defect"] + [f"{index},{index},yes" if index < 2 else f"{index},{index},no" for index in range(100)]
        context = {"org_id": "1", "user_id": "owner", "session_id": "session-quality"}
        uploaded = planner.uploaded_datasets.store_upload(context=context, session_id=context["session_id"], filename="quality.csv", raw=("\n".join(rows) + "\n").encode())
        run_id = artifacts.create_run(context)
        fields = uploaded["fields"]
        metadata = {
            "dataset_id": uploaded["id"], "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource",
            "session_id": context["session_id"], "fields": fields,
            "date_range": {"all_from": "2026-01-01", "all_to": "2026-12-31"},
            "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/upload.csv", "columns": [{"selector": field["name"]} for field in fields]},
        }
        metadata_ref = artifacts.write_json(context, run_id, "dataset-metadata", metadata)
        upload_contract = {**base, "dataset_id": uploaded["id"], "target": "defect", "features": ["x"], "ontology_snapshot_sha256": uploaded["source_sha256"]}
        denied = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["x", "defect"], "minimum_rows": 20, "analysis_contract": upload_contract, "_server_context": context})
        assert not denied["ok"] and "IMBALANCE_STRATEGY_REQUIRED" in denied["evidence"]["rejection_codes"]
        allowed = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["x", "defect"], "minimum_rows": 20, "analysis_contract": {**upload_contract, "class_imbalance_strategy": "balanced"}, "_server_context": context})
        assert allowed["ok"], allowed

        query_path = ROOT / "grafana-query-mcp/server.py"
        query_spec = importlib.util.spec_from_file_location("quality_grafana_query", query_path)
        if query_spec is None or query_spec.loader is None:
            raise RuntimeError(f"cannot load {query_path}")
        grafana_query = importlib.util.module_from_spec(query_spec)
        sys.modules[query_spec.name] = grafana_query
        query_spec.loader.exec_module(grafana_query)
        setattr(grafana_query.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        plan = artifacts.read_json(context, allowed["plan_ref"])
        grafana_query.verify_authorized_plan(context, plan)
        sandbox_path = ROOT / "sandbox-analysis-mcp/server.py"
        sandbox_spec = importlib.util.spec_from_file_location("quality_sandbox", sandbox_path)
        if sandbox_spec is None or sandbox_spec.loader is None:
            raise RuntimeError(f"cannot load {sandbox_path}")
        sandbox = importlib.util.module_from_spec(sandbox_spec)
        sys.modules[sandbox_spec.name] = sandbox
        sandbox_spec.loader.exec_module(sandbox)
        setattr(sandbox.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        sandbox.verify_plan_for_context(context, plan)
        try:
            grafana_query.verify_authorized_plan(context, {**plan, "ontology": {**plan["ontology"], "sha256": "0" * 64}})
        except ValueError:
            pass
        else:
            raise AssertionError("Grafana Query accepted a tampered upload ontology hash")

    print("ok: upload quality policy gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
