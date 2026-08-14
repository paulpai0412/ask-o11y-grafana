"""Generic SQL AST validation against approved executable ontology relations."""
from __future__ import annotations

from typing import Any

from sqlglot import exp, parse_one  # type: ignore[reportMissingModuleSource]
from sqlglot.errors import ParseError  # type: ignore[reportMissingModuleSource]
from sqlglot.optimizer.scope import build_scope  # type: ignore[reportMissingModuleSource]


def validate_ontology_joins(sql: str, snapshot: dict[str, Any], dialect: str) -> tuple[bool, str]:
    try:
        tree = parse_one(sql, read=dialect)
    except ParseError:
        return False, "SQL_AST_INVALID"
    root_scope = build_scope(tree)
    if not isinstance(tree, exp.Select) or root_scope is None:
        return False, "SQL_AST_INVALID"
    relations = [relation for dataset in snapshot["registry"]["datasets"] for relation in dataset.get("relations", [])]
    approved = [relation for relation in relations if relation.get("status") == "approved" and bool(relation.get("executable"))]
    for scope in root_scope.traverse():
        aliases = {alias.upper(): source.name.upper() for alias, source in scope.sources.items() if isinstance(source, exp.Table)}
        tables = list(scope.tables)
        if not tables:
            continue
        joined = {tables[0].name.upper()}
        for join in scope.expression.find_all(exp.Join, bfs=False):
            target = join.this
            if not isinstance(target, exp.Table) or (target.alias or target.name).upper() not in aliases:
                return False, "JOIN_RELATION_NOT_APPROVED"
            target_table = target.name.upper()
            on = join.args.get("on")
            pairs: set[tuple[str, str, str, str]] = set()
            if on is not None:
                for equality in on.find_all(exp.EQ):
                    left, right = equality.left, equality.right
                    if isinstance(left, exp.Column) and isinstance(right, exp.Column) and left.table and right.table:
                        pairs.add((aliases.get(left.table.upper(), ""), left.name.upper(), aliases.get(right.table.upper(), ""), right.name.upper()))
            candidates = [relation for relation in approved if target_table in {str(relation["from_dataset"]).upper(), str(relation["to_dataset"]).upper()} and joined.intersection({str(relation["from_dataset"]).upper(), str(relation["to_dataset"]).upper()})]
            if not candidates:
                return False, "JOIN_RELATION_NOT_APPROVED"
            matched = False
            for relation in candidates:
                expected = {(str(relation["from_dataset"]).upper(), str(left).upper(), str(relation["to_dataset"]).upper(), str(right).upper()) for left, right in zip(relation["from_fields"], relation["to_fields"], strict=True)}
                reverse = {(right_table, right, left_table, left) for left_table, left, right_table, right in expected}
                if expected <= pairs or reverse <= pairs:
                    matched = True
                    break
            if not matched:
                return False, "JOIN_PREDICATE_MISMATCH"
            joined.add(target_table)
    return True, "OK"
