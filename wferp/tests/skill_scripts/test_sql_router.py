import json

from skill_scripts.execution_validator import ExecutionExpectation
from skill_scripts.sql_router import RoutingOptions, route_generate_sql


def _bundle():
    return {
        "modules": [{"ModuleID": "ACT", "ModuleName": "會計總帳管理系統"}],
        "tables": [
            {"DB": "VPIC1.dbo.", "TableID": "ACTMK", "TableName": "科目/部門預算單身檔", "TableNameViet": "", "ModuleID": "ACT"},
            {"DB": "DSCSYS.dbo.", "TableID": "ADMMC", "TableName": "使用者資料檔", "TableNameViet": "", "ModuleID": "ADM"},
        ],
        "fields": [
            {"TableID": "ACTMK", "ID": "MK002", "FieldName": "會計年度", "NameVietnam": "Năm kế toán"},
            {"TableID": "ACTMK", "ID": "MK006", "FieldName": "期預算", "NameVietnam": "Dự toán kỳ"},
        ],
        "index_keys": [
            {"TableName": "ACTMK", "IndexColumnName": "MK001+MK002+MK003+MK004+MK005", "isPrimaryKey": "1"},
        ],
    }


def _assert_runtime_error(fn, expected_substring: str):
    try:
        fn()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert expected_substring in str(exc)


def test_route_generate_sql_rule_mode_returns_deterministic_sql():
    sql, meta = route_generate_sql(
        "查詢2026年的工程預算明細",
        _bundle(),
        RoutingOptions(mode="rule"),
    )
    assert sql.startswith("SELECT")
    assert meta["route"] == "rule"


def test_route_generate_sql_raises_when_provider_not_configured():
    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(mode="llm-first", llm_provider="none"),
        ),
        "LLM_PROVIDER_NOT_CONFIGURED",
    )


def test_route_generate_sql_shadow_mode_returns_rule_and_candidate(monkeypatch):
    mock_payload = {
        "sql": "SELECT * FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.91,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    sql, meta = route_generate_sql(
        "查詢2026年的工程預算明細",
        _bundle(),
        RoutingOptions(mode="shadow", llm_provider="mock", llm_model="mock", min_confidence=0.5),
    )
    assert sql.startswith("SELECT")
    assert meta["route"] == "shadow_rule"
    assert "candidate_sql" in meta


def test_route_generate_sql_raises_after_llm_repair_attempts_exhausted(monkeypatch):
    mock_payload = {
        "sql": "SELECT MK002 FROM ACTMK",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.91,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(mode="llm-first", llm_provider="mock", llm_model="mock", min_confidence=0.5, llm_repair_attempts=1),
        ),
        "LLM_REPAIR_FAILED:TABLE_REFERENCE_FORMAT_INVALID",
    )


def test_route_generate_sql_llm_repair_succeeds_without_rule_fallback(monkeypatch):
    responses = [
        json.dumps(
            {
                "sql": "SELECT MK002 FROM ACTMK",
                "used_tables": ["ACTMK"],
                "assumptions": [],
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
                "used_tables": ["ACTMK"],
                "assumptions": ["fixed brackets"],
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
    ]

    def _fake_call_llm(provider: str, model: str, prompt_text: str, timeout_sec: float = 30.0) -> str:
        assert provider == "mock"
        return responses.pop(0)

    monkeypatch.setattr("skill_scripts.sql_router.call_llm", _fake_call_llm)

    sql, meta = route_generate_sql(
        "查詢2026年的工程預算明細",
        _bundle(),
        RoutingOptions(mode="llm-first", llm_provider="mock", llm_model="mock", min_confidence=0.5, llm_repair_attempts=2),
    )
    assert sql.startswith("SELECT [MK002],[MK006]")
    assert meta["route"] == "llm"


class _FakeDbClient:
    def __init__(self, env: str = "test"):
        self.config = type("Cfg", (), {"env": env})()

    def execute_readonly(self, sql: str):
        return [{"MK002": "2026", "MK006": 1000.0}]

    def health_check(self):
        return True, "OK"


def test_route_generate_sql_llm_path_with_execution_validation(monkeypatch):
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    sql, meta = route_generate_sql(
        "查詢2026年的工程預算明細",
        _bundle(),
        RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            validate_execution=True,
            execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
        ),
        db_client=_FakeDbClient(),
    )
    assert sql.startswith("SELECT")
    assert meta["route"] == "llm"


class _FakeProdDbClient(_FakeDbClient):
    def __init__(self):
        super().__init__(env="prod")


def test_route_generate_sql_blocks_execution_validation_on_non_test_env(monkeypatch):
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                validate_execution=True,
                execution_expectation=ExecutionExpectation(required_columns=["MK002"], min_rows=1),
            ),
            db_client=_FakeProdDbClient(),
        ),
        "DB_ENV_NOT_TEST",
    )


