#!/usr/bin/env python3
"""Validate one ontology registry and emit a reproducible immutable snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[reportMissingModuleSource]
import yaml  # type: ignore[reportMissingModuleSource]

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCHEMA = ROOT / "semantic/schema/registry.schema.json"
GENERIC_SCHEMA = ROOT / "semantic/schema/generic-registry.schema.json"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_registry(registry: dict[str, Any], schema: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(registry)
    dataset_ids: set[str] = set()
    for dataset in registry["datasets"]:
        canonical_id = str(dataset["canonical_id"])
        if canonical_id in dataset_ids:
            raise ValueError(f"duplicate dataset: {canonical_id}")
        dataset_ids.add(canonical_id)
        fields = dataset["fields"]
        by_name = {field["physical_name"]: field for field in fields}
        if len(by_name) != len(fields):
            raise ValueError(f"duplicate physical field in {dataset['physical_id']}")
        missing_keys = sorted(set(dataset["entity_key"]) - by_name.keys())
        if missing_keys:
            raise ValueError(f"dataset entity_key references missing fields: {missing_keys}")
        time_identity = dataset.get("time_identity")
        if time_identity and time_identity not in by_name:
            raise ValueError(f"dataset time_identity references missing field: {time_identity}")
        if "target" not in dataset:
            for relation in dataset.get("relations", []):
                if relation["status"] != "approved" and relation["executable"]:
                    raise ValueError("non-approved relation cannot be executable")
            continue
        required = {dataset["target"], dataset["quality_policy"]["field"]}
        missing = sorted(required - by_name.keys())
        if missing:
            raise ValueError(f"dataset contract references missing fields: {missing}")
        target = by_name[dataset["target"]]
        if target["status"] != "approved" or target["analysis_role"] != "target":
            raise ValueError("target must be approved with target role")
        for name in dataset["approved_features"]:
            field = by_name.get(name)
            if field is None:
                raise ValueError(f"approved feature is missing: {name}")
            if field["status"] != "approved" or field["analysis_role"] != "feature":
                raise ValueError(f"approved feature has unsafe status/role: {name}")
            if not field["availability"]["eligible_at_as_of"] or field["unit"] is None:
                raise ValueError(f"approved feature lacks availability/unit: {name}")
        for field in fields:
            if field["semantic_kind"] == "unknown" and field["status"] == "approved":
                raise ValueError(f"unknown semantics cannot be approved: {field['physical_name']}")
            if field["semantic_kind"] == "target_proxy" and (field["status"] == "approved" or field["analysis_role"] != "forbidden"):
                raise ValueError(f"unresolved target proxy must remain forbidden: {field['physical_name']}")


def build_snapshot(registry_path: Path) -> dict[str, Any]:
    registry_bytes = registry_path.read_bytes()
    registry = yaml.safe_load(registry_bytes)
    if not isinstance(registry, dict):
        raise ValueError("registry root must be an object")
    schema_path = GENERIC_SCHEMA if "namespace" in registry else LEGACY_SCHEMA
    schema_bytes = schema_path.read_bytes()
    try:
        schema = json.loads(schema_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("ontology registry schema is invalid JSON") from exc
    validate_registry(registry, schema)
    payload = {
        "format": "ask-o11y-ontology-snapshot-v1",
        "registry": registry,
        "registry_sha256": sha256(registry_bytes),
        "schema_sha256": sha256(schema_bytes),
    }
    return {**payload, "snapshot_sha256": sha256(canonical_bytes(payload))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="also prove repeated builds are byte-identical")
    args = parser.parse_args()
    snapshot = build_snapshot(args.registry)
    output = args.output or ROOT / "semantic/snapshots" / f"{snapshot['registry']['snapshot_id']}.json"
    encoded = canonical_bytes(snapshot)
    if args.check and encoded != canonical_bytes(build_snapshot(args.registry)):
        raise RuntimeError("ontology snapshot build is not reproducible")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    print(json.dumps({"ok": True, "output": str(output), "snapshot_id": snapshot["registry"]["snapshot_id"], "sha256": snapshot["snapshot_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
