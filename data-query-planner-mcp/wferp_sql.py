"""Bounded WFERP schema context and validation for LLM-authored SELECT SQL."""
from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one  # type: ignore[reportMissingModuleSource]
from sqlglot.errors import ParseError  # type: ignore[reportMissingModuleSource]
from sqlglot.optimizer.scope import build_scope  # type: ignore[reportMissingModuleSource]

ROOT = Path(__file__).resolve().parent.parent
METADATA_DIR = Path(__file__).resolve().parent / "metadata" / "wferp"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ontology_sql_validator = load_module("ontology_sql_validator", ROOT / "ontology_sql_validator.py")
ontology_graph = load_module("ontology_graph", ROOT / "ontology_graph.py")
MAX_PROMPT_BYTES = 8 * 1024
MAX_SQL_BYTES = 32 * 1024
MAX_CONTEXT_TABLES = 8
MAX_COLUMNS_PER_TABLE = 30

FORBIDDEN_NON_SELECT = (
    "insert", "update", "delete", "create", "alter", "drop", "merge", "truncate", "exec", "execute",
    "into", "openrowset", "openquery", "opendatasource", "bulk", "waitfor", "shutdown", "backup", "restore",
    "dbcc", "grant", "revoke", "deny", "use",
)
FORBIDDEN_SQL2000_REGEX = (
    r"\bwith\b\s+[\[\(a-zA-Z_][\w\]\)]*\s+as\s*\(", r"\bover\b", r"\bpartition\s+by\b",
    r"\brow_number\b", r"\brank\b", r"\bdense_rank\b", r"\boffset\b", r"\bfetch\b",
    r"\bexcept\b", r"\bintersect\b", r"\bconcat\s*\(",
)
FROM_JOIN_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\](?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)
FROM_JOIN_UNBRACKETED_PATTERN = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
ALIASED_COLUMN_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.\[([A-Za-z]{2}\d{3})\]", re.IGNORECASE)
BRACKETED_QUALIFIED_COLUMN_PATTERN = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]\.\[([A-Za-z]{2}\d{3})\]", re.IGNORECASE)
COLUMN_PATTERN = re.compile(r"\[([A-Za-z]{2}\d{3})\]")
COLUMN_UNBRACKETED_PATTERN = re.compile(r"\b([A-Za-z]{2}\d{3})\b")
YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")
TOP_PATTERN = re.compile(r"\btop\s*\(?\s*(\d+)\s*\)?", re.IGNORECASE)

_REPAIR_HINTS = {
    "TABLE_REFERENCE_FORMAT_INVALID": "Bracket every table reference, for example FROM [wferp_test].[dbo].[ACTMK] MK.",
    "NO_TABLE_REFERENCE": "Add one authorized bracketed FROM table.",
    "UNKNOWN_TABLE": "Choose only tables returned by the WFERP schema context.",
    "UNKNOWN_TABLE_ALIAS": "Declare every table alias in FROM or JOIN.",
    "UNKNOWN_COLUMN": "Choose only columns returned by the WFERP schema context.",
    "UNKNOWN_COLUMN_FOR_TABLE": "The column does not belong to that table; check the context columns.",
    "NON_SELECT_INTENT": "Return exactly one read-only SELECT statement.",
    "UNSUPPORTED_SQL2000_FEATURE": "Use legacy SQL Server syntax: no CTE, window, OFFSET/FETCH, EXCEPT, INTERSECT, or CONCAT.",
    "MULTI_STATEMENT_NOT_ALLOWED": "Return exactly one SELECT statement.",
    "YEAR_MISMATCH": "Include the explicit year requested by the user.",
    "TOP_MISMATCH": "Preserve the explicit TOP/前 N 筆 limit requested by the user.",
    "PROMPT_TABLE_MISMATCH": "Include every explicit WFERP table id named by the user.",
    "PROMPT_COLUMN_MISMATCH": "Include every explicit WFERP column id named by the user.",
    "DATABASE_SCOPE_INVALID": "Use only [wferp_test].[dbo] as the database and schema.",
    "SQL_AST_INVALID": "Return one syntactically valid legacy-compatible T-SQL SELECT.",
    "JOIN_RELATION_NOT_APPROVED": "This JOIN relation is only proposed in the ontology. Use one table or obtain steward approval for the exact keys/cardinality.",
    "JOIN_PREDICATE_MISMATCH": "Use exactly the approved ontology relation key pairs for the JOIN.",
}


