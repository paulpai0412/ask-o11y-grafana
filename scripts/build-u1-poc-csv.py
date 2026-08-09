#!/usr/bin/env python3
"""Build the Unit 1 operating-data CSV artifact from the source XLSX.

Stdlib-only on purpose: the repo does not carry Python dependencies and the
source workbook is a plain .xlsx zip of XML files.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_XLSX = ROOT / "data" / "2026年機組數據-20260729.xlsx"
OUT_DIR = ROOT / "data" / "poc"
OUT_CSV = OUT_DIR / "u1_operating_daily.csv"
OUT_META = OUT_DIR / "u1_operating_daily.metadata.json"
SHEET_NAME = "工作表-U1"

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# Flat PoC contract. Excel column letters come from the inspected workbook.
FIELD_MAP = [
    ("date", "A", "date", "日期", "", "ISO date"),
    ("unit_id", None, "string", "機組", "", "Fixed value U1"),
    ("heat_rate", "AN", "number", "熱耗率", "kcal/kWh", "Target metric"),
    ("raw_coal_consumption_g", "AL", "number", "原煤耗", "g/kWh", "Direct efficiency factor"),
    ("avg_generation_mw", "AE", "number", "保證時段發電量 average", "MW", "Operating load"),
    ("main_steam_temp_c", "AI", "number", "主蒸汽溫度", "℃", "Process parameter"),
    ("reheat_steam_temp_c", "AJ", "number", "再熱蒸汽溫度", "℃", "Process parameter"),
    ("scr_temp_c", "AK", "number", "09SCR-TTX-0101", "℃", "SCR/flue-gas temperature"),
    ("condenser_vacuum", "BW", "number", "冷凝器真空度", "", "Cold-end parameter"),
    ("scr_inlet_nox", "BU", "number", "SCR 入口 NOx", "", "Emission companion parameter"),
    ("scr_inlet_co", "BV", "number", "SCR 入口 CO", "", "Combustion companion parameter"),
    ("condenser_outlet_water_temp", "BX", "number", "冷凝器出口水溫", "℃", "Cold-end companion parameter"),
    ("burner_angle", "AH", "number", "燃燒器角度", "degree", "Combustion setting"),
    ("overfire_air_fcp", "CC", "number", "火上風門開度設定值 FCP", "", "Combustion setting"),
    ("overfire_air_fcs", "CD", "number", "火上風門開度設定值 FCS", "", "Combustion setting"),
    ("overfire_air_fct", "CE", "number", "火上風門開度設定值 FCT", "", "Combustion setting"),
    ("overfire_air_fcf", "CF", "number", "火上風門開度設定值 FCF", "", "Combustion setting"),
    ("coal_avg_heat_value_kcal_kg", "AD", "number", "一號機煤炭平均熱值 全時段", "kcal/kg", "Fuel quality"),
    ("unburned_carbon_pct", "AM", "number", "未燃碳 飛灰(保證)", "%", "Combustion efficiency companion"),
]
OUTPUT_FIELDS = [name for name, *_ in FIELD_MAP] + ["heat_rate_valid", "data_quality_note"]
REQUIRED_FIELDS = [
    "date",
    "unit_id",
    "heat_rate",
    "raw_coal_consumption_g",
    "avg_generation_mw",
    "main_steam_temp_c",
    "reheat_steam_temp_c",
    "scr_temp_c",
    "condenser_vacuum",
]
OPTIONAL_FIELDS = [f for f in OUTPUT_FIELDS if f not in REQUIRED_FIELDS and f not in {"heat_rate_valid", "data_quality_note"}]


def col_to_num(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + ord(ch.upper()) - 64
    return n


def cell_col(cell_ref: str) -> int:
    m = re.match(r"([A-Z]+)", cell_ref)
    if not m:
        raise ValueError(f"bad cell ref: {cell_ref}")
    return col_to_num(m.group(1))


def excel_date(serial: str) -> str:
    try:
        days = float(serial)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad Excel date serial: {serial!r}") from exc
    return (dt.datetime(1899, 12, 30) + dt.timedelta(days=days)).date().isoformat()


def clean_number(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        f = float(value)
    except ValueError:
        return str(value)
    if math.isfinite(f) and f.is_integer():
        try:
            return str(int(f))
        except OverflowError:
            return repr(f)
    return repr(f)


def as_float(value: str | None) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except ValueError:
        return None


def read_xlsx_sheet(path: Path, sheet_name: str) -> dict[int, dict[int, str]]:
    with ZipFile(path) as zf:
        names = set(zf.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", NS):
                shared.append("".join(t.text or "" for t in si.findall(".//a:t", NS)))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        target = None
        for sheet in workbook.findall(".//a:sheet", NS):
            if sheet.attrib["name"] == sheet_name:
                rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                target = relmap[rid]
                break
        if target is None:
            raise SystemExit(f"sheet not found: {sheet_name}")
        sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        root = ET.fromstring(zf.read(sheet_path))

        def value(cell: ET.Element) -> str:
            typ = cell.attrib.get("t")
            if typ == "s":
                v = cell.find("a:v", NS)
                if v is None or v.text is None:
                    return ""
                try:
                    return shared[int(v.text)]
                except (ValueError, IndexError) as exc:
                    raise ValueError(f"bad shared string index: {v.text!r}") from exc
            if typ == "inlineStr":
                return "".join(t.text or "" for t in cell.findall(".//a:t", NS))
            v = cell.find("a:v", NS)
            return v.text if v is not None and v.text is not None else ""

        rows: dict[int, dict[int, str]] = {}
        for row in root.findall(".//a:sheetData/a:row", NS):
            try:
                r = int(row.attrib["r"])
            except (KeyError, ValueError) as exc:
                raise ValueError(f"bad row reference: {row.attrib!r}") from exc
            rows.setdefault(r, {})
            for cell in row.findall("a:c", NS):
                rows[r][cell_col(cell.attrib["r"])] = value(cell)
        return rows


def build_rows(sheet_rows: dict[int, dict[int, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row_no in sorted(r for r in sheet_rows if r >= 5):
        raw_date = sheet_rows[row_no].get(col_to_num("A"), "")
        if not raw_date:
            continue
        rec: dict[str, str] = {"unit_id": "U1"}
        for name, col, *_ in FIELD_MAP:
            if name == "unit_id":
                continue
            if name == "date":
                rec[name] = excel_date(raw_date)
            elif col:
                rec[name] = clean_number(sheet_rows[row_no].get(col_to_num(col)))
        heat_rate = as_float(rec.get("heat_rate"))
        if heat_rate is None:
            valid, note = "false", "blank_heat_rate"
        elif heat_rate <= 0:
            valid, note = "false", "zero_or_negative_heat_rate"
        else:
            valid, note = "true", ""
        rec["heat_rate_valid"] = valid
        rec["data_quality_note"] = note
        out.append({field: rec.get(field, "") for field in OUTPUT_FIELDS})
    return out


def write_artifacts(rows: list[dict[str, str]]) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    valid_rows = [r for r in rows if r["heat_rate_valid"] == "true"]
    zero_rows = [r for r in rows if r["data_quality_note"] == "zero_or_negative_heat_rate"]
    blank_rows = [r for r in rows if r["data_quality_note"] == "blank_heat_rate"]
    metadata: dict[str, Any] = {
        "profile": "u1-operating-daily",
        "raw_file": str(SOURCE_XLSX.relative_to(ROOT)),
        "source_sheet": SHEET_NAME,
        "csv_file": str(OUT_CSV.relative_to(ROOT)),
        "datasource_uid": "csv-poc",
        "unit_id": "U1",
        "time_field": "date",
        "unit_field": "unit_id",
        "required_fields": REQUIRED_FIELDS,
        "optional_fields": OPTIONAL_FIELDS,
        "minimum_rows": 100,
        "units": {name: unit for name, _col, _typ, _display, unit, _desc in FIELD_MAP},
        "row_counts": {
            "total": len(rows),
            "valid_heat_rate": len(valid_rows),
            "zero_or_negative_heat_rate": len(zero_rows),
            "blank_heat_rate": len(blank_rows),
        },
        "date_range": {
            "all_from": rows[0]["date"],
            "all_to": rows[-1]["date"],
            "valid_from": valid_rows[0]["date"],
            "valid_to": valid_rows[-1]["date"],
        },
        "fields": [
            {
                "name": name,
                "source_column": col,
                "type": typ,
                "display_name": display,
                "unit": unit,
                "description": desc,
                "aliases": [display, name],
            }
            for name, col, typ, display, unit, desc in FIELD_MAP
        ] + [
            {"name": "heat_rate_valid", "source_column": None, "type": "boolean", "display_name": "熱耗率有效", "unit": "", "description": "true when heat_rate is positive", "aliases": ["有效熱耗率"]},
            {"name": "data_quality_note", "source_column": None, "type": "string", "display_name": "資料品質註記", "unit": "", "description": "blank/zero heat-rate marker", "aliases": ["資料品質"]},
        ],
    }
    OUT_META.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def self_check(rows: list[dict[str, str]], metadata: dict[str, Any]) -> None:
    if len(rows) != 365:
        raise RuntimeError(f"expected 365 date rows, got {len(rows)}")
    counts = metadata["row_counts"]
    expected_counts = {"valid_heat_rate": 172, "zero_or_negative_heat_rate": 37, "blank_heat_rate": 156}
    for key, expected in expected_counts.items():
        if counts[key] != expected:
            raise RuntimeError(f"bad {key}: {counts}")
    if rows[0]["date"] != "2026-01-01" or rows[-1]["date"] != "2026-12-31":
        raise RuntimeError(f"bad date conversion: {rows[0]} / {rows[-1]}")
    if rows[0]["unit_id"] != "U1":
        raise RuntimeError(f"bad unit_id: {rows[0]}")
    heat_rate = as_float(rows[0]["heat_rate"])
    main_steam_temp = as_float(rows[0]["main_steam_temp_c"])
    if heat_rate is None or abs(heat_rate - 8423.253614815063) >= 1e-9:
        raise RuntimeError(f"bad representative heat_rate: {rows[0]['heat_rate']}")
    if main_steam_temp is None or abs(main_steam_temp - 526.1359005975662) >= 1e-9:
        raise RuntimeError(f"bad representative main_steam_temp_c: {rows[0]['main_steam_temp_c']}")
    missing = [f for f in REQUIRED_FIELDS if f not in rows[0]]
    if missing:
        raise RuntimeError(f"missing required fields: {missing}")


def main() -> int:
    global SOURCE_XLSX
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE_XLSX)
    parser.add_argument("--check", action="store_true", help="run deterministic self-checks after writing artifacts")
    args = parser.parse_args()
    SOURCE_XLSX = args.source
    rows = build_rows(read_xlsx_sheet(SOURCE_XLSX, SHEET_NAME))
    metadata = write_artifacts(rows)
    if args.check:
        self_check(rows, metadata)
    print(json.dumps({
        "csv": str(OUT_CSV),
        "metadata": str(OUT_META),
        "row_counts": metadata["row_counts"],
        "date_range": metadata["date_range"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
