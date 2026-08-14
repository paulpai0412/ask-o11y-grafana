"""Immutable ontology snapshot catalog and U1 ML policy validation."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "semantic/catalog.json"
MAX_FIELDS = 200


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("ontology catalog is unavailable or invalid") from exc
    if not isinstance(catalog, dict) or catalog.get("format") != "ask-o11y-ontology-catalog-v1" or not isinstance(catalog.get("snapshots"), list):
        raise ValueError("ontology catalog is invalid")
    ids = [entry.get("snapshot_id") for entry in catalog["snapshots"] if isinstance(entry, dict)]
    if len(ids) != len(set(ids)) or catalog.get("default_snapshot_id") not in ids:
        raise ValueError("ontology catalog snapshot identities are invalid")
    return catalog


def list_snapshots(namespace: str | None = None, dataset_id: str | None = None) -> list[dict[str, Any]]:
    entries = load_catalog()["snapshots"]
    return [entry for entry in entries if (namespace is None or entry["namespace"] == namespace) and (dataset_id is None or dataset_id in entry["dataset_ids"])]


def _catalog_entry(snapshot_ref: str | None = None, dataset_id: str | None = None, namespace: str | None = None) -> dict[str, Any]:
    catalog = load_catalog()
    entries = [entry for entry in catalog["snapshots"] if (namespace is None or entry["namespace"] == namespace) and (dataset_id is None or dataset_id in entry["dataset_ids"])]
    if snapshot_ref not in (None, "approved"):
        entries = [entry for entry in entries if snapshot_ref in {entry["snapshot_id"], entry["sha256"]}]
    elif dataset_id is None and namespace is None:
        entries = [entry for entry in entries if entry["snapshot_id"] == catalog["default_snapshot_id"]]
    if len(entries) != 1:
        raise ValueError("UNKNOWN_OR_AMBIGUOUS_SNAPSHOT")
    return entries[0]


def load_snapshot(path: Path | None = None, *, snapshot_ref: str | None = None, dataset_id: str | None = None, namespace: str | None = None) -> dict[str, Any]:
    entry = None
    snapshot_path = path
    if snapshot_path is None:
        entry = _catalog_entry(snapshot_ref, dataset_id, namespace)
        snapshot_path = (ROOT / entry["path"]).resolve()
        snapshots_root = (ROOT / "semantic/snapshots").resolve()
        if snapshots_root not in snapshot_path.parents:
            raise ValueError("ontology catalog path escapes snapshot directory")
    try:
        snapshot = json.loads(snapshot_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("ontology snapshot is unavailable or invalid") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("ontology snapshot root must be an object")
    claimed = snapshot.get("snapshot_sha256")
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    actual = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    registry = snapshot.get("registry")
    if claimed != actual or not isinstance(registry, dict) or registry.get("status") != "approved":
        raise ValueError("ontology snapshot hash/status verification failed")
    if entry and (claimed != entry["sha256"] or registry.get("snapshot_id") != entry["snapshot_id"]):
        raise ValueError("ontology catalog snapshot/hash verification failed")
    return snapshot


def snapshot_identity(snapshot: dict[str, Any]) -> dict[str, str]:
    registry = snapshot["registry"]
    return {"snapshot_id": str(registry["snapshot_id"]), "namespace": str(registry.get("namespace", "analysis.u1")), "version": str(registry["registry_version"]), "sha256": str(snapshot["snapshot_sha256"]), "status": str(registry["status"])}


def verify_snapshot_ref(snapshot: dict[str, Any], snapshot_ref: str | None) -> str | None:
    if snapshot_ref is None or snapshot_ref == "approved":
        return None
    identity = snapshot_identity(snapshot)
    if snapshot_ref not in {identity["snapshot_id"], identity["sha256"]}:
        return "SNAPSHOT_HASH_MISMATCH"
    return None


def find_dataset(snapshot: dict[str, Any], dataset_id: str) -> dict[str, Any] | None:
    return next((dataset for dataset in snapshot["registry"]["datasets"] if dataset_id in {dataset["physical_id"], dataset["canonical_id"]}), None)


def find_relation(snapshot: dict[str, Any], from_dataset: str, to_dataset: str) -> dict[str, Any] | None:
    endpoints = {from_dataset.upper(), to_dataset.upper()}
    return next((relation for dataset in snapshot["registry"]["datasets"] for relation in dataset.get("relations", []) if {str(relation["from_dataset"]).upper(), str(relation["to_dataset"]).upper()} == endpoints), None)


def fields_by_name(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for field in dataset["fields"]:
        output[str(field["physical_name"])] = field
        output[str(field["canonical_id"])] = field
    return output


def field_view(field: dict[str, Any]) -> dict[str, Any]:
    return {key: field.get(key) for key in ("canonical_id", "physical_name", "type", "unit", "semantic_kind", "analysis_role", "status", "availability", "lineage", "evidence", "reason") if key in field}


def verify_plan(plan: dict[str, Any]) -> None:
    claimed = plan.get("plan_sha256")
    ontology = plan.get("ontology")
    if claimed is None and ontology is None:
        return
    if not isinstance(claimed, str) or not isinstance(ontology, dict):
        raise ValueError("CONTRACT_HASH_MISMATCH")
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    actual = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    snapshot = load_snapshot(snapshot_ref=ontology.get("sha256"))
    identity = snapshot_identity(snapshot)
    if claimed != actual or ontology.get("sha256") != identity["sha256"] or ontology.get("snapshot_id") != identity["snapshot_id"]:
        raise ValueError("CONTRACT_HASH_MISMATCH")


def validate_analysis_contract(snapshot: dict[str, Any], contract: dict[str, Any], snapshot_ref: str | None = None) -> dict[str, Any]:
    """Legacy U1 policy validator; generic ontology loading is analysis-neutral."""
    codes: list[str] = []
    failed_rules: list[str] = []

    def reject(code: str, rule: str) -> None:
        if code not in codes:
            codes.append(code)
        if rule not in failed_rules:
            failed_rules.append(rule)

    mismatch = verify_snapshot_ref(snapshot, snapshot_ref or contract.get("ontology_snapshot_sha256"))
    if mismatch:
        reject(mismatch, "snapshot.pin")
    allowed = {"kind", "dataset_id", "target", "features", "as_of", "split", "seed", "ontology_snapshot_sha256", "quality_filter"}
    if not isinstance(contract, dict) or set(contract) - allowed:
        reject("ANALYSIS_CONTRACT_INVALID", "contract.shape")
        contract = contract if isinstance(contract, dict) else {}
    dataset_id = contract.get("dataset_id")
    dataset = find_dataset(snapshot, str(dataset_id)) if isinstance(dataset_id, str) else None
    if dataset is None:
        reject("UNKNOWN_DATASET", "dataset.exists")
        return {"conforms": False, "rejection_codes": codes, "failed_rules": failed_rules, "snapshot": snapshot_identity(snapshot), "included_fields": [], "excluded_fields": []}
    if "target" not in dataset:
        reject("ACTION_CAPABILITY_NOT_SUPPORTED", "validator.ml_policy_missing")
        return {"conforms": False, "rejection_codes": codes, "failed_rules": failed_rules, "snapshot": snapshot_identity(snapshot), "included_fields": [], "excluded_fields": []}
    if dataset.get("status") != "approved":
        reject("SNAPSHOT_NOT_APPROVED", "dataset.approved")
    by_name = fields_by_name(dataset)
    target_name = contract.get("target")
    target = by_name.get(str(target_name)) if isinstance(target_name, str) else None
    if target is None:
        reject("UNKNOWN_FIELD", "target.exists")
    elif target["physical_name"] != dataset["target"] or target["status"] != "approved" or target["analysis_role"] != "target":
        reject("TARGET_NOT_APPROVED", "target.approved_role")
    features = contract.get("features")
    if not isinstance(features, list) or not features or len(features) > MAX_FIELDS or len(set(map(str, features))) != len(features):
        reject("ANALYSIS_CONTRACT_INVALID", "features.bounded_unique")
        features = []
    included: list[str] = []
    for raw_name in features:
        name = str(raw_name)
        field = by_name.get(name)
        if field is None:
            reject("UNKNOWN_FIELD", f"feature.exists:{name}")
            continue
        physical = str(field["physical_name"])
        field_valid = True
        if physical == dataset["target"]:
            reject("TARGET_USED_AS_FEATURE", f"feature.not_target:{physical}")
            field_valid = False
        if field["analysis_role"] == "quality":
            reject("QUALITY_FIELD_USED_AS_FEATURE", f"feature.not_quality:{physical}")
            field_valid = False
        if field["semantic_kind"] == "target_proxy":
            reject("TARGET_PROXY_UNRESOLVED", f"feature.lineage:{physical}")
            field_valid = False
        if field["status"] != "approved":
            reject("FIELD_NOT_APPROVED", f"feature.approved:{physical}")
            field_valid = False
        if field["analysis_role"] != "feature" or physical not in dataset["approved_features"]:
            reject("FIELD_ROLE_FORBIDDEN", f"feature.allowlist:{physical}")
            field_valid = False
        availability = field.get("availability") or {}
        eligible = availability.get("eligible_at_as_of")
        if not isinstance(eligible, bool) or not eligible:
            reject("AVAILABILITY_UNKNOWN", f"feature.availability:{physical}")
            field_valid = False
        if field_valid:
            included.append(physical)
    try:
        date.fromisoformat(str(contract.get("as_of")))
    except ValueError:
        reject("AS_OF_INVALID", "time.as_of")
    if contract.get("quality_filter") is not None and contract.get("quality_filter") != dataset["quality_policy"]:
        reject("QUALITY_POLICY_VIOLATION", "quality.policy")
    split = contract.get("split")
    policy = dataset["split_policy"]
    split_keys = {"kind", "time_field", "test_fraction", "preprocessing_fit_scope", "seed"}
    if not isinstance(split, dict) or set(split) - split_keys or any(split.get(key) != policy[key] for key in ("kind", "time_field", "test_fraction", "preprocessing_fit_scope")) or ("seed" in split and split["seed"] != policy["seed"]):
        reject("SPLIT_POLICY_VIOLATION", "split.policy")
    if contract.get("kind") != "random_forest_shap" or contract.get("seed") != policy["seed"]:
        reject("ANALYSIS_CONTRACT_INVALID", "analysis.kind_seed")
    selected = set(included)
    excluded = [{"field": field["physical_name"], "reason": field["reason"], "status": field["status"], "role": field["analysis_role"]} for field in dataset["fields"] if field["physical_name"] not in selected and field["physical_name"] not in {dataset["target"], dataset["time_identity"], dataset["quality_policy"]["field"]}]
    return {"conforms": not codes, "rejection_codes": codes[:32], "failed_rules": failed_rules[:64], "snapshot": snapshot_identity(snapshot), "included_fields": included, "excluded_fields": excluded[:MAX_FIELDS]}
