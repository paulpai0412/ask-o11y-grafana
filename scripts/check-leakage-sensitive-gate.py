#!/usr/bin/env python3
"""Self-check for deterministic leakage/sensitive field gating on uploads."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
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


def main() -> int:
    semantics = load_module("governance_semantics", ROOT / "upload_semantics.py")

    rows = ["Age,Gender,Race,Satisfaction Score,MonthlyCharge,Churn"]
    labels = [("yes" if index % 3 == 0 else "no") for index in range(24)]
    for index, label in enumerate(labels):
        rows.append(f"{30 + index},{'Female' if index % 2 else 'Male'},{'White' if index % 2 else 'Asian'},{3 + index % 3},{50 + index},{label}")
    with tempfile.TemporaryDirectory() as tmp:
        upload_dir = Path(tmp)
        (upload_dir / "data.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        hints = semantics.build_hints(upload_dir / "data.csv", dataset_id="upload_gov", org_id="1", user_id="9")

    roles = {field["physical_name"]: field["analysis_role"] for field in hints["fields"]}
    assert roles["Satisfaction Score"] == "leakage_risk", roles
    assert roles["Gender"] == "sensitive" and roles["Race"] == "sensitive", roles
    assert roles["Age"] == "feature" and roles["MonthlyCharge"] == "feature", roles

    allowlist = semantics.feature_allowlist(hints)
    assert "Satisfaction Score" not in allowlist and "Gender" not in allowlist and "Race" not in allowlist

    base = {
        "kind": "gradient_boosting", "dataset_id": "upload_gov", "target": "Churn",
        "split": {"kind": "stratified_holdout", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"},
        "seed": 42, "class_imbalance_strategy": "none",
    }
    rejected = semantics.validate_analysis_contract(hints, {**base, "features": ["Age", "Satisfaction Score", "Gender"]})
    assert not rejected["conforms"]
    assert "LEAKAGE_FIELD_FORBIDDEN" in rejected["rejection_codes"], rejected
    assert "SENSITIVE_FIELD_FORBIDDEN" in rejected["rejection_codes"], rejected

    accepted = semantics.validate_analysis_contract(hints, {**base, "features": ["Age", "MonthlyCharge"]})
    assert accepted["conforms"], accepted
    assert set(accepted["included_fields"]) == {"Age", "MonthlyCharge"}
    assert {"Satisfaction Score", "Gender", "Race"} <= set(accepted["excluded_fields"])

    # Planner integration: the deterministic gate rejects leaky plans before execution.
    planner = load_module("governance_planner", ROOT / "data-query-planner-mcp/server.py")
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = load_module("governance_sandbox", ROOT / "sandbox-analysis-mcp/server.py")
        setattr(sandbox.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        planner_artifacts = planner.ArtifactStore(Path(tmp) / "runs")
        artifacts = planner.ArtifactStore(Path(tmp) / "runs")
        setattr(planner, "ARTIFACTS", artifacts)
        setattr(planner.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        context = {"org_id": "1", "user_id": "gov-owner", "session_id": "session-gov"}
        uploaded = planner.uploaded_datasets.store_upload(context=context, session_id=context["session_id"], filename="gov.csv", raw=("\n".join(rows) + "\n").encode())
        run_id = artifacts.create_run(context)
        fields = uploaded["fields"]
        metadata = {
            "dataset_id": uploaded["id"], "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource",
            "session_id": context["session_id"], "fields": fields,
            "date_range": {"all_from": "2026-01-01", "all_to": "2026-12-31"},
            "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://example.invalid/gov.csv", "columns": [{"selector": field["name"]} for field in fields]},
        }
        metadata_ref = artifacts.write_json(context, run_id, "dataset-metadata", metadata)
        contract = {
            "kind": "gradient_boosting", "dataset_id": uploaded["id"], "target": "Churn",
            "features": ["Age", "Satisfaction Score"], "as_of": "2026-08-25",
            "split": {"kind": "stratified_holdout", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"},
            "seed": 42, "class_imbalance_strategy": "none", "ontology_snapshot_sha256": uploaded["source_sha256"],
        }
        denied = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["Age", "Satisfaction Score", "Churn"], "minimum_rows": 20, "analysis_contract": contract, "_server_context": context})
        assert not denied["ok"] and "LEAKAGE_FIELD_FORBIDDEN" in denied["evidence"]["rejection_codes"], denied
        clean = planner.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["Age", "MonthlyCharge", "Churn"], "minimum_rows": 20, "analysis_contract": {**contract, "features": ["Age", "MonthlyCharge"]}, "_server_context": context})
        assert clean["ok"], clean
        plan = artifacts.read_json(context, clean["plan_ref"])
        sandbox.verify_plan_for_context(context, plan)

    print("ok: leakage/sensitive deterministic gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
