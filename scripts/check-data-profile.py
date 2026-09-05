#!/usr/bin/env python3
"""Red-first contract check for the real uploaded Vestas data profile.

This check deliberately reads the repository's real 10,000-row source. It does
not construct a fixture, mock a frame, or substitute a fallback dataset.
"""
from __future__ import annotations

import csv
import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".scratch/huggingface/vestas_high_wind_power_regulation.csv"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_real_source() -> tuple[list[str], dict[str, list[str]]]:
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        columns = {name: [] for name in headers}
        for row in reader:
            for name in headers:
                columns[name].append(row[name])
    return headers, columns


def main() -> int:
    profile = load_module("data_profile_check", ROOT / "sandbox-analysis-mcp/data_profile.py")
    headers, columns = read_real_source()
    assert len(columns[headers[0]]) == 10000, "the regression source must remain complete"
    field_views = [
        {"physical_name": name, "type": "date" if name == headers[0] else "number", "analysis_role": "feature"}
        for name in headers
    ]
    result = profile.profile_columns(columns, fields_view=field_views, ontology_status="observed")
    assert result["rows"] == 10000
    assert result["profiled_rows"] == 10000
    assert result["columns"] == len(headers)
    assert [item["name"] for item in result["fields"]] == headers
    try:
        power = [float(value) for value in columns["Power"]]
    except (TypeError, ValueError) as exc:
        raise AssertionError("real Vestas Power column is not numeric") from exc
    power_entry = next(item for item in result["fields"] if item["name"] == "Power")
    assert power_entry["numeric"]["count"] == len(power)
    assert math.isclose(power_entry["numeric"]["min"], min(power), rel_tol=0, abs_tol=1e-4)
    assert math.isclose(power_entry["numeric"]["max"], max(power), rel_tol=0, abs_tol=1e-4)
    assert len(result["correlation"]["columns"]) <= 12
    assert len(result["correlation"]["matrix"]) == len(result["correlation"]["columns"])

    manifest = profile.build_profile_manifest(
        result,
        identity={"dataset_id": "from-real-upload"},
        purpose="real source profile",
        conclusion="descriptive evidence only",
    )
    profile.validate_profile_manifest(manifest)
    assert not manifest["data"]["derived_dataset"]
    assert result["temporal_fields"]
    assert "profiled_rows" in (ROOT / "sandbox-analysis-mcp/data_profile.py").read_text(encoding="utf-8")
    print("ok: real Vestas 10,000-row profile contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
