#!/usr/bin/env python3
"""Convert SQL Server catalog JSON into proposed relation evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid SQL Server catalog JSON: {path}") from exc


def build_manifest(catalog_path: Path, owner: str, effective_from: str) -> dict[str, Any]:
    catalog = load_json(catalog_path)
    rows = catalog.get("foreign_keys") if isinstance(catalog, dict) else None
    if not isinstance(rows, list):
        raise ValueError("catalog JSON must contain foreign_keys")
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("foreign key row must be an object")
        key = (str(row["schema"]), str(row["constraint_name"]), str(row["from_table"]), str(row["to_table"]))
        grouped.setdefault(key, []).append(row)
    relations = []
    for (schema, constraint, source, target), columns in sorted(grouped.items()):
        try:
            ordered = sorted(columns, key=lambda item: int(item["ordinal"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid ordinal for foreign key {constraint}") from exc
        relations.append({"canonical_id": f"relation.{schema.casefold()}.{source.casefold()}-{target.casefold()}-{constraint.casefold()}", "from_dataset": target, "to_dataset": source, "from_fields": [str(item["to_column"]) for item in ordered], "to_fields": [str(item["from_column"]) for item in ordered], "cardinality": "1:N", "status": "proposed", "executable": False, "reason": f"Observed checked SQL Server foreign key {constraint}; explicit review is still required.", "evidence": {"kind": "sqlserver_foreign_key", "schema": schema, "constraint": constraint, "is_disabled": any(bool(item.get("is_disabled")) for item in ordered), "is_not_trusted": any(bool(item.get("is_not_trusted")) for item in ordered)}})
    return {"format": "ask-o11y-relation-evidence-v1", "status": "proposed", "owner": owner, "effective_from": effective_from, "source_ref": str(catalog_path), "relations": relations}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--effective-from", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest(args.catalog, args.owner, args.effective_from)
    encoded = canonical_bytes(manifest)
    if args.check and encoded != canonical_bytes(build_manifest(args.catalog, args.owner, args.effective_from)):
        raise RuntimeError("SQL Server relation import is not reproducible")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(json.dumps({"ok": True, "output": str(args.output), "relations": len(manifest["relations"]), "approved_relations": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
