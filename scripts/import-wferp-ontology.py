#!/usr/bin/env python3
"""Import the complete WFERP metadata bundle as non-authoritative ontology candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[reportMissingModuleSource]

ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "data-query-planner-mcp/metadata/wferp"
SCHEMA = ROOT / "semantic/schema/candidate-ir.schema.json"
DEFAULT_OUTPUT = ROOT / "semantic/candidates/wferp.json"
SOURCE_FILES = ("schema_bundle.json", "primary_key_map.json", "relationship_edges.json")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def source_hash() -> str:
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        raw = (METADATA / name).read_bytes()
        digest.update(name.encode() + b"\0" + raw + b"\0")
    return digest.hexdigest()


def field_type(raw: str) -> str:
    return {"N": "number", "I": "integer", "D": "date", "T": "datetime"}.get(raw, "string")


def semantic_kind(field: dict[str, Any], key_fields: set[str]) -> str:
    field_id = str(field["ID"])
    description = f"{field.get('FieldName') or ''} {field.get('Description') or ''}".casefold()
    if field_id in key_fields:
        return "identifier"
    if "date" in description or "日期" in description or "時間" in description or "formate:ymd" in description:
        return "temporal"
    if str(field.get("Type")) in {"N", "I"}:
        return "measurement"
    return "dimension"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid ontology import input: {path}") from exc


def build_candidate_ir() -> dict[str, Any]:
    bundle = load_json(METADATA / "schema_bundle.json")
    primary_keys = load_json(METADATA / "primary_key_map.json")
    raw_relations = load_json(METADATA / "relationship_edges.json")
    fields_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in bundle["fields"]:
        fields_by_table[str(field["TableID"]).upper()].append(field)
    datasets = []
    for table in sorted(bundle["tables"], key=lambda item: str(item["TableID"])):
        table_id = str(table["TableID"]).upper()
        keys = [str(value).upper() for value in primary_keys.get(table_id, [])]
        key_set = set(keys)
        fields = []
        for field in sorted(fields_by_table[table_id], key=lambda item: (str(item.get("sID") or ""), str(item["ID"]))):
            field_id = str(field["ID"]).upper()
            fields.append({
                "canonical_id": f"field.wferp.{table_id.lower()}.{field_id.lower()}",
                "physical_name": field_id,
                "type": field_type(str(field.get("Type") or "")),
                "unit": None,
                "semantic_kind": semantic_kind(field, key_set),
                "status": "observed",
                "reason": "Physical column and descriptive metadata observed; business semantics and unit are not automatically approved.",
                "source": {"name": field.get("FieldName"), "name_vietnamese": field.get("NameVietnam"), "description": field.get("Description"), "physical_type": field.get("Type"), "length": field.get("Length")},
            })
        datasets.append({
            "canonical_id": f"dataset.wferp.{table_id.lower()}",
            "physical_id": table_id,
            "asset_kind": "sql_table",
            "status": "observed",
            "grain": f"one physical row in WFERP table {table_id}; business grain is unapproved",
            "entity_key": keys,
            "fields": fields,
            "source": {"database": table.get("DB"), "name": table.get("TableName"), "name_vietnamese": table.get("TableNameViet"), "module_id": table.get("ModuleID"), "module_name": table.get("ModuleName")},
        })
    relations = []
    for edge in sorted(raw_relations, key=lambda item: (str(item["from_table"]), str(item["to_table"]), tuple(item["from_columns"]))):
        source, target = str(edge["from_table"]).upper(), str(edge["to_table"]).upper()
        relations.append({
            "canonical_id": f"relation.wferp.{source.lower()}-{target.lower()}",
            "from_dataset": source,
            "to_dataset": target,
            "from_fields": [str(value).upper() for value in edge["from_columns"]],
            "to_fields": [str(value).upper() for value in edge["to_columns"]],
            "cardinality": edge.get("cardinality", "unknown"),
            "status": "proposed",
            "executable": False,
            "reason": str(edge.get("reason") or "Heuristic relation requires steward approval."),
            "source": {"confidence": edge.get("confidence"), "ref": "data-query-planner-mcp/metadata/wferp/relationship_edges.json"},
        })
    candidate = {
        "format": "ask-o11y-ontology-candidate-ir-v1",
        "namespace": "erp.wferp",
        "source_snapshot": {"kind": "wferp-metadata-bundle", "refs": [f"data-query-planner-mcp/metadata/wferp/{name}" for name in SOURCE_FILES], "sha256": source_hash()},
        "datasets": datasets,
        "relations": relations,
        "limitations": [
            "Physical metadata is observed, not steward-approved business semantics.",
            "Missing primary keys remain empty instead of being guessed.",
            "All relationship edges are medium-confidence heuristics and remain proposed/non-executable.",
            "No raw datasource rows were read or profiled.",
        ],
    }
    schema = load_json(SCHEMA)
    jsonschema.Draft202012Validator(schema).validate(candidate)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    candidate = build_candidate_ir()
    encoded = canonical_bytes(candidate)
    if args.check and encoded != canonical_bytes(build_candidate_ir()):
        raise RuntimeError("WFERP candidate import is not reproducible")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(json.dumps({"ok": True, "output": str(args.output), "sha256": hashlib.sha256(encoded).hexdigest(), "datasets": len(candidate["datasets"]), "fields": sum(len(item["fields"]) for item in candidate["datasets"]), "relations": len(candidate["relations"]), "approved_candidates": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
