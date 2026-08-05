from collections import defaultdict
from typing import Any


JsonDict = dict[str, Any]


def _table_field_ref(row: JsonDict) -> str:
    table = str(row.get("TableID", "")).strip()
    field = str(row.get("ID", "")).strip()
    return f"{table}.{field}"


def build_field_index(fields: list[JsonDict]) -> dict[str, list[str]]:
    out = defaultdict(list)
    for row in fields:
        field_id = str(row.get("ID", "")).strip()
        if not field_id:
            continue
        out[field_id].append(_table_field_ref(row))
    return {k: v for k, v in out.items()}


def build_alias_index(fields: list[JsonDict]) -> dict[str, list[str]]:
    out = defaultdict(list)
    for row in fields:
        ref = _table_field_ref(row)
        for key in ("ID", "FieldName", "NameVietnam"):
            alias = str(row.get(key, "")).strip().lower()
            if not alias:
                continue
            if ref not in out[alias]:
                out[alias].append(ref)
    return {k: v for k, v in out.items()}
