#!/usr/bin/env python3
"""Apply explicit reviewed relation approvals to any Candidate IR."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[reportMissingModuleSource]
import yaml  # type: ignore[reportMissingModuleSource]

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCHEMA = ROOT / "semantic/schema/generic-registry.schema.json"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {path}") from exc


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_bytes())
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid YAML input: {path}") from exc


def build_snapshot(candidate_path: Path, approvals_path: Path, snapshot_id: str) -> dict[str, Any]:
    candidate, approvals = load_json(candidate_path), load_yaml(approvals_path)
    if not isinstance(candidate, dict) or candidate.get("format") != "ask-o11y-ontology-candidate-ir-v1":
        raise ValueError("invalid Candidate IR")
    if not isinstance(approvals, dict) or approvals.get("status") != "approved" or not isinstance(approvals.get("relations"), list):
        raise ValueError("invalid approval manifest")
    datasets, candidates = candidate.get("datasets"), candidate.get("relations")
    if not isinstance(datasets, list) or not isinstance(candidates, list):
        raise ValueError("invalid Candidate IR collections")
    statuses = {dataset.get("status") for dataset in datasets if isinstance(dataset, dict)} | {field.get("status") for dataset in datasets if isinstance(dataset, dict) for field in dataset.get("fields", []) if isinstance(field, dict)}
    if statuses - {"observed", "proposed"} or any(relation.get("status") != "proposed" or bool(relation.get("executable")) for relation in candidates if isinstance(relation, dict)):
        raise ValueError("Candidate IR attempted to authorize imported semantics")
    dataset_ids = {str(dataset["physical_id"]) for dataset in datasets}
    candidate_by_id = {relation["canonical_id"]: relation for relation in candidates}
    approved_by_id = {relation["canonical_id"]: relation for relation in approvals["relations"]}
    if len(approved_by_id) != len(approvals["relations"]):
        raise ValueError("duplicate approved relation")
    for relation_id, approved in approved_by_id.items():
        if approved.get("status") != "approved" or not bool(approved.get("executable")):
            raise ValueError(f"approved relation is not executable: {relation_id}")
        if approved.get("from_dataset") not in dataset_ids or approved.get("to_dataset") not in dataset_ids:
            raise ValueError(f"approved relation references unknown dataset: {relation_id}")
        candidate_relation = candidate_by_id.get(relation_id)
        if candidate_relation is not None and any(candidate_relation[key] != approved[key] for key in ("from_dataset", "to_dataset", "from_fields", "to_fields", "cardinality")):
            raise ValueError(f"approved relation conflicts with imported candidate: {relation_id}")
    released_relations = [{**relation, **approved_by_id.pop(relation["canonical_id"])} if relation["canonical_id"] in approved_by_id else relation for relation in candidates]
    released_relations.extend(approved_by_id.values())
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for relation in released_relations:
        by_dataset.setdefault(str(relation["from_dataset"]), []).append(relation)
    registry = {
        "registry_version": str(approvals.get("registry_version", "0.1.0")),
        "snapshot_id": snapshot_id,
        "namespace": str(candidate["namespace"]),
        "status": "approved",
        "provenance": {"registry_path": str(candidate_path), "source_refs": [*candidate["source_snapshot"]["refs"], str(approvals_path)], "owner": str(approvals["owner"]), "effective_from": str(approvals["effective_from"])},
        "limitations": candidate["limitations"],
        "datasets": [{**dataset, "relations": by_dataset.get(str(dataset["physical_id"]), [])} for dataset in datasets],
    }
    jsonschema.Draft202012Validator(load_json(REGISTRY_SCHEMA)).validate(registry)
    candidate_bytes = canonical_bytes(candidate)
    payload = {"format": "ask-o11y-ontology-snapshot-v1", "registry": registry, "registry_sha256": hashlib.sha256(candidate_bytes).hexdigest(), "schema_sha256": hashlib.sha256(REGISTRY_SCHEMA.read_bytes()).hexdigest()}
    return {**payload, "snapshot_sha256": hashlib.sha256(canonical_bytes(payload)).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    snapshot = build_snapshot(args.candidate, args.approvals, args.snapshot_id)
    encoded = canonical_bytes(snapshot)
    if args.check and encoded != canonical_bytes(build_snapshot(args.candidate, args.approvals, args.snapshot_id)):
        raise RuntimeError("ontology relation promotion is not reproducible")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    relations = [relation for dataset in snapshot["registry"]["datasets"] for relation in dataset.get("relations", [])]
    print(json.dumps({"ok": True, "output": str(args.output), "sha256": snapshot["snapshot_sha256"], "datasets": len(snapshot["registry"]["datasets"]), "relations": len(relations), "approved_relations": sum(relation["status"] == "approved" and bool(relation["executable"]) for relation in relations)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
