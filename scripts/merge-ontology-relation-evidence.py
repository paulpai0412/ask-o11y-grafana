#!/usr/bin/env python3
"""Merge proposed relation evidence into any ontology Candidate IR."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[reportMissingModuleSource]

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "semantic/schema/candidate-ir.schema.json"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {path}") from exc


def merge(candidate_path: Path, evidence_paths: list[Path]) -> dict[str, Any]:
    candidate = load_json(candidate_path)
    if not isinstance(candidate, dict) or candidate.get("format") != "ask-o11y-ontology-candidate-ir-v1":
        raise ValueError("invalid Candidate IR")
    datasets = candidate.get("datasets")
    relations = candidate.get("relations")
    if not isinstance(datasets, list) or not isinstance(relations, list):
        raise ValueError("invalid Candidate IR collections")
    dataset_ids = {str(dataset["physical_id"]) for dataset in datasets}
    merged = {relation["canonical_id"]: relation for relation in relations}
    if len(merged) != len(relations):
        raise ValueError("Candidate IR contains duplicate relations")
    for path in evidence_paths:
        evidence = load_json(path)
        if not isinstance(evidence, dict) or evidence.get("format") != "ask-o11y-relation-evidence-v1" or evidence.get("status") != "proposed" or not isinstance(evidence.get("relations"), list):
            raise ValueError(f"invalid proposed relation evidence: {path}")
        for relation in evidence["relations"]:
            relation_id = relation["canonical_id"]
            if relation.get("status") != "proposed" or bool(relation.get("executable")):
                raise ValueError(f"relation evidence attempted authorization: {relation_id}")
            if relation.get("from_dataset") not in dataset_ids or relation.get("to_dataset") not in dataset_ids:
                raise ValueError(f"relation evidence references unknown dataset: {relation_id}")
            normalized = {key: value for key, value in relation.items() if key != "evidence"}
            normalized["source"] = {"ref": str(path), **relation.get("evidence", {})}
            previous = merged.get(relation_id)
            if previous is not None and any(previous[key] != normalized[key] for key in ("from_dataset", "to_dataset", "from_fields", "to_fields", "cardinality")):
                raise ValueError(f"relation evidence conflicts with Candidate IR: {relation_id}")
            merged[relation_id] = normalized
    output = {**candidate, "relations": sorted(merged.values(), key=lambda relation: relation["canonical_id"])}
    jsonschema.Draft202012Validator(load_json(SCHEMA)).validate(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--relations", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    candidate = merge(args.candidate, args.relations)
    encoded = canonical_bytes(candidate)
    if args.check and encoded != canonical_bytes(merge(args.candidate, args.relations)):
        raise RuntimeError("relation evidence merge is not reproducible")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(json.dumps({"ok": True, "output": str(args.output), "sha256": hashlib.sha256(encoded).hexdigest(), "datasets": len(candidate["datasets"]), "relations": len(candidate["relations"]), "approved_relations": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
