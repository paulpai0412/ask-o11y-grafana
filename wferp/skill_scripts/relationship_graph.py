from collections import defaultdict
from typing import Any


JsonDict = dict[str, Any]


def _split_index_columns(index_column_name: str):
    return [c.strip() for c in str(index_column_name or "").split("+") if c.strip()]


def build_primary_key_map(index_keys: list[JsonDict]) -> dict[str, list[str]]:
    primary_key_map = {}
    for row in index_keys:
        if str(row.get("isPrimaryKey", "")).strip() != "1":
            continue
        table = str(row.get("TableName", "")).strip()
        columns = _split_index_columns(row.get("IndexColumnName", ""))
        if table and columns:
            primary_key_map[table] = columns
    return primary_key_map


def _group_tables_by_prefix(pk_map: dict[str, list[str]]) -> dict[str, list[str]]:
    grouped = defaultdict(list)
    for table in pk_map:
        grouped[table[:3]].append(table)
    for tables in grouped.values():
        tables.sort()
    return dict(grouped)


def _infer_edge(parent: str, child: str, parent_pk: list[str], child_pk: list[str]) -> JsonDict:
    width = min(len(parent_pk), len(child_pk))
    parent_join_cols = parent_pk[:width]
    child_join_cols = child_pk[:width]
    cardinality = "1:1" if len(parent_pk) == len(child_pk) and width == len(child_pk) else "1:N"
    return {
        "from_table": parent,
        "to_table": child,
        "from_columns": parent_join_cols,
        "to_columns": child_join_cols,
        "confidence": "medium",
        "cardinality": cardinality,
        "reason": "heuristic inference from same 3-char prefix and aligned key position pattern",
    }


def infer_relationships(fields: list[JsonDict], index_keys: list[JsonDict]) -> list[JsonDict]:
    del fields
    pk_map = build_primary_key_map(index_keys)
    by_prefix = _group_tables_by_prefix(pk_map)

    edges = []
    for _, tables in by_prefix.items():
        if len(tables) < 2:
            continue
        ordered = sorted(tables)
        parent = ordered[0]
        for child in ordered[1:]:
            edge = _infer_edge(parent, child, pk_map[parent], pk_map[child])
            edges.append(edge)
    return edges
