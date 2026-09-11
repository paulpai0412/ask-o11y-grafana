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
ALLOWED_ANALYSIS_KINDS = ("catboost", "random_forest_shap", "gradient_boosting", "logistic_regression", "xgboost")
ALLOWED_REGRESSION_KINDS = ("dummy", "ridge", "random_forest", "extra_trees", "hist_gradient_boosting", "catboost", "xgboost")
REGRESSION_MODES = ("retrospective_association", "forward_prediction")
MISSING_TARGET_SPLIT_POLICY_DEFAULT = {"mode": "reject", "approved": False}
MISSING_TARGET_SPLIT_POLICY_MODES = ("reject", "drop_invalid_target_split")


def normalize_missing_target_split_policy(value: Any, *, allow_drop: bool = True) -> dict[str, Any]:
    """Return the only bounded policy that can govern invalid target/split rows."""
    if value is None:
        return dict(MISSING_TARGET_SPLIT_POLICY_DEFAULT)
    if not isinstance(value, dict) or set(value) != {"mode", "approved"}:
        raise ValueError("missing_value_policy must contain only mode and approved")
    mode, approved = value.get("mode"), value.get("approved")
    if mode not in MISSING_TARGET_SPLIT_POLICY_MODES or not isinstance(approved, bool):
        raise ValueError("missing_value_policy mode or approval is invalid")
    if (mode == "reject") != (not approved):
        raise ValueError("missing_value_policy approval must exactly match its mode")
    if mode == "drop_invalid_target_split" and not allow_drop:
        raise ValueError("missing_value_policy row dropping is unsupported for this analysis")
    return {"mode": mode, "approved": approved}


