from collections import defaultdict
from typing import Any

from skill_scripts.relationship_graph import infer_relationships


JsonDict = dict[str, Any]


def _normalize_text(value: str) -> str:
    return str(value or "").strip().lower()


def _table_score(prompt: str, table_row: JsonDict, module_map: dict[str, JsonDict]) -> int:
    p = _normalize_text(prompt)
    score = 0

    table_id = _normalize_text(table_row.get("TableID", ""))
    if table_id and table_id in p:
        score += 120

    for key in ("TableName", "TableNameViet"):
        name = _normalize_text(table_row.get(key, ""))
        if name and name in p:
            score += 90

    module_id = _normalize_text(table_row.get("ModuleID", ""))
    if module_id and module_id in p:
        score += 30

    module_row = module_map.get(module_id.upper(), {})
    for key in ("ModuleName", "ModuleNameViet"):
        value = _normalize_text(module_row.get(key, ""))
        if value and value in p:
            score += 25

    if "預算" in prompt and table_id in {"actmk", "actmj", "actmi"}:
        score += 80
    if "明細" in prompt and table_id == "actmk":
        score += 80

    return score


def _find_requested_fields(prompt: str, fields: list[JsonDict], max_hits: int = 80) -> list[JsonDict]:
    hits: list[JsonDict] = []
    seen: set[tuple[str, str]] = set()

    for row in fields:
        field_id = str(row.get("ID", "")).strip()
        field_name = str(row.get("FieldName", "")).strip()
        field_viet = str(row.get("NameVietnam", "")).strip()
        if not field_id:
            continue

        matched = False
        if field_id and field_id.lower() in prompt.lower():
            matched = True
        elif field_name and field_name in prompt:
            matched = True
        elif field_viet and field_viet.lower() in prompt.lower():
            matched = True

        if matched:
            key = (str(row.get("TableID", "")).strip(), field_id)
            if key not in seen:
                seen.add(key)
                hits.append(row)
                if len(hits) >= max_hits:
                    break

    return hits


def _expand_related_tables(base_table_ids: set[str], index_keys: list[JsonDict], fields: list[JsonDict]) -> set[str]:
    if not base_table_ids:
        return set()

    edges = infer_relationships(fields, index_keys)
    related = set(base_table_ids)
    for edge in edges:
        confidence = str(edge.get("confidence", "")).strip().lower()
        if confidence not in {"high", "medium"}:
            continue
        left = str(edge.get("from_table", "")).strip().upper()
        right = str(edge.get("to_table", "")).strip().upper()
        if left in base_table_ids or right in base_table_ids:
            related.add(left)
            related.add(right)

    return related


def build_context_slice(prompt: str, bundle: JsonDict, top_k: int = 8, max_columns_per_table: int = 30) -> JsonDict:
    tables = bundle.get("tables", [])
    fields = bundle.get("fields", [])
    index_keys = bundle.get("index_keys", [])
    modules = {str(m.get("ModuleID", "")).strip().upper(): m for m in bundle.get("modules", [])}

    ranked_tables = sorted(
        tables,
        key=lambda table: (
            -_table_score(prompt, table, modules),
            str(table.get("TableID", "")).strip(),
        ),
    )
    selected_tables = ranked_tables[: max(1, top_k)]
    selected_ids = {str(t.get("TableID", "")).strip().upper() for t in selected_tables}

    field_hits = _find_requested_fields(prompt, fields)
    for hit in field_hits:
        tid = str(hit.get("TableID", "")).strip().upper()
        if tid:
            selected_ids.add(tid)

    selected_ids = _expand_related_tables(selected_ids, index_keys, fields)

    selected_tables = [t for t in ranked_tables if str(t.get("TableID", "")).strip().upper() in selected_ids]
    if len(selected_tables) > top_k:
        selected_tables = selected_tables[:top_k]
        selected_ids = {str(t.get("TableID", "")).strip().upper() for t in selected_tables}

    columns_by_table: dict[str, list[JsonDict]] = defaultdict(list)
    for row in fields:
        tid = str(row.get("TableID", "")).strip().upper()
        if tid in selected_ids:
            columns_by_table[tid].append(
                {
                    "ID": str(row.get("ID", "")).strip(),
                    "FieldName": str(row.get("FieldName", "")).strip(),
                    "NameVietnam": str(row.get("NameVietnam", "")).strip(),
                    "Type": row.get("Type", ""),
                    "Length": row.get("Length", ""),
                }
            )

    compact_columns = {
        table_id: cols[:max_columns_per_table]
        for table_id, cols in columns_by_table.items()
    }

    relationships = [
        edge
        for edge in infer_relationships(fields, index_keys)
        if str(edge.get("from_table", "")).strip().upper() in selected_ids
        and str(edge.get("to_table", "")).strip().upper() in selected_ids
        and str(edge.get("confidence", "")).strip().lower() in {"high", "medium"}
    ]

    requested_columns = [
        f"{str(r.get('TableID', '')).strip()}.{str(r.get('ID', '')).strip()}"
        for r in field_hits
    ]

    return {
        "tables": [
            {
                "TableID": str(t.get("TableID", "")).strip(),
                "TableName": str(t.get("TableName", "")).strip(),
                "ModuleID": str(t.get("ModuleID", "")).strip(),
                "DB": str(t.get("DB", "")).strip(),
            }
            for t in selected_tables
        ],
        "columns": compact_columns,
        "relationships": relationships,
        "requested_columns": requested_columns,
    }
