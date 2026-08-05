import os

from skill_scripts.intent_parser import parse_intent
from skill_scripts.sql2000_guard import validate_sql
from typing import Any


JsonDict = dict[str, Any]


def _safe_identifier(name: str) -> str:
    return f"[{str(name).strip()}]"


def _format_table_3part(db_prefix: str, table_id: str) -> str:
    db = str(db_prefix or "").strip().rstrip(".")
    parts = [p for p in db.split(".") if p]
    if len(parts) >= 2:
        db_name, schema = parts[0], parts[1]
    elif len(parts) == 1:
        db_name, schema = parts[0], "dbo"
    else:
        db_name, schema = "DSCSYS", "dbo"
    return f"{_safe_identifier(db_name)}.{_safe_identifier(schema)}.{_safe_identifier(table_id)}"


def _default_db_override() -> str:
    raw = str(os.getenv("WFERP_DEFAULT_DB", "")).strip()
    if not raw:
        return ""

    value = raw.rstrip(".")
    if "." not in value:
        return f"{value}.dbo."
    return f"{value}."


def _table_match_score(prompt: str, table_row: JsonDict, module_map: dict[str, JsonDict]) -> int:
    p = prompt.lower()
    score = 0
    table_id = str(table_row.get("TableID", "")).strip()
    if table_id and table_id.lower() in p:
        score += 100

    for key in ("TableName", "TableNameViet"):
        value = str(table_row.get(key, "")).strip().lower()
        if value and value in p:
            score += 80

    module_id = str(table_row.get("ModuleID", "")).strip()
    if module_id and module_id.lower() in p:
        score += 30

    module_row = module_map.get(module_id, {})
    for key in ("ModuleName", "ModuleNameViet"):
        value = str(module_row.get(key, "")).strip().lower()
        if value and value in p:
            score += 30

    if "採購" in prompt and table_id.startswith("PUR"):
        score += 50

    # domain keyword boost for budget queries
    if "預算" in prompt and table_id in {"ACTMJ", "ACTMK", "ACTMI"}:
        score += 70
    if "明細" in prompt and table_id == "ACTMK":
        score += 90
    if "工程" in prompt and table_id == "ACTMK":
        score += 30

    return score


def _choose_table(prompt: str, bundle: JsonDict) -> JsonDict:
    tables = bundle.get("tables", [])
    modules = {str(m.get("ModuleID", "")).strip(): m for m in bundle.get("modules", [])}

    if not tables:
        return {"DB": "DSCSYS.dbo.", "TableID": "ADMMC"}

    scored = sorted(
        ((table, _table_match_score(prompt, table, modules)) for table in tables),
        key=lambda item: (-item[1], str(item[0].get("TableID", "")).strip()),
    )

    best_table, best_score = scored[0]
    if best_score <= 0:
        for row in tables:
            if str(row.get("TableID", "")).strip() == "ADMMC":
                return row
        return {"DB": "DSCSYS.dbo.", "TableID": "ADMMC"}
    return best_table


def _year_filter_clause(table_id: str, year: str | None) -> str:
    if not year:
        return ""
    if table_id == "ACTMJ":
        return f" WHERE {_safe_identifier('MJ002')} = '{year}'"
    if table_id == "ACTMK":
        return f" WHERE {_safe_identifier('MK002')} = '{year}'"
    return ""


def generate_select_sql(prompt: str, bundle: JsonDict) -> str:
    intent = parse_intent(prompt)
    if intent["non_select_intent"]:
        raise ValueError("NON_SELECT_INTENT")

    table = _choose_table(intent["raw"], bundle)
    table_id = str(table.get("TableID", "")).strip() or "ADMMC"
    db = _default_db_override() or str(table.get("DB", "")).strip() or "DSCSYS.dbo."
    top = intent["top"]
    year = intent.get("year")

    select_prefix = f"SELECT TOP {top}" if top is not None else "SELECT"
    sql = f"{select_prefix} * FROM {_format_table_3part(db, table_id)}{_year_filter_clause(table_id, year)}"
    ok, code = validate_sql(sql)
    if not ok:
        raise ValueError(code)
    return sql
