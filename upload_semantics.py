"""Upload-time ontology candidate generation and deterministic analysis-role hints.

Workstream A of docs/design/ontology-ml-accuracy.md. Best-effort by design:
semantic annotation must never fail an upload.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ontology_contract import optimization_direction

ROOT = Path(__file__).resolve().parent
MISSING_TOKENS = {"", "?", "na", "n/a", "null", "none"}
LEAKAGE_NAME_TOKENS = ("satisfaction", "churn reason", "churn category", "churn score", "reason", "feedback", "rating", "survey", "cancellation")
SENSITIVE_NAME_TOKENS = ("gender", "sex", "race", "ethnic", "religion", "disab")


def _load_builder():
    spec = importlib.util.spec_from_file_location("import_csv_ontology", ROOT / "scripts/import-csv-ontology.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load import-csv-ontology.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in MISSING_TOKENS


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _column_samples(csv_path: Path) -> tuple[list[str], list[list[str]], int]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        headers = next(reader)
        samples = [[] for _ in headers]
        rows_read = 0
        for row in reader:
            if len(row) != len(headers):
                raise ValueError("uploaded CSV has inconsistent row width")
            for index, value in enumerate(row):
                samples[index].append(value)
            rows_read += 1
    return headers, samples, rows_read


def _analysis_roles(headers: list[str], samples: list[list[str]], rows_read: int) -> list[str]:
    """Observed types and conservative risk flags; never choose a target by column order."""
    builder = _load_builder()
    roles: list[str] = []
    for header, values in zip(headers, samples, strict=True):
        present = [value for value in values if not _is_missing(value)]
        distinct = len(set(present))
        scalar_type = builder.infer_scalar(present)
        lowered = header.casefold()
        if any(token in lowered for token in LEAKAGE_NAME_TOKENS):
            roles.append("leakage_risk")  # post-outcome signals; deterministic exclusion
            continue
        if not present or distinct <= 1:
            roles.append("constant")
            continue
        if any(token in lowered for token in SENSITIVE_NAME_TOKENS):
            roles.append("sensitive")  # protected attributes; governance approval required
            continue
        if scalar_type in {"date", "datetime"}:
            roles.append("temporal")
            continue
        roles.append("feature")
    return roles


def build_hints(csv_path: Path, dataset_id: str, org_id: str, user_id: str) -> dict[str, Any]:
    builder_module_path = ROOT / "scripts/import-csv-ontology.py"
    if not builder_module_path.exists():
        raise RuntimeError(f"missing {builder_module_path}")
    builder = _load_builder()
    candidate = builder.build_candidate(csv_path, dataset_id=dataset_id, namespace=f"upload-{org_id}-{user_id}")
    headers, samples, rows_read = _column_samples(csv_path)
    roles = _analysis_roles(headers, samples, rows_read)
    fields = []
    for header, values, role in zip(headers, samples, roles, strict=True):
        present = [value.strip() for value in values if not _is_missing(value)]
        missing = len(values) - len(present)
        data_type = builder.infer_scalar(present)
        field = {
            "physical_name": header,
            "analysis_role": role,
            "data_type": data_type,
            "distinct_count": len(set(present)),
            "missing_rate": round(missing / max(len(values), 1), 4),
            "sampled_rows": len(values),
        }
        if data_type in {"integer", "number"} and present:
            try:
                numeric = [float(value) for value in present]
                field.update({"observed_min": min(numeric), "observed_max": max(numeric)})
            except ValueError:
                pass
        counts = Counter(present)
        if data_type not in {"integer", "number", "date", "datetime"}:
            field["observed_values"] = sorted(counts)[:32]
        if len(counts) == 2:
            field["minority_rate"] = round(min(counts.values()) / max(sum(counts.values()), 1), 4)
        fields.append(field)
    return {
        "format": "ask-o11y-upload-analysis-hints-v1",
        "dataset_id": dataset_id,
        "org_id": str(org_id),
        "user_id": str(user_id),
        "candidate_snapshot": candidate,
        "quality_policy": {"rare_positive_rate": 0.05, "missing_rate_max": 0.4, "minimum_valid_rows": 20},
        "fields": fields,
    }


def annotate_upload(upload_dir: Path, dataset_id: str, org_id: str, user_id: str) -> dict[str, Any] | None:
    """Best-effort: write candidate-ontology.json + analysis-hints.json. Returns hints or None."""
    try:
        hints = build_hints(upload_dir / "data.csv", dataset_id=dataset_id, org_id=org_id, user_id=user_id)
        (upload_dir / "candidate-ontology.json").write_text(json.dumps(hints["candidate_snapshot"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = {key: hints[key] for key in ("format", "dataset_id", "org_id", "user_id", "quality_policy", "fields")}
        (upload_dir / "analysis-hints.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload
    except Exception:
        return None


def _validate_regression_contract(hints: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    codes: list[str] = []
    fields = {field["physical_name"]: field for field in hints.get("fields", [])}
    target_name = contract.get("target")
    target = fields.get(target_name)
    if target is None:
        codes.append("TARGET_NOT_APPROVED")
    elif _as_float(target.get("missing_rate"), 0.0) >= 1.0:
        codes.append("TARGET_ALL_MISSING")
    elif _as_float(target.get("distinct_count"), 0.0) < 2:
        codes.append("TARGET_CONSTANT")
    elif target.get("data_type") not in {"integer", "number"}:
        codes.append("TARGET_NOT_CONTINUOUS")

    def field_list(name: str) -> list[str]:
        value = contract.get(name)
        if not isinstance(value, list) or len(set(map(str, value))) != len(value):
            codes.append("ANALYSIS_CONTRACT_INVALID")
            return []
        result = [str(item) for item in value]
        if any(item not in fields for item in result):
            codes.append("UNKNOWN_FIELD")
        return result

    features = field_list("features")
    controllable = field_list("controllable_fields")
    context = field_list("context_fields")
    forbidden = field_list("forbidden_fields")
    raw_split = contract.get("split")
    split_contract: dict[str, Any] = raw_split if isinstance(raw_split, dict) else {}
    raw_population_filter = contract.get("population_filter") or {}
    population_filter: dict[str, Any] = raw_population_filter if isinstance(raw_population_filter, dict) else {}
    population_filter_names = set(population_filter)
    if not isinstance(raw_population_filter, dict) or any(name not in fields for name in population_filter_names):
        codes.append("POPULATION_FILTER_INVALID")
    if str(target_name) in population_filter_names:
        codes.append("POPULATION_FILTER_INVALID")
    for name, value in population_filter.items():
        field = fields.get(name)
        observed_values = field.get("observed_values") if isinstance(field, dict) else None
        if isinstance(observed_values, list) and value not in observed_values:
            codes.append("POPULATION_FILTER_VALUE_UNSEEN")
    filter_roles_invalid = any(fields.get(name, {}).get("analysis_role") in {"identifier", "constant", "leakage_risk", "sensitive"} for name in population_filter_names)
    if filter_roles_invalid:
        codes.append("POPULATION_FILTER_INVALID")
    split_only = {name for name in (split_contract.get("time_field"), split_contract.get("group_field")) if isinstance(name, str)}
    model_context = set(context) - split_only - population_filter_names
    if not features or set(controllable) & set(context) or set(features) != set(controllable) | model_context:
        codes.append("FIELD_ROLE_FORBIDDEN")
    if target_name in features or (set(features) & set(forbidden) - population_filter_names):
        codes.append("TARGET_PROXY_LEAKAGE")

    included: list[str] = []
    for name in features:
        field = fields.get(name)
        if field is None:
            continue
        role = field.get("analysis_role")
        if role == "leakage_risk":
            codes.append("LEAKAGE_FIELD_FORBIDDEN")
        elif role == "sensitive":
            codes.append("SENSITIVE_FIELD_FORBIDDEN")
        elif role in {"identifier", "constant"}:
            codes.append("FIELD_ROLE_FORBIDDEN")
        else:
            included.append(name)
    for name in controllable:
        field = fields.get(name)
        if field is not None and (field.get("analysis_role") not in {"feature", "target_candidate"} or field.get("data_type") not in {"integer", "number"} or _as_float(field.get("distinct_count"), 0.0) < 2):
            codes.append("CONTROLLABLE_FIELD_FORBIDDEN")
    if any(name in features for name, field in fields.items() if field.get("analysis_role") == "leakage_risk"):
        codes.append("LEAKAGE_FIELD_FORBIDDEN")

    split = split_contract
    if split.get("preprocessing_fit_scope") != "training_only" or split.get("kind") not in {"chronological_holdout", "grouped_holdout"}:
        codes.append("SPLIT_POLICY_VIOLATION")
    elif split.get("kind") == "chronological_holdout":
        time_field = fields.get(split.get("time_field"))
        if time_field is None or time_field.get("analysis_role") != "temporal":
            codes.append("SPLIT_POLICY_VIOLATION")
    else:
        group_name = split.get("group_field")
        if group_name not in fields or group_name == target_name or group_name in features:
            codes.append("SPLIT_POLICY_VIOLATION")

    algorithms = contract.get("algorithms")
    supported = {"dummy", "ridge", "random_forest", "extra_trees", "hist_gradient_boosting", "catboost", "xgboost"}
    if not isinstance(algorithms, list) or not algorithms or any(item not in supported for item in algorithms):
        codes.append("ANALYSIS_CONTRACT_INVALID")
    try:
        optimization_direction(contract)
    except ValueError:
        codes.append("ANALYSIS_CONTRACT_INVALID")
    if contract.get("autotune") and contract.get("objective", "mae") != "mae":
        codes.append("ANALYSIS_CONTRACT_INVALID")
    feature_set_count = 0
    feature_set_ids: list[str] = []
    raw_feature_sets = contract.get("feature_sets")
    if raw_feature_sets is not None:
        if not isinstance(raw_feature_sets, list) or not 1 <= len(raw_feature_sets) <= 6:
            codes.append("ANALYSIS_CONTRACT_INVALID")
        else:
            seen_ids: set[str] = set()
            for item in raw_feature_sets:
                if not isinstance(item, dict):
                    codes.append("ANALYSIS_CONTRACT_INVALID")
                    continue
                set_id = item.get("id")
                set_features = item.get("features")
                set_controllable = item.get("controllable_fields", [])
                set_context = item.get("context_fields", [])
                if not isinstance(set_id, str) or not set_id or set_id in seen_ids or not isinstance(set_features, list) or not set_features or not isinstance(set_controllable, list) or not isinstance(set_context, list):
                    codes.append("ANALYSIS_CONTRACT_INVALID")
                    continue
                seen_ids.add(set_id)
                feature_set_ids.append(set_id)
                set_feature_names = {str(name) for name in set_features}
                set_controllable_names = {str(name) for name in set_controllable}
                set_context_names = {str(name) for name in set_context}
                expected_set_features = (set_controllable_names | set_context_names) - split_only - population_filter_names
                if len(set_feature_names) != len(set_features) or not set_feature_names <= set(features) or set_feature_names != expected_set_features or set_controllable_names & set_context_names:
                    codes.append("FIELD_ROLE_FORBIDDEN")
            feature_set_count = len(feature_set_ids)
    constrained = contract.get("constrained_search")
    if constrained is not None:
        bounds = constrained.get("bounds") if isinstance(constrained, dict) else None
        support_groups = constrained.get("support_group_fields", []) if isinstance(constrained, dict) else []
        if not isinstance(constrained, dict) or not isinstance(bounds, dict) or any(name not in controllable for name in bounds) or not isinstance(support_groups, list) or any(name not in context for name in support_groups):
            codes.append("CONSTRAINED_SEARCH_INVALID")
        elif any(not isinstance(value, list) or len(value) != 2 or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value) or value[0] > value[1] for value in bounds.values()):
            codes.append("CONSTRAINED_SEARCH_INVALID")
        else:
            for name, value in bounds.items():
                field = fields[name]
                observed_min, observed_max = field.get("observed_min"), field.get("observed_max")
                if not isinstance(observed_min, (int, float)) or not isinstance(observed_max, (int, float)) or value[0] < observed_min or value[1] > observed_max:
                    codes.append("CONSTRAINED_BOUNDS_OUTSIDE_SUPPORT")

    excluded_roles = {"identifier", "constant", "leakage_risk", "sensitive"}
    excluded = list(dict.fromkeys([*forbidden, *(name for name, field in fields.items() if field.get("analysis_role") in excluded_roles)]))
    unique_codes = list(dict.fromkeys(codes))
    target_resolution = None if target is None else {"field": target_name, "analysis_role": "target", "source": "contract", "data_type": target.get("data_type")}
    return {
        "conforms": not unique_codes,
        "rejection_codes": unique_codes,
        "failed_rules": unique_codes,
        "included_fields": included,
        "excluded_fields": excluded,
        "limitations": [],
        "field_views": list(fields.values()),
        "target_resolution": target_resolution,
        "population_filter": population_filter,
        "feature_set_count": feature_set_count,
        "feature_set_ids": feature_set_ids,
        "snapshot": {"snapshot_id": f"candidate:{hints.get('dataset_id')}", "status": "observed"},
    }


def validate_analysis_contract(hints: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Validate one upload ML contract against declared roles and quality policy."""
    if contract.get("task_kind") == "regression":
        return _validate_regression_contract(hints, contract)
    codes: list[str] = []
    fields = {field["physical_name"]: field for field in hints.get("fields", [])}
    policy = hints.get("quality_policy") or {}
    target_name = contract.get("target")
    target = fields.get(target_name)
    if target is None or target.get("analysis_role") in {"sensitive", "leakage_risk", "constant"}:
        codes.append("TARGET_NOT_APPROVED")
    elif target.get("distinct_count") != 2 or _as_float(target.get("missing_rate"), 0.0) > 0:
        codes.append("TARGET_NOT_BINARY_OR_MISSING")
    features = contract.get("features")
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        codes.append("ANALYSIS_CONTRACT_INVALID")
        features = []
    included: list[str] = []
    for name in features:
        field = fields.get(name)
        if field is None:
            codes.append("UNKNOWN_FIELD")
            continue
        role = field.get("analysis_role")
        if name == target_name:
            codes.append("TARGET_USED_AS_FEATURE")
        elif role == "leakage_risk":
            codes.append("LEAKAGE_FIELD_FORBIDDEN")
        elif role == "sensitive":
            codes.append("SENSITIVE_FIELD_FORBIDDEN")
        elif role not in {"feature"}:
            codes.append("FIELD_ROLE_FORBIDDEN")
        else:
            included.append(name)
    split = contract.get("split")
    if not isinstance(split, dict) or split.get("preprocessing_fit_scope") != "training_only" or split.get("kind") != "stratified_holdout" or split.get("time_field") or split.get("group_field"):
        codes.append("SPLIT_POLICY_VIOLATION")
    if contract.get("kind") not in {"catboost", "random_forest_shap", "gradient_boosting", "logistic_regression", "xgboost"}:
        codes.append("ANALYSIS_CONTRACT_INVALID")
    minority_rate = target.get("minority_rate") if isinstance(target, dict) else None
    rare_positive_rate = _as_float(policy.get("rare_positive_rate"), 0.05)
    if isinstance(minority_rate, (int, float)) and minority_rate < rare_positive_rate and not contract.get("class_imbalance_strategy"):
        codes.append("IMBALANCE_STRATEGY_REQUIRED")
    missing_limit = _as_float(policy.get("missing_rate_max"), 0.4)
    limitations = [name for name in included if _as_float(fields[name].get("missing_rate"), 0.0) > missing_limit]
    excluded = [name for name, field in fields.items() if field.get("analysis_role") in {"identifier", "constant", "leakage_risk", "sensitive"}]
    return {
        "conforms": not codes,
        "rejection_codes": list(dict.fromkeys(codes)),
        "failed_rules": list(dict.fromkeys(codes)),
        "included_fields": included,
        "excluded_fields": excluded,
        "limitations": limitations,
        "snapshot": {"snapshot_id": f"candidate:{hints.get('dataset_id')}", "status": "observed"},
    }


def load_hints(upload_dir: Path) -> dict[str, Any]:
    path = upload_dir / "analysis-hints.json"
    if not path.exists():
        raise FileNotFoundError(f"analysis hints missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid analysis hints JSON at {path}: {exc}") from exc


def feature_allowlist(hints: dict[str, Any]) -> list[str]:
    """Fields eligible as ML features: excludes identifier/constant/target_candidate."""
    return [f["physical_name"] for f in hints["fields"] if f["analysis_role"] in {"feature", "temporal"}]


def primary_target(hints: dict[str, Any]) -> str | None:
    """Observed data does not establish the user's prediction target."""
    return None