def load_json(name: str) -> Any:
    try:
        return json.loads((METADATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load WFERP metadata: {name}") from exc


def load_metadata() -> dict[str, Any]:
    bundle = load_json("schema_bundle.json")
    return {
        "bundle": bundle,
        "aliases": load_json("alias_index.json"),
        "relationships": load_json("relationship_edges.json"),
        "primary_keys": load_json("primary_key_map.json"),
    }


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _search_terms(value: str) -> set[str]:
    normalized = _normalize(value)
    terms = {word for word in re.findall(r"[a-z0-9_]+", normalized) if len(word) >= 2}
    for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
        terms.update(sequence[index:index + 2] for index in range(max(0, len(sequence) - 1)))
    return terms


def _matching_aliases(prompt: str, aliases: dict[str, list[str]]) -> tuple[dict[str, float], set[str]]:
    text = _normalize(prompt)
    table_scores: dict[str, float] = defaultdict(float)
    exact_columns: set[str] = set()
    for alias, refs in aliases.items():
        normalized = _normalize(alias)
        if not normalized or normalized not in text:
            continue
        weight = 120.0 / max(1, len(refs))
        for ref in refs:
            qualified = str(ref).upper()
            if "." not in qualified:
                continue
            table_scores[qualified.split(".", 1)[0]] += weight
            exact_columns.add(qualified)
    return table_scores, exact_columns


def build_context(prompt: str, metadata: dict[str, Any], top_k: int = MAX_CONTEXT_TABLES, ontology_snapshot: dict[str, Any] | None = None, max_relation_hops: int = 2) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_BYTES} bytes")
    if not 1 <= top_k <= MAX_CONTEXT_TABLES:
        raise ValueError(f"top_k must be between 1 and {MAX_CONTEXT_TABLES}")

    bundle = metadata["bundle"]
    tables = bundle.get("tables", [])
    fields = bundle.get("fields", [])
    modules = {str(row.get("ModuleID", "")).upper(): row for row in bundle.get("modules", [])}
    alias_table_scores, alias_columns = _matching_aliases(prompt, metadata["aliases"])
    text = _normalize(prompt)
    prompt_terms = _search_terms(text)
    terms_by_table: dict[str, set[str]] = defaultdict(set)
    for field in fields:
        table_id = str(field.get("TableID", "")).upper()
        for key in ("FieldName", "NameVietnam"):
            terms_by_table[table_id].update(_search_terms(str(field.get(key) or "")))
    for table in tables:
        table_id = str(table.get("TableID", "")).upper()
        module = modules.get(str(table.get("ModuleID", "")).upper(), {})
        for value in (table.get("TableName"), table.get("TableNameViet"), module.get("ModuleName"), module.get("ModuleNameViet")):
            terms_by_table[table_id].update(_search_terms(str(value or "")))
    document_frequency = Counter(term for terms in terms_by_table.values() for term in terms)

    def score(table: dict[str, Any]) -> tuple[float, str]:
        table_id = str(table.get("TableID", "")).upper()
        module = modules.get(str(table.get("ModuleID", "")).upper(), {})
        values = [table_id, table.get("TableName"), table.get("TableNameViet"), table.get("ModuleID"), module.get("ModuleName"), module.get("ModuleNameViet")]
        points = alias_table_scores[table_id]
        for index, value in enumerate(values):
            normalized = _normalize(value)
            if normalized and normalized in text:
                points += 120 if index == 0 else 80 if index < 3 else 30
        for term in prompt_terms.intersection(terms_by_table[table_id]):
            points += 20.0 * math.log((len(tables) + 1) / (document_frequency[term] + 1))
        return points, table_id

    ranked = sorted(tables, key=lambda table: (-score(table)[0], score(table)[1]))
    lexical = [table for table in ranked if score(table)[0] > 0]
    if not lexical:
        raise ValueError("NO_SCHEMA_CONTEXT_MATCH")
    seed_ids = [str(lexical[0]["TableID"]).upper()]
    expansion = {"seeds": seed_ids, "datasets": seed_ids, "paths": [], "max_hops": 0, "include_proposed": False, "truncated": False}
    if ontology_snapshot is not None:
        expansion = ontology_graph.expand_datasets(ontology_snapshot, seed_ids, max_relation_hops, top_k, False)
    selected_ids_order = [str(value).upper() for value in expansion["datasets"]]
    for table in lexical:
        table_id = str(table["TableID"]).upper()
        if table_id not in selected_ids_order:
            selected_ids_order.append(table_id)
        if len(selected_ids_order) == top_k:
            break
    table_by_id = {str(table["TableID"]).upper(): table for table in tables}
    selected = [table_by_id[table_id] for table_id in selected_ids_order if table_id in table_by_id]
    selected_ids = set(selected_ids_order)

    columns: dict[str, list[dict[str, Any]]] = {table_id: [] for table_id in selected_ids}
    for field in fields:
        table_id = str(field.get("TableID", "")).upper()
        field_id = str(field.get("ID", "")).upper()
        if table_id not in selected_ids:
            continue
        columns[table_id].append({
            "id": field_id,
            "name": field.get("FieldName"),
            "name_vietnamese": field.get("NameVietnam"),
            "type": field.get("Type"),
            "length": field.get("Length"),
            "description": field.get("Description"),
            "requested": f"{table_id}.{field_id}" in alias_columns,
        })
    for table_id, values in columns.items():
        values.sort(key=lambda value: (not value["requested"], value["id"]))
        columns[table_id] = values[:MAX_COLUMNS_PER_TABLE]

    relationships = [path["relation"] for path in expansion["paths"]]
    proposed_relationships = [edge for edge in metadata["relationships"] if str(edge.get("from_table", "")).upper() in selected_ids and str(edge.get("to_table", "")).upper() in selected_ids]
    return {
        "sql_author": "Ask O11y LLM",
        "dialect": "Microsoft SQL Server legacy-compatible SELECT",
        "rules": [
            "Return exactly one SELECT statement.",
            "Bracket every database, schema, table, and ERP column identifier.",
            "Use only tables and columns in this context.",
            "Use TOP only when the user explicitly requests a row limit.",
            "Do not use CTE, window functions, OFFSET/FETCH, EXCEPT, INTERSECT, or CONCAT.",
        ],
        "tables": [{
            "database": "wferp_test",
            "schema": "dbo",
            "id": str(table.get("TableID", "")),
            "name": table.get("TableName"),
            "name_vietnamese": table.get("TableNameViet"),
            "module_id": table.get("ModuleID"),
            "module_name": table.get("ModuleName"),
            "primary_key": metadata["primary_keys"].get(str(table.get("TableID", "")), []),
            "columns": columns[str(table.get("TableID", "")).upper()],
        } for table in selected],
        "lexical_seed_tables": seed_ids,
        "ontology_expansion": {key: value for key, value in expansion.items() if key != "paths"},
        "relationships": relationships,
        "proposed_relationships": proposed_relationships,
        "repair_codes": _REPAIR_HINTS,
    }


def validate_sql_policy(sql: str) -> tuple[bool, str]:
    text = str(sql or "").strip()
    if not text or len(text.encode()) > MAX_SQL_BYTES:
        return False, "SQL_SIZE_INVALID"
    lowered = text.lower()
    if "--" in text or "/*" in text or "*/" in text:
        return False, "SQL_COMMENTS_NOT_ALLOWED"
    if any(re.search(rf"\b{token}\b", lowered) for token in FORBIDDEN_NON_SELECT):
        return False, "NON_SELECT_INTENT"
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in FORBIDDEN_SQL2000_REGEX):
        return False, "UNSUPPORTED_SQL2000_FEATURE"
    if len([part for part in text.split(";") if part.strip()]) > 1:
        return False, "MULTI_STATEMENT_NOT_ALLOWED"
    if not lowered.startswith("select"):
        return False, "NON_SELECT_INTENT"
    return True, "OK"


