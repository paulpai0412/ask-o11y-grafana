import re
from typing import Any


JsonDict = dict[str, Any]

FROM_JOIN_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:\[[^\]]+\]\.){0,2}\[([^\]]+)\](?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)
FROM_JOIN_UNBRACKETED_PATTERN = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
COLUMN_PATTERN = re.compile(r"\[([A-Za-z]{2}\d{3})\]")
COLUMN_UNBRACKETED_PATTERN = re.compile(r"\b([A-Za-z]{2}\d{3})\b")
ALIASED_COLUMN_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.\[([A-Za-z]{2}\d{3})\]", re.IGNORECASE)
BRACKETED_QUALIFIED_COLUMN_PATTERN = re.compile(
    r"\[([A-Za-z_][A-Za-z0-9_]*)\]\.\[([A-Za-z]{2}\d{3})\]",
    re.IGNORECASE,
)


def _table_ids(bundle: JsonDict) -> set[str]:
    return {str(row.get("TableID", "")).strip().upper() for row in bundle.get("tables", []) if str(row.get("TableID", "")).strip()}


def _column_ids(bundle: JsonDict) -> set[str]:
    return {str(row.get("ID", "")).strip().upper() for row in bundle.get("fields", []) if str(row.get("ID", "")).strip()}


def _columns_by_table(bundle: JsonDict) -> dict[str, set[str]]:
    by_table: dict[str, set[str]] = {}
    for row in bundle.get("fields", []):
        table_id = str(row.get("TableID", "")).strip().upper()
        column_id = str(row.get("ID", "")).strip().upper()
        if not table_id or not column_id:
            continue
        by_table.setdefault(table_id, set()).add(column_id)
    return by_table


def validate_metadata_references(sql: str, bundle: JsonDict) -> tuple[bool, str]:
    text = str(sql or "")
    known_tables = _table_ids(bundle)
    known_columns = _column_ids(bundle)
    by_table = _columns_by_table(bundle)

    table_matches = list(FROM_JOIN_PATTERN.finditer(text))
    referenced_tables = {m.group(1).strip().upper() for m in table_matches}
    alias_to_table = {
        m.group(2).strip().upper(): m.group(1).strip().upper()
        for m in table_matches
        if m.group(2)
    }

    for table_id in referenced_tables:
        alias_to_table.setdefault(table_id, table_id)
    unbracketed_tables = {m.group(1).strip().upper() for m in FROM_JOIN_UNBRACKETED_PATTERN.finditer(text)}

    if unbracketed_tables:
        return False, "TABLE_REFERENCE_FORMAT_INVALID"

    if not referenced_tables:
        return False, "NO_TABLE_REFERENCE"

    for table_id in referenced_tables:
        if table_id not in known_tables:
            return False, "UNKNOWN_TABLE"

    for match in ALIASED_COLUMN_PATTERN.finditer(text):
        alias = match.group(1).strip().upper()
        column = match.group(2).strip().upper()
        if alias not in alias_to_table:
            return False, "UNKNOWN_TABLE_ALIAS"
        table_id = alias_to_table[alias]
        if column not in by_table.get(table_id, set()):
            return False, "UNKNOWN_COLUMN_FOR_TABLE"

    for match in BRACKETED_QUALIFIED_COLUMN_PATTERN.finditer(text):
        alias = match.group(1).strip().upper()
        column = match.group(2).strip().upper()
        if alias not in alias_to_table:
            return False, "UNKNOWN_TABLE_ALIAS"
        table_id = alias_to_table[alias]
        if column not in by_table.get(table_id, set()):
            return False, "UNKNOWN_COLUMN_FOR_TABLE"

    referenced_columns = {m.group(1).strip().upper() for m in COLUMN_PATTERN.finditer(text)}
    referenced_columns.update({m.group(1).strip().upper() for m in COLUMN_UNBRACKETED_PATTERN.finditer(text)})
    for column_id in referenced_columns:
        if column_id not in known_columns:
            return False, "UNKNOWN_COLUMN"

        if referenced_tables:
            if not any(column_id in by_table.get(table_id, set()) for table_id in referenced_tables):
                return False, "UNKNOWN_COLUMN_FOR_TABLE"

    return True, "OK"