class _FakeBrokenDbClient(_FakeDbClient):
    def execute_readonly(self, sql: str):
        raise RuntimeError("connection failed")


class _FakeNonRuntimeBrokenDbClient(_FakeDbClient):
    def execute_readonly(self, sql: str):
        raise ValueError("connection failed")


class _FakeCustomDbClientNoConfig:
    def execute_readonly(self, sql: str):
        return [{"MK002": "2026", "MK006": 1000.0}]

    def health_check(self):
        return True, "OK"


class _FakeExecMismatchThenPassDbClient(_FakeDbClient):
    def __init__(self, env: str = "test"):
        super().__init__(env=env)
        self.calls = 0

    def execute_readonly(self, sql: str):
        self.calls += 1
        if self.calls == 1:
            return [{"MK002": "2026"}]
        return [{"MK002": "2026", "MK006": 1000.0}]


class _FakeExecMismatchAlwaysDbClient(_FakeDbClient):
    def execute_readonly(self, sql: str):
        return [{"MK002": "2026"}]


def test_route_generate_sql_falls_back_when_execution_validation_errors(monkeypatch):
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                validate_execution=True,
                execution_expectation=ExecutionExpectation(required_columns=["MK002"], min_rows=1),
                allow_non_test_execution=True,
                llm_repair_attempts=1,
            ),
            db_client=_FakeBrokenDbClient(),
        ),
        "LLM_REPAIR_FAILED:EXECUTION_VALIDATION_ERROR",
    )


def test_route_generate_sql_falls_back_when_execution_validation_raises_non_runtime_error(monkeypatch):
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                validate_execution=True,
                execution_expectation=ExecutionExpectation(required_columns=["MK002"], min_rows=1),
                allow_non_test_execution=True,
                llm_repair_attempts=1,
            ),
            db_client=_FakeNonRuntimeBrokenDbClient(),
        ),
        "LLM_REPAIR_FAILED:EXECUTION_VALIDATION_ERROR",
    )


def test_route_generate_sql_blocks_execution_validation_for_client_without_env_config(monkeypatch):
    mock_payload = {
        "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))
    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                validate_execution=True,
                execution_expectation=ExecutionExpectation(required_columns=["MK002"], min_rows=1),
            ),
            db_client=_FakeCustomDbClientNoConfig(),
        ),
        "DB_ENV_NOT_TEST",
    )


def test_route_generate_sql_repairs_after_execution_validation_mismatch(monkeypatch):
    responses = [
        json.dumps(
            {
                "sql": "SELECT [MK002] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
                "used_tables": ["ACTMK"],
                "assumptions": [],
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "sql": "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
                "used_tables": ["ACTMK"],
                "assumptions": ["fixed missing column"],
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
    ]
    prompts: list[str] = []

    def _fake_call_llm(provider: str, model: str, prompt_text: str, timeout_sec: float = 30.0) -> str:
        prompts.append(prompt_text)
        return responses.pop(0)

    monkeypatch.setattr("skill_scripts.sql_router.call_llm", _fake_call_llm)

    sql, meta = route_generate_sql(
        "查詢2026年的工程預算明細",
        _bundle(),
        RoutingOptions(
            mode="llm-first",
            llm_provider="mock",
            llm_model="mock",
            min_confidence=0.5,
            validate_execution=True,
            execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
            llm_repair_attempts=2,
        ),
        db_client=_FakeExecMismatchThenPassDbClient(),
    )

    assert sql.startswith("SELECT [MK002],[MK006]")
    assert meta["route"] == "llm"
    assert len(prompts) >= 2
    assert "Previous SQL:" in prompts[1]
    assert "Failure code: MISSING_REQUIRED_COLUMN" in prompts[1]


def test_route_generate_sql_raises_when_execution_validation_mismatch_persists(monkeypatch):
    mock_payload = {
        "sql": "SELECT [MK002] FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.95,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock_payload, ensure_ascii=False))

    _assert_runtime_error(
        lambda: route_generate_sql(
            "查詢2026年的工程預算明細",
            _bundle(),
            RoutingOptions(
                mode="llm-first",
                llm_provider="mock",
                llm_model="mock",
                min_confidence=0.5,
                validate_execution=True,
                execution_expectation=ExecutionExpectation(required_columns=["MK002", "MK006"], min_rows=1),
                llm_repair_attempts=1,
            ),
            db_client=_FakeExecMismatchAlwaysDbClient(),
        ),
        "LLM_REPAIR_FAILED:MISSING_REQUIRED_COLUMN",
    )
