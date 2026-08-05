import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal

from pytest import MonkeyPatch

from skill_scripts.execution_validator import AggregateExpectation, ExecutionExpectation
from skill_scripts.governed_query import (
    approve_query_result,
    reject_query_result,
    require_query_result_approved_for_chart_spec,
    run_governed_query,
)
from skill_scripts.sql_router import RoutingOptions


Row = dict[str, object]


def _bundle() -> dict[str, list[dict[str, str]]]:
    return {
        "modules": [{"ModuleID": "ACT", "ModuleName": "會計總帳管理系統"}],
        "tables": [
            {"DB": "VPIC1.dbo.", "TableID": "ACTMK", "TableName": "科目/部門預算單身檔", "TableNameViet": "", "ModuleID": "ACT"},
        ],
        "fields": [
            {"TableID": "ACTMK", "ID": "MK002", "FieldName": "會計年度", "NameVietnam": "Năm kế toán"},
            {"TableID": "ACTMK", "ID": "MK006", "FieldName": "期預算", "NameVietnam": "Dự toán kỳ"},
        ],
        "index_keys": [
            {"TableName": "ACTMK", "IndexColumnName": "MK001+MK002+MK003+MK004+MK005", "isPrimaryKey": "1"},
        ],
    }


@dataclass(frozen=True)
class _FakeDbConfig:
    env: str


class _FakeDbClient:
    config: _FakeDbConfig
    _rows: list[Row]

    def __init__(self, rows: list[Row] | None = None, env: str = "test") -> None:
        self._rows = rows or [{"MK002": "2026", "MK006": 1000.0}]
        self.config = _FakeDbConfig(env=env)
        self.executed_sql: list[str] = []

    def execute_readonly(self, sql: str) -> list[Row]:
        assert sql.startswith("SELECT")
        self.executed_sql.append(sql)
        return self._rows

    def health_check(self) -> tuple[bool, str]:
        return True, "OK"


class _FakeCustomDbClientNoConfig:
    def execute_readonly(self, sql: str) -> list[Row]:
        _ = sql
        return [{"MK002": "2026", "MK006": 1000.0}]

    def health_check(self) -> tuple[bool, str]:
        return True, "OK"


def _assert_runtime_error(fn: Callable[[], object], expected_substring: str) -> None:
    try:
        _ = fn()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert expected_substring in str(exc)


def test_run_governed_query_returns_ready_for_approval(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    result = run_governed_query(
        prompt="查詢2026年的工程預算明細",
        bundle=_bundle(),
        options=RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            execution_expectation=ExecutionExpectation(
                required_columns=["MK002", "MK006"],
                min_rows=1,
                aggregates=[],
            ),
        ),
        db_client=_FakeDbClient(),
        sample_rows_limit=1,
    )
    assert result.approval_state == "ready_for_analyst_approval"
    assert result.evidence.validation_status == "OK"
    assert result.evidence.execution_status == "OK"
    assert result.evidence.returned_columns == ["MK002", "MK006"]
    assert result.evidence.row_count == 1
    assert result.evidence.sample_rows == [{"MK002": "2026", "MK006": 1000.0}]
    assert result.evidence.required_columns == ["MK002", "MK006"]
    assert result.evidence.route == "llm"
    assert result.evidence.execution_timestamp


def test_run_governed_query_includes_configured_aggregate_checks(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    result = run_governed_query(
        prompt="查詢2026年的工程預算明細",
        bundle=_bundle(),
        options=RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            execution_expectation=ExecutionExpectation(
                required_columns=["MK002", "MK006"],
                min_rows=1,
                aggregates=[AggregateExpectation(operation="sum", column="MK006", expected_value=1000.0)],
            ),
        ),
        db_client=_FakeDbClient(),
    )
    assert result.evidence.aggregate_checks == [
        {"operation": "sum", "column": "MK006", "expected_value": 1000.0, "tolerance": 0.0}
    ]


def test_run_governed_query_returns_json_serializable_sample_rows(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    result = run_governed_query(
        prompt="查詢2026年的工程預算明細",
        bundle=_bundle(),
        options=RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
        ),
        db_client=_FakeDbClient(rows=[{"MK002": "2026", "MK006": Decimal("1000.50")}]),
    )
    assert result.evidence.sample_rows == [{"MK002": "2026", "MK006": 1000.5}]
    json.dumps(asdict(result), ensure_ascii=False)


def test_run_governed_query_rejects_unsafe_sql_before_execution(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "DELETE FROM [VPIC1].[dbo].[ACTMK]",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    db_client = _FakeDbClient()
    _assert_runtime_error(
        lambda: run_governed_query(
            prompt="查詢2026年的工程預算明細",
            bundle=_bundle(),
            options=RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
                llm_repair_attempts=0,
            ),
            db_client=db_client,
        ),
        "LLM_REPAIR_FAILED:NON_SELECT_INTENT",
    )
    assert db_client.executed_sql == []


def test_run_governed_query_requires_db_client() -> None:
    _assert_runtime_error(
        lambda: run_governed_query(
            prompt="查詢2026年的工程預算明細",
            bundle=_bundle(),
            options=RoutingOptions(mode="rule"),
            db_client=None,
        ),
        "DB_CLIENT_REQUIRED",
    )


def test_run_governed_query_blocks_client_without_env_config(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    _assert_runtime_error(
        lambda: run_governed_query(
            prompt="查詢2026年的工程預算明細",
            bundle=_bundle(),
            options=RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
            ),
            db_client=_FakeCustomDbClientNoConfig(),
        ),
        "DB_ENV_NOT_TEST",
    )


def test_approve_query_result_unlocks_chart_spec(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    result = run_governed_query(
        prompt="查詢2026年的工程預算明細",
        bundle=_bundle(),
        options=RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
        ),
        db_client=_FakeDbClient(),
    )
    _assert_runtime_error(lambda: require_query_result_approved_for_chart_spec(result), "QUERY_RESULT_NOT_APPROVED")
    approved = approve_query_result(result)
    require_query_result_approved_for_chart_spec(approved)
    assert approved.approval_state == "approved_for_chart_spec"


def test_reject_query_result_requires_reason(monkeypatch: MonkeyPatch) -> None:
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    result = run_governed_query(
        prompt="查詢2026年的工程預算明細",
        bundle=_bundle(),
        options=RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
        ),
        db_client=_FakeDbClient(),
    )
    _assert_runtime_error(lambda: reject_query_result(result, ""), "REJECTION_REASON_REQUIRED")
    rejected = reject_query_result(result, "資料不符預期")
    assert rejected.approval_state == "rejected"
    assert rejected.rejection_reason == "資料不符預期"
