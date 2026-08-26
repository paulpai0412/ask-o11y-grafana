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

ROOT = Path(__file__).resolve().parent
MAX_SAMPLE_ROWS = 1000
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
                continue
            for index, value in enumerate(row):
                samples[index].append(value)
            rows_read += 1
            if rows_read >= MAX_SAMPLE_ROWS:
                break
    return headers, samples, rows_read


def _analysis_roles(headers: list[str], samples: list[list[str]], rows_read: int) -> list[str]:
    """Deterministic roles: identifier / temporal / target_candidate / constant / feature."""
    builder = _load_builder()
    n = max(rows_read, 1)
    roles: list[str] = []
    categorical_candidates: list[int] = []
    for header, values in zip(headers, samples, strict=True):
        present = [value for value in values if not _is_missing(value)]
        distinct = len(set(present))
        scalar_type = builder.infer_scalar(present)
        base_kind = builder.semantic_kind(header, scalar_type)
        lowered = header.casefold()
        if any(token in lowered for token in LEAKAGE_NAME_TOKENS):
            roles.append("leakage_risk")  # post-outcome signals; deterministic exclusion
            continue
        if any(token in lowered for token in SENSITIVE_NAME_TOKENS):
            roles.append("sensitive")  # protected attributes; governance approval required
            continue
        if base_kind == "temporal":
            roles.append("temporal")
            continue
        if lowered == "id" or lowered.endswith("_id"):
            roles.append("identifier")
            continue
        if any(token in lowered for token in ("weight", "wgt")):
            roles.append("identifier")  # sampling/frequency weights are not predictive features
            continue
        if scalar_type in {"integer", "number"} and present:
            # ponytail: magnitude heuristic — all-unique large-magnitude numerics are
            # ids/weights/serials; small-valued measures (age, temp) stay features.
            # Revisit with column-name embedding if false positives appear.
            try:
                magnitudes = [abs(float(value)) for value in present]
            except ValueError:
                magnitudes = []
            if magnitudes and len(set(present)) / max(len(present), 1) >= 0.95 and max(magnitudes) >= 1000:
                roles.append("identifier")
                continue
        if distinct <= 20:
            categorical_candidates.append(len(roles))
            roles.append("feature")  # provisional; last one below becomes target_candidate
            continue
        roles.append("feature")
    if categorical_candidates:
        roles[categorical_candidates[-1]] = "target_candidate"
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
        field = {
            "physical_name": header,
            "analysis_role": role,
            "missing_rate": round(missing / max(len(values), 1), 4),
            "sampled_rows": len(values),
        }
        counts = Counter(present)
        if role == "target_candidate" and len(counts) == 2:
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


def validate_analysis_contract(hints: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Validate one upload ML contract against declared roles and quality policy."""
    codes: list[str] = []
    fields = {field["physical_name"]: field for field in hints.get("fields", [])}
    policy = hints.get("quality_policy") or {}
    target_name = contract.get("target")
    target = fields.get(target_name)
    if target is None or target.get("analysis_role") != "target_candidate":
        codes.append("TARGET_NOT_APPROVED")
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
        if role == "leakage_risk":
            codes.append("LEAKAGE_FIELD_FORBIDDEN")
        elif role == "sensitive":
            codes.append("SENSITIVE_FIELD_FORBIDDEN")
        elif role not in {"feature", "temporal"}:
            codes.append("FIELD_ROLE_FORBIDDEN")
        else:
            included.append(name)
    split = contract.get("split")
    if not isinstance(split, dict) or split.get("preprocessing_fit_scope") != "training_only":
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
    for field in hints["fields"]:
        if field["analysis_role"] == "target_candidate":
            return field["physical_name"]
    return None