def optimization_direction(contract: dict[str, Any]) -> str | None:
    """Business target direction is independent of the metric scorer."""
    optimization = contract.get("optimization")
    if optimization is not None and (not isinstance(optimization, dict) or set(optimization) != {"direction"}):
        raise ValueError("optimization requires only an explicit business direction")
    direction = optimization["direction"] if optimization is not None else contract.get("target_direction")
    if direction is not None and direction not in {"minimize", "maximize"}:
        raise ValueError("invalid optimization direction")
    if optimization is not None and contract.get("target_direction") not in (None, direction):
        raise ValueError("legacy and current optimization directions disagree")
    constrained = contract.get("constrained_search") or {}
    if not isinstance(constrained, dict) or (constrained.get("enabled") and direction is None):
        raise ValueError("constrained search requires an explicit business direction")
    return direction


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
    return {"snapshot_id": str(registry["snapshot_id"]), "namespace": str(registry.get("namespace", "analysis.u1")), "version": str(registry["registry_version"]), "sha256": str(snapshot["snapshot_sha256"]), "status": str(registry["status"]), "approval_scope": str(registry.get("approval_scope", "unknown"))}


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
    return {key: field.get(key) for key in ("canonical_id", "physical_name", "display_name", "aliases", "definition", "description", "type", "unit", "semantic_kind", "analysis_role", "status", "availability", "lineage", "operating_limits", "evidence", "reason") if key in field}


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
    task_kind = contract.get("task_kind", "binary_classification") if isinstance(contract, dict) else "binary_classification"
    regression = task_kind == "regression"
    population_filter: dict[str, Any] = {}
    feature_set_count = 0
    feature_set_ids: list[str] = []
    allowed = {"task_kind", "analysis_mode", "include_treatment_candidates", "algorithms", "target_direction", "optimization", "controllable_fields", "context_fields", "forbidden_fields", "constrained_search", "population_filter", "feature_sets", "kind", "dataset_id", "target", "features", "as_of", "split", "seed", "ontology_snapshot_sha256", "quality_filter", "positive_class", "purpose", "conclusion", "autotune", "objective", "objective_minimum", "search_budget", "class_imbalance_strategy", "cost_matrix", "cost_matrix_approved", "reporting_denominator", "minimum_recall", "missing_value_policy"}
    if not isinstance(contract, dict) or set(contract) - allowed:
        reject("ANALYSIS_CONTRACT_INVALID", "contract.shape")
        contract = contract if isinstance(contract, dict) else {}
    dataset_id = contract.get("dataset_id")
    try:
        missing_value_policy = normalize_missing_target_split_policy(contract.get("missing_value_policy"), allow_drop=regression)
    except ValueError:
        reject("ANALYSIS_CONTRACT_INVALID", "analysis.missing_value_policy")
        missing_value_policy = dict(MISSING_TARGET_SPLIT_POLICY_DEFAULT)
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
        treatment_feature = regression and field["analysis_role"] == "treatment_candidate"
        if field["status"] != "approved" and not treatment_feature:
            reject("FIELD_NOT_APPROVED", f"feature.approved:{physical}")
            field_valid = False
        if treatment_feature:
            if not bool(contract.get("include_treatment_candidates")):
                reject("TREATMENT_FEATURE_OPT_IN_REQUIRED", f"feature.treatment_opt_in:{physical}")
                field_valid = False
            if physical not in set(map(str, contract.get("controllable_fields") or [])):
                reject("CONTROLLABLE_FIELD_REQUIRED", f"feature.controllable:{physical}")
                field_valid = False
            if contract.get("analysis_mode") == "forward_prediction":
                availability = field.get("availability") or {}
                if not bool(availability.get("eligible_at_as_of")):
                    reject("AVAILABILITY_UNKNOWN", f"feature.availability:{physical}")
                    field_valid = False
        elif field["analysis_role"] != "feature" or physical not in dataset["approved_features"]:
            reject("FIELD_ROLE_FORBIDDEN", f"feature.allowlist:{physical}")
            field_valid = False
        availability = field.get("availability") or {}
        eligible = availability.get("eligible_at_as_of")
        if not treatment_feature and (not isinstance(eligible, bool) or not eligible):
            reject("AVAILABILITY_UNKNOWN", f"feature.availability:{physical}")
            field_valid = False
        if field_valid:
            included.append(physical)
    if not regression:
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
    if not regression and isinstance(split, dict) and split.get("kind") != "stratified_holdout":
        reject("SPLIT_POLICY_VIOLATION", "split.classification_kind")
    if regression:
        if contract.get("analysis_mode") not in REGRESSION_MODES:
            reject("ANALYSIS_CONTRACT_INVALID", "analysis.mode")
        algorithms = contract.get("algorithms")
        if not isinstance(algorithms, list) or not algorithms or any(kind not in ALLOWED_REGRESSION_KINDS for kind in algorithms):
            reject("ANALYSIS_CONTRACT_INVALID", "analysis.algorithms")
        try:
            optimization_direction(contract)
        except ValueError:
            reject("ANALYSIS_CONTRACT_INVALID", "analysis.optimization")
        if contract.get("objective", "mae") != "mae":
            reject("ANALYSIS_CONTRACT_INVALID", "analysis.objective")
        controllable = contract.get("controllable_fields")
        context = contract.get("context_fields")
        forbidden = contract.get("forbidden_fields")
        raw_population_filter = contract.get("population_filter") or {}
        if isinstance(raw_population_filter, dict):
            population_filter = raw_population_filter
        if not isinstance(raw_population_filter, dict) or any(not isinstance(name, str) or not name for name in raw_population_filter):
            reject("ANALYSIS_CONTRACT_INVALID", "analysis.population_filter")
            population_filter = {}
        population_filter_names = set(population_filter)
        if any(name not in by_name for name in population_filter_names):
            reject("UNKNOWN_FIELD", "analysis.population_filter.exists")
        if any(name in {str(target_name), str(dataset.get("target"))} for name in population_filter_names):
            reject("POPULATION_FILTER_INVALID", "analysis.population_filter.target")
        if any(by_name.get(name, {}).get("analysis_role") in {"quality", "forbidden", "identifier"} for name in population_filter_names):
            reject("POPULATION_FILTER_INVALID", "analysis.population_filter.role")
        controllable_values: list[Any] = controllable if isinstance(controllable, list) else []
        context_values: list[Any] = context if isinstance(context, list) else []
        forbidden_values: list[Any] = forbidden if isinstance(forbidden, list) else []
        split_field = split.get("time_field") if isinstance(split, dict) else None
        feature_names = {str(name) for name in features}
        valid_role_lists = all(len(set(map(str, value))) == len(value) for value in (controllable_values, context_values, forbidden_values)) and all(isinstance(value, list) for value in (controllable, context, forbidden))
        if not valid_role_lists:
            controllable_names: set[str] = set()
            context_names: set[str] = set()
            forbidden_names: set[str] = set()
            reject("ANALYSIS_CONTRACT_INVALID", "analysis.field_roles")
        else:
            controllable_names = {str(name) for name in controllable_values}
            context_names = {str(name) for name in context_values}
            forbidden_names = {str(name) for name in forbidden_values}
            expected_features = (controllable_names | context_names) - ({str(split_field)} if isinstance(split_field, str) else set()) - population_filter_names
            if feature_names != expected_features:
                reject("FIELD_ROLE_FORBIDDEN", "analysis.feature_role_partition")
            if feature_names & (forbidden_names - population_filter_names):
                reject("TARGET_PROXY_UNRESOLVED", "analysis.forbidden_feature_overlap")
            if any(name not in by_name for name in controllable_names):
                reject("UNKNOWN_FIELD", "analysis.controllable.exists")
            treatment_count = sum(by_name.get(name, {}).get("analysis_role") == "treatment_candidate" for name in controllable_names)
            if treatment_count and not bool(contract.get("include_treatment_candidates")):
                reject("TREATMENT_FEATURE_OPT_IN_REQUIRED", "analysis.treatment_opt_in")
        raw_feature_sets = contract.get("feature_sets")
        if raw_feature_sets is not None:
            if not isinstance(raw_feature_sets, list) or not 1 <= len(raw_feature_sets) <= 6:
                reject("ANALYSIS_CONTRACT_INVALID", "analysis.feature_sets.bound")
            else:
                feature_set_ids = []
                seen_ids: set[str] = set()
                for item in raw_feature_sets:
                    if not isinstance(item, dict):
                        reject("ANALYSIS_CONTRACT_INVALID", "analysis.feature_sets.shape")
                        continue
                    set_id = item.get("id")
                    set_features = item.get("features")
                    set_controllable = item.get("controllable_fields", [])
                    set_context = item.get("context_fields", [])
                    if not isinstance(set_id, str) or not set_id or set_id in seen_ids or not isinstance(set_features, list) or not set_features or not isinstance(set_controllable, list) or not isinstance(set_context, list):
                        reject("ANALYSIS_CONTRACT_INVALID", "analysis.feature_sets.shape")
                        continue
                    seen_ids.add(set_id)
                    feature_set_ids.append(set_id)
                    set_feature_names = {str(name) for name in set_features}
                    set_controllable_names = {str(name) for name in set_controllable}
                    set_context_names = {str(name) for name in set_context}
                    if len(set_feature_names) != len(set_features) or not set_feature_names <= feature_names:
                        reject("FIELD_ROLE_FORBIDDEN", f"analysis.feature_sets.allowlist:{set_id}")
                    expected_set_features = (set_controllable_names | set_context_names) - ({str(split_field)} if isinstance(split_field, str) else set()) - population_filter_names
                    if set_feature_names != expected_set_features or set_controllable_names & set_context_names:
                        reject("FIELD_ROLE_FORBIDDEN", f"analysis.feature_sets.partition:{set_id}")
                    if any(name not in by_name for name in set_controllable_names | set_context_names):
                        reject("UNKNOWN_FIELD", f"analysis.feature_sets.exists:{set_id}")
                    if any(by_name.get(name, {}).get("analysis_role") == "treatment_candidate" for name in set_controllable_names) and not bool(contract.get("include_treatment_candidates")):
                        reject("TREATMENT_FEATURE_OPT_IN_REQUIRED", f"analysis.feature_sets.treatment_opt_in:{set_id}")
                feature_set_count = len(feature_set_ids)
    elif contract.get("kind") not in ALLOWED_ANALYSIS_KINDS or contract.get("seed") != policy["seed"]:
        reject("ANALYSIS_CONTRACT_INVALID", "analysis.kind_seed")
    selected = set(included)
    excluded = [{"field": field["physical_name"], "reason": field["reason"], "status": field["status"], "role": field["analysis_role"]} for field in dataset["fields"] if field["physical_name"] not in selected and field["physical_name"] not in {dataset["target"], dataset["time_identity"], dataset["quality_policy"]["field"]}]
    return {
        "conforms": not codes,
        "rejection_codes": codes[:32],
        "failed_rules": failed_rules[:64],
        "snapshot": snapshot_identity(snapshot),
        "included_fields": included,
        "excluded_fields": excluded[:MAX_FIELDS],
        "field_views": [
            {key: field.get(key) for key in ("physical_name", "unit", "semantic_kind", "analysis_role", "reason") if key in field}
            for field in dataset["fields"][:MAX_FIELDS]
        ],
        "analysis_mode": contract.get("analysis_mode") if regression else None,
        "treatment_feature_count": sum(by_name.get(name, {}).get("analysis_role") == "treatment_candidate" for name in included),
        "population_filter": population_filter,
        "feature_set_count": feature_set_count,
        "feature_set_ids": feature_set_ids,
        "missing_value_policy": missing_value_policy,
    }
