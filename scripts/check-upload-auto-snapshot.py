#!/usr/bin/env python3
"""Self-check for Workstream A: uploads auto-produce ontology candidates with analysis hints."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]

ADULT_CSV = (
    b"age,workclass,fnlwgt,education,income,row_id\n"
    b"90,Private,77053,HS-grad,<=50K,1\n"
    b"38,Local-gov,12345,Bachelors,<=50K,2\n"
    b"53,Private,99999,Masters,>50K,3\n"
    b"28,?,54321,HS-grad,<=50K,4\n"
)


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"invalid JSON at {path}: {exc}") from exc


def main() -> int:
    load_module("ontology_contract", ROOT / "ontology_contract.py")
    uploads = load_module("uploaded_datasets", ROOT / "uploaded_datasets.py")
    hints_mod = load_module("upload_semantics", ROOT / "upload_semantics.py")

    with tempfile.TemporaryDirectory() as tmp:
        setattr(uploads, "UPLOAD_ROOT", Path(tmp))
        context = {"org_id": "1", "user_id": "9"}
        meta = uploads.store_upload(context=context, session_id="sess-a", filename="adult.csv", raw=ADULT_CSV)

        # 1. candidate snapshot exists inside the upload dir and validates against the IR schema
        candidate_path = uploads.UPLOAD_ROOT / str(meta["id"]) / "candidate-ontology.json"  # type: ignore[attr-defined]
        assert candidate_path.exists(), f"missing {candidate_path}"
        candidate = read_json(candidate_path)
        try:
            schema = read_json(ROOT / "semantic/schema/candidate-ir.schema.json")
        except AssertionError:
            raise
        jsonschema = __import__("jsonschema")
        jsonschema.Draft202012Validator(schema).validate(candidate)

        # 2. analysis hints exist with per-field roles
        hints = hints_mod.load_hints(uploads.UPLOAD_ROOT / str(meta["id"]))  # type: ignore[attr-defined]
        roles = {f["physical_name"]: f["analysis_role"] for f in hints["fields"]}
        assert set(roles) == {"age", "workclass", "fnlwgt", "education", "income", "row_id"}, roles
        assert set(roles.values()) == {"feature"}, roles

        # 3. Upload observation does not guess identifiers, targets, or features from names/order.
        allowlist = hints_mod.feature_allowlist(hints)
        assert set(allowlist) == set(roles), allowlist

        # 4. Ontology MCP exposes candidate classifications with authenticated context
        ontology = load_module("ontology_mcp_server", ROOT / "ontology-mcp/server.py")
        setattr(ontology.uploaded_datasets, "UPLOAD_ROOT", uploads.UPLOAD_ROOT)  # type: ignore[attr-defined]
        classified = ontology.tool_classify_fields({
            "dataset_id": str(meta["id"]),
            "fields": ["age", "fnlwgt", "income"],
            "_server_context": {**context, "session_id": "sess-a"},
        })
        assert classified["candidate"]
        assert classified["target_candidate"] is None
        assert classified["quality_policy"]["minimum_valid_rows"] == 20
        assert set(classified["feature_allowlist"]) == set(roles)
        semantic_context = ontology.tool_get_semantic_context({
            "dataset_id": str(meta["id"]),
            "fields": ["age", "fnlwgt", "income"],
            "intent": "regression candidate context",
            "_server_context": {**context, "session_id": "sess-a"},
        })
        assert semantic_context["candidate"] and semantic_context["snapshot"]["status"] == "observed"
        assert semantic_context["context"]["target_candidate"] is None
        assert semantic_context["context"]["fields"][1]["analysis_role"] == "feature"
        validated = ontology.tool_validate_analysis_contract({
            "contract": {
                "kind": "logistic_regression", "dataset_id": str(meta["id"]), "target": "income",
                "features": ["age", "workclass", "education"],
                "split": {"kind": "stratified_holdout", "test_fraction": 0.25, "preprocessing_fit_scope": "training_only"},
                "ontology_snapshot_sha256": meta["source_sha256"],
            },
            "_server_context": {**context, "session_id": "sess-a"},
        })
        assert validated["validation"]["conforms"]

        # 5. missing rate recorded from sample ('?' treated as missing)
        by_name = {f["physical_name"]: f for f in hints["fields"]}
        try:
            missing_rate = float(by_name["workclass"]["missing_rate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AssertionError(f"missing_rate unreadable: {by_name.get('workclass')}") from exc
        assert abs(missing_rate - 0.25) < 0.01, by_name["workclass"]

    print("ok: upload auto-snapshot + analysis hints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
