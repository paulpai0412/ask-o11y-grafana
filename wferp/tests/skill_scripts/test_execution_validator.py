from skill_scripts.execution_validator import (
    AggregateExpectation,
    ExecutionExpectation,
    execute_and_validate,
    validate_execution_result,
)


class _FakeDbClient:
    def __init__(self, rows):
        self._rows = rows

    def execute_readonly(self, sql: str):
        assert sql.startswith("SELECT")
        return self._rows


def test_validate_execution_result_checks_required_columns():
    rows = [{"MK002": "2026", "MK006": 1000.0}]
    ok, code = validate_execution_result(
        rows,
        ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
    )
    assert ok is True
    assert code == "OK"


def test_validate_execution_result_detects_aggregate_mismatch():
    rows = [{"MK006": 100.0}, {"MK006": 200.0}]
    ok, code = validate_execution_result(
        rows,
        ExecutionExpectation(
            min_rows=1,
            aggregates=[AggregateExpectation(operation="sum", column="MK006", expected_value=500.0)],
        ),
    )
    assert ok is False
    assert code == "AGGREGATE_MISMATCH"


def test_validate_execution_result_detects_missing_aggregate_column():
    rows = [{"MK002": "2026"}]
    ok, code = validate_execution_result(
        rows,
        ExecutionExpectation(
            min_rows=1,
            aggregates=[AggregateExpectation(operation="sum", column="MK006", expected_value=0.0)],
        ),
    )
    assert ok is False
    assert code == "MISSING_AGGREGATE_COLUMN"


def test_execute_and_validate_uses_db_client_and_returns_rows():
    rows = [{"MK002": "2026", "MK006": 1000.0}]
    fake = _FakeDbClient(rows)
    ok, code, actual_rows = execute_and_validate(
        "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK]",
        fake,
        ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
    )
    assert ok is True
    assert code == "OK"
    assert actual_rows == rows