def validate_metadata_references(sql: str, bundle: dict[str, Any]) -> tuple[bool, str, list[str]]:
    known_tables = {str(row.get("TableID", "")).upper() for row in bundle.get("tables", [])}
    columns_by_table: dict[str, set[str]] = {}
    for field in bundle.get("fields", []):
        columns_by_table.setdefault(str(field.get("TableID", "")).upper(), set()).add(str(field.get("ID", "")).upper())
    try:
        tree = parse_one(sql, read="tsql")
    except ParseError:
        return False, "SQL_AST_INVALID", []
    if not isinstance(tree, exp.Select):
        return False, "NON_SELECT_INTENT", []
    table_nodes = list(tree.find_all(exp.Table))
    if FROM_JOIN_UNBRACKETED_PATTERN.search(sql):
        return False, "TABLE_REFERENCE_FORMAT_INVALID", []
    if not table_nodes:
        return False, "NO_TABLE_REFERENCE", []
    if any(table.catalog.lower() != "wferp_test" or table.db.lower() != "dbo" for table in table_nodes):
        return False, "DATABASE_SCOPE_INVALID", []
    tables = [table.name.upper() for table in table_nodes]
    if any(table not in known_tables for table in tables):
        return False, "UNKNOWN_TABLE", []
    root_scope = build_scope(tree)
    if root_scope is None:
        return False, "SQL_AST_INVALID", []
    for scope in root_scope.traverse():
        aliases = {alias.upper(): source.name.upper() for alias, source in scope.sources.items() if isinstance(source, exp.Table)}
        inherited_aliases: dict[str, str] = {}
        parent = scope.parent
        while parent is not None:
            inherited_aliases.update({alias.upper(): source.name.upper() for alias, source in parent.sources.items() if isinstance(source, exp.Table)})
            parent = parent.parent
        scoped_tables = set(aliases.values())
        known_columns = set().union(*(columns_by_table.get(table, set()) for table in scoped_tables))
        external = set(scope.external_columns)
        for column in scope.columns:
            name = column.name.upper()
            if column.table:
                alias = column.table.upper()
                resolved = aliases.get(alias) or (inherited_aliases.get(alias) if column in external else None)
                if resolved is None:
                    return False, "UNKNOWN_TABLE_ALIAS", []
                if name not in columns_by_table.get(resolved, set()):
                    return False, "UNKNOWN_COLUMN_FOR_TABLE", []
            elif name not in known_columns:
                return False, "UNKNOWN_COLUMN", []
    return True, "OK", tables


