from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AggregateExpectation:
    operation: str
    column: str
    expected_value: float
    tolerance: float = 0.0


@dataclass(frozen=True)
class ExecutionExpectation:
    required_columns: list[str] = field(default_factory=list)
    min_rows: int = 0
    max_rows: int | None = None
    aggregates: list[AggregateExpectation] = field(default_factory=list)


def _to_float(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _compute_aggregate(rows: list[dict[str, Any]], operation: str, column: str) -> float:
    values = [_to_float(row.get(column)) for row in rows]
    if not values:
        return 0.0
    op = operation.lower()
    if op == "sum":
        return float(sum(values))
    if op == "min":
        return float(min(values))
    if op == "max":
        return float(max(values))
    if op == "avg":
        return float(sum(values) / len(values))
    raise RuntimeError("UNKNOWN_AGGREGATE_OPERATION")


def validate_execution_result(rows: list[dict[str, Any]], expectation: ExecutionExpectation) -> tuple[bool, str]:
    row_count = len(rows)
    if row_count < expectation.min_rows:
        return False, "ROWCOUNT_TOO_LOW"
    if expectation.max_rows is not None and row_count > expectation.max_rows:
        return False, "ROWCOUNT_TOO_HIGH"

    if expectation.required_columns:
        if not rows:
            return False, "MISSING_REQUIRED_COLUMN"
        actual_columns = set(rows[0].keys())
        if any(column not in actual_columns for column in expectation.required_columns):
            return False, "MISSING_REQUIRED_COLUMN"

    for aggregate in expectation.aggregates:
        if any(aggregate.column not in row for row in rows):
            return False, "MISSING_AGGREGATE_COLUMN"
        actual_value = _compute_aggregate(rows, aggregate.operation, aggregate.column)
        if abs(actual_value - aggregate.expected_value) > aggregate.tolerance:
            return False, "AGGREGATE_MISMATCH"

    return True, "OK"


def execute_and_validate(sql: str, db_client, expectation: ExecutionExpectation) -> tuple[bool, str, list[dict[str, Any]]]:
    rows = db_client.execute_readonly(sql)
    ok, code = validate_execution_result(rows, expectation)
    return ok, code, rows
