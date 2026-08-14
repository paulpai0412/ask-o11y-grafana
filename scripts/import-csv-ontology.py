#!/usr/bin/env python3
"""Import a CSV header and bounded type sample as non-authoritative ontology candidates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[reportMissingModuleSource]

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "semantic/schema/candidate-ir.schema.json"
MAX_SAMPLE_ROWS = 1000


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-.")
    if not normalized:
        raise ValueError("dataset_id/namespace must contain a portable identifier")
    return normalized


def infer_scalar(values: list[str]) -> str:
    nonempty = [value.strip() for value in values if value.strip()]
    if not nonempty:
        return "string"
    lowered = {value.casefold() for value in nonempty}
    if lowered <= {"true", "false"}:
        return "boolean"
    try:
        for value in nonempty:
            int(value)
        return "integer"
    except ValueError:
        pass
    try:
        for value in nonempty:
            float(value)
        return "number"
    except ValueError:
        pass
    try:
        for value in nonempty:
            date.fromisoformat(value)
        return "date"
    except ValueError:
        pass
    try:
        for value in nonempty:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "datetime"
    except ValueError:
        return "string"


def semantic_kind(name: str, scalar_type: str) -> str:
    lowered = name.casefold()
    if scalar_type in {"date", "datetime"} or any(token in lowered for token in ("date", "time", "timestamp")):
        return "temporal"
    if lowered == "id" or lowered.endswith("_id"):
        return "identifier"
    if scalar_type in {"integer", "number"}:
        return "measurement"
    return "dimension"


def build_candidate(csv_path: Path, dataset_id: str, namespace: str) -> dict[str, Any]:
    try:
        raw = csv_path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"CSV is unavailable or not UTF-8: {csv_path}") from exc
    rows = csv.reader(text.splitlines())
    try:
        headers = next(rows)
    except StopIteration as exc:
        raise ValueError("CSV is empty") from exc
    if not headers or any(not header.strip() for header in headers) or len(headers) != len(set(headers)):
        raise ValueError("CSV headers must be non-empty and unique")
    samples = [[] for _ in headers]
    sampled_rows = 0
    for row in rows:
        if len(row) != len(headers):
            raise ValueError("CSV row width does not match headers")
        for index, value in enumerate(row):
            samples[index].append(value)
        sampled_rows += 1
        if sampled_rows >= MAX_SAMPLE_ROWS:
            break
    dataset_slug = slug(dataset_id)
    fields = []
    for header, values in zip(headers, samples, strict=True):
        scalar_type = infer_scalar(values)
        fields.append({"canonical_id": f"field.{dataset_slug}.{slug(header)}", "physical_name": header, "type": scalar_type, "unit": None, "semantic_kind": semantic_kind(header, scalar_type), "status": "observed", "reason": "CSV header and bounded primitive type sample observed; business semantics and unit are not automatically approved.", "source": {"sampled_nonempty_values": sum(bool(value.strip()) for value in values), "sample_limit": MAX_SAMPLE_ROWS}})
    candidate = {"format": "ask-o11y-ontology-candidate-ir-v1", "namespace": slug(namespace), "source_snapshot": {"kind": "csv-header-and-type-sample", "refs": [str(csv_path)], "sha256": hashlib.sha256(raw).hexdigest()}, "datasets": [{"canonical_id": f"dataset.{dataset_slug}", "physical_id": dataset_id, "asset_kind": "tabular_file", "status": "observed", "grain": "one physical CSV row; business grain is unapproved", "entity_key": [], "fields": fields, "source": {"sampled_rows": sampled_rows, "total_rows_not_asserted": True}}], "relations": [], "limitations": [f"Type inference sampled at most {MAX_SAMPLE_ROWS} rows.", "No business role, unit, key, metric, relation, or approval was inferred.", "No datasource credentials or runtime query were used."]}
    try:
        schema = json.loads(SCHEMA.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Candidate IR schema is unavailable or invalid") from exc
    jsonschema.Draft202012Validator(schema).validate(candidate)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    candidate = build_candidate(args.csv, args.dataset_id, args.namespace)
    encoded = canonical_bytes(candidate)
    if args.check and encoded != canonical_bytes(build_candidate(args.csv, args.dataset_id, args.namespace)):
        raise RuntimeError("CSV candidate import is not reproducible")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(json.dumps({"ok": True, "output": str(args.output), "sha256": hashlib.sha256(encoded).hexdigest(), "datasets": 1, "fields": len(candidate["datasets"][0]["fields"]), "approved_candidates": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