def validate_prompt_consistency(prompt: str, sql: str, bundle: dict[str, Any]) -> tuple[bool, str]:
    prompt_text = str(prompt or "")
    year = YEAR_PATTERN.search(prompt_text)
    if year and year.group(1) not in sql:
        return False, "YEAR_MISMATCH"
    requested_top = TOP_PATTERN.search(prompt_text) or re.search(r"前\s*(\d+)\s*筆", prompt_text)
    if requested_top:
        actual_top = TOP_PATTERN.search(sql)
        if not actual_top or actual_top.group(1) != requested_top.group(1):
            return False, "TOP_MISMATCH"
    known_tables = {str(row.get("TableID", "")).upper() for row in bundle.get("tables", [])}
    explicit_tables = {token.upper() for token in re.findall(r"\b[A-Za-z]{5}\b", prompt_text) if token.upper() in known_tables}
    if any(f"[{table}]" not in sql.upper() for table in explicit_tables):
        return False, "PROMPT_TABLE_MISMATCH"
    explicit_columns = {token.upper() for token in re.findall(r"\b[A-Za-z]{2}\d{3}\b", prompt_text)}
    if any(f"[{column}]" not in sql.upper() for column in explicit_columns):
        return False, "PROMPT_COLUMN_MISMATCH"
    return True, "OK"


def validate_join_ontology(sql: str, ontology_snapshot: dict[str, Any]) -> tuple[bool, str]:
    return ontology_sql_validator.validate_ontology_joins(sql, ontology_snapshot, "tsql")


def validate_llm_sql(prompt: str, sql: str, metadata: dict[str, Any], ontology_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    for validator in (
        lambda: validate_sql_policy(sql),
        lambda: validate_metadata_references(sql, metadata["bundle"]),
        lambda: validate_prompt_consistency(prompt, sql, metadata["bundle"]),
    ):
        result = validator()
        if not result[0]:
            code = result[1]
            return {"ok": False, "code": code, "repair_hint": _REPAIR_HINTS.get(code, "Revise the SQL to satisfy the declared schema and policy.")}
    if ontology_snapshot is not None:
        ontology_ok, ontology_code = validate_join_ontology(sql, ontology_snapshot)
        if not ontology_ok:
            return {"ok": False, "code": ontology_code, "repair_hint": _REPAIR_HINTS[ontology_code]}
    tables = validate_metadata_references(sql, metadata["bundle"])[2]
    return {"ok": True, "code": "OK", "tables": tables}
