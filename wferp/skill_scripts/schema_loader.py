import json
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _normalize_table_row(row: JsonDict) -> JsonDict:
    out = dict(row)
    out["TableID"] = str(out.get("TableID", "")).strip()
    out["ModuleID"] = str(out.get("ModuleID", "")).strip()
    out["DB"] = str(out.get("DB", "")).strip()
    return out


def _normalize_field_row(row: JsonDict) -> JsonDict:
    out = dict(row)
    out["TableID"] = str(out.get("TableID", "")).strip()
    out["ID"] = str(out.get("ID", "")).strip()
    out["ModuleID"] = str(out.get("ModuleID", "")).strip()
    out["DB"] = str(out.get("DB", "")).strip()
    return out


def _normalize_index_row(row: JsonDict) -> JsonDict:
    out = dict(row)
    out["TableName"] = str(out.get("TableName", "")).strip()
    out["IndexColumnName"] = str(out.get("IndexColumnName", "")).strip()
    out["isPrimaryKey"] = str(out.get("isPrimaryKey", "")).strip()
    return out


def _normalize_module_row(row: JsonDict) -> JsonDict:
    out = dict(row)
    out["ModuleID"] = str(out.get("ModuleID", "")).strip()
    return out


def load_schema_bundle(source_dir: str = "_Source"):
    base = Path(source_dir)
    bundle = {
        "modules": [_normalize_module_row(r) for r in _read_json(base / "MoudleName.json")],
        "tables": [_normalize_table_row(r) for r in _read_json(base / "TableName.json")],
        "fields": [_normalize_field_row(r) for r in _read_json(base / "TableStructure.json")],
        "index_keys": [_normalize_index_row(r) for r in _read_json(base / "TableIndexKey.json")],
    }
    return bundle
