"""Bounded MSSQL reads through the existing Grafana datasource connection."""
from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import build_scope

MAX_SCHEMA_COLUMNS = 2000
# Only scalar/aggregate built-ins; no user-defined, external-access or admin functions.
READ_FUNCTIONS = frozenset({
    "ABS", "AVG", "CEIL", "FLOOR", "ROUND", "SUM", "MIN", "MAX", "COUNT",
    "CAST", "TRY_CAST", "CONVERT", "COALESCE", "NULLIF", "IF", "CASE",
    "LOWER", "UPPER", "TRIM", "LTRIM", "RTRIM", "LENGTH", "SUBSTRING", "CONCAT",
    "REPLACE", "LEFT", "RIGHT", "YEAR", "MONTH", "DAY", "DATE_FROM_PARTS",
    "DATE_ADD", "DATE_DIFF", "DATE_TRUNC", "EXTRACT", "TS_OR_DS_TO_DATE",
    "TIME_TO_STR", "STR_TO_TIME", "CURRENT_TIMESTAMP", "CURRENT_DATE",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE",
    "STDDEV", "STDDEV_SAMP", "STDDEV_POP", "VARIANCE", "VARIANCE_POP",
})


def query_model(uid: str, sql: str) -> dict[str, Any]:
    return {"refId": "A", "datasource": {"type": "mssql", "uid": uid},
            "rawSql": sql, "format": "table", "rawQuery": True}


def schema_sql(schemas: list[str]) -> str:
    if not schemas or any(not isinstance(name, str) or not name or len(name) > 128 for name in schemas):
        raise ValueError("MSSQL dataset requires authorized schema names")
    names = ", ".join("'" + name.replace("'", "''") + "'" for name in schemas)
    return (f"SELECT TOP {MAX_SCHEMA_COLUMNS + 1} TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, "
            "DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA IN ({names}) ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION")


def schema_tables(frame: dict[str, Any]) -> list[dict[str, Any]]:
    names = [field["name"] for field in frame["schema"]["fields"]]
    required = {"TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "IS_NULLABLE", "ORDINAL_POSITION"}
    if not required <= set(names):
        raise ValueError("Grafana returned an incomplete MSSQL schema")
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for values in zip(*frame["data"]["values"], strict=True):
        row = dict(zip(names, values, strict=True))
        key = (row["TABLE_SCHEMA"], row["TABLE_NAME"])
        table = tables.setdefault(key, {"schema": key[0], "name": key[1], "fields": []})
        table["fields"].append({"name": row["COLUMN_NAME"], "type": row["DATA_TYPE"],
                                "nullable": row["IS_NULLABLE"] == "YES"})
    if not tables:
        raise ValueError("No readable tables or views in the authorized MSSQL schemas")
    return list(tables.values())


def bounded_select(sql: Any, tables: list[dict[str, Any]], maximum_rows: int) -> str:
    """Normalize a single read, refusing effects and references outside live metadata.

    Fetch one extra row to detect overflow rather than silently accepting truncation.
    Grafana's configured read-only SQL login remains the database permission boundary.
    """
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20_000:
        raise ValueError("MSSQL sql must be a nonempty SELECT of at most 20000 characters")
    try:
        statements = sqlglot.parse(sql, read="tsql", error_level=ErrorLevel.RAISE)
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise ValueError("Only one read-only SELECT is allowed")
        tree = statements[0]
        nodes = list(tree.walk())
        if len(nodes) > 2000:
            raise ValueError("SELECT exceeds the query complexity bound")
        forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Parameter, exp.Placeholder,
                     exp.Dot, exp.Lock, exp.QueryOption, exp.TableSample)
        for node in nodes:
            if isinstance(node, forbidden):
                raise ValueError("SQL writes, variables, hints and external references are not allowed")
            if isinstance(node, exp.Select) and any(node.args.get(key) for key in ("hint", "locks", "options")):
                raise ValueError("SELECT hints and options are not allowed")
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise ValueError("Recursive queries are not allowed")
            if isinstance(node, exp.Func) and node.sql_name() not in READ_FUNCTIONS:
                raise ValueError(f"SQL function is not an allowed read built-in: {node.sql_name()}")
            if isinstance(node, exp.Column) and (node.db or node.catalog):
                raise ValueError("Use column names or table aliases, not database-qualified columns")
            if isinstance(node, exp.Limit) and node.args.get("limit_options"):
                raise ValueError("TOP PERCENT and WITH TIES are not bounded reads")
        allowed = {(item["schema"].casefold(), item["name"].casefold()) for item in tables}
        root_scope = build_scope(tree)
        if root_scope is None:
            raise ValueError("SELECT scope is invalid")
        physical_tables = 0
        for scope in root_scope.traverse():
            for source in scope.sources.values():
                if not isinstance(source, exp.Table):
                    continue  # Derived SELECT / CTE; its own scope is inspected too.
                physical_tables += 1
                if (not isinstance(source.this, exp.Identifier) or source.catalog
                        or source.args.get("hints") or source.args.get("pivots")
                        or (source.db.casefold(), source.name.casefold()) not in allowed):
                    raise ValueError("SELECT must use schema-qualified tables/views from inspect_dataset")
        if not physical_tables:
            raise ValueError("SELECT must read an authorized table or view")
        # Recursive CTEs in T-SQL do not need the RECURSIVE keyword. A self-reference
        # remains a physical/unresolved table in sqlglot scope and is rejected above.
        limit = tree.args.get("limit")
        if limit is not None:
            value = limit.expression
            if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 1:
                raise ValueError("TOP must be a positive integer")
            if int(value.this) > maximum_rows:
                tree = tree.limit(maximum_rows + 1)
        else:
            tree = tree.limit(maximum_rows + 1)
        return tree.sql(dialect="tsql", unsupported_level=ErrorLevel.RAISE)
    except SqlglotError as exc:
        raise ValueError("SQL must be valid supported read-only T-SQL") from exc
