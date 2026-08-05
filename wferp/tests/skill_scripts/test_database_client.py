from skill_scripts.database_client import DatabaseClient, DatabaseConfig


def test_database_config_reads_environment_values(monkeypatch):
    monkeypatch.setenv("DB_DRIVER", "mssql")
    monkeypatch.setenv("DB_CONNECTION_STRING", "Server=127.0.0.1;Database=wferp_test;")
    monkeypatch.setenv("DB_ENV", "test")
    cfg = DatabaseConfig.from_env()
    assert cfg.driver == "mssql"
    assert cfg.connection_string.startswith("Server=")
    assert cfg.env == "test"


def test_database_config_builds_mssql_string_for_sql_auth():
    cfg = DatabaseConfig(
        driver="mssql",
        connection_string="",
        auth_mode="sql_auth",
        env="test",
        host="127.0.0.1",
        port=1433,
        database="wferp_test",
        username="sa",
        password="Passw0rd!",
        domain="",
        odbc_driver="ODBC Driver 18 for SQL Server",
    )
    conn_str = cfg.resolved_connection_string()
    assert "server=127.0.0.1:1433;" in conn_str
    assert "user=sa;" in conn_str


def test_database_config_builds_domain_username_for_mssql():
    cfg = DatabaseConfig(
        driver="mssql",
        connection_string="",
        auth_mode="windows_domain",
        env="test",
        host="127.0.0.1",
        port=1433,
        database="wferp_test",
        username="alice",
        password="secret",
        domain="ACME",
        odbc_driver="ODBC Driver 18 for SQL Server",
    )
    assert "user=ACME\\alice;" in cfg.resolved_connection_string()


def test_database_client_health_check_missing_connection(monkeypatch):
    monkeypatch.delenv("DB_CONNECTION_STRING", raising=False)
    cfg = DatabaseConfig.from_env().with_overrides(connection_string="")
    client = DatabaseClient(cfg)
    ok, code = client.health_check()
    assert ok is False
    assert code == "DB_PASSWORD_MISSING"


def test_database_client_execute_readonly_normalizes_driver_errors():
    class _BadCursor:
        description = [("MK002",)]

        def execute(self, sql: str):
            raise ValueError("driver execute failed")

    class _BadConnection:
        def cursor(self):
            return _BadCursor()

        def close(self):
            return None

    class _TestClient(DatabaseClient):
        def _connect(self):
            return _BadConnection()

    cfg = DatabaseConfig(
        driver="mssql",
        connection_string="server=127.0.0.1:1433;user=sa;password=Passw0rd!;database=wferp_test",
        auth_mode="sql_auth",
        env="test",
        host="127.0.0.1",
        port=1433,
        database="wferp_test",
        username="sa",
        password="Passw0rd!",
        domain="",
        odbc_driver="ODBC Driver 18 for SQL Server",
    )

    client = _TestClient(cfg)
    try:
        client.execute_readonly("SELECT [MK002] FROM [VPIC1].[dbo].[ACTMK]")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "DB_EXECUTION_FAILED"


def test_database_client_mssql_connect_uses_parsed_connection_fields(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeConnection:
        def cursor(self):
            class _Cursor:
                description = [("MK002",), ("MK006",)]

                def execute(self, sql: str):
                    return None

                def fetchall(self):
                    return [("2026", 100000.0)]

            return _Cursor()

        def close(self):
            return None

    class _FakePymssql:
        @staticmethod
        def connect(**kwargs):
            captured.update(kwargs)
            return _FakeConnection()

    def _fake_import(name: str):
        assert name == "pymssql"
        return _FakePymssql

    monkeypatch.setattr("skill_scripts.database_client.importlib.import_module", _fake_import)

    cfg = DatabaseConfig(
        driver="mssql",
        connection_string="server=127.0.0.1:1433;user=sa;password=Passw0rd!234;database=wferp_test",
        auth_mode="sql_auth",
        env="test",
        host="irrelevant-host",
        port=1433,
        database="irrelevant-db",
        username="irrelevant-user",
        password="irrelevant-pass",
        domain="",
        odbc_driver="ODBC Driver 18 for SQL Server",
    )
    client = DatabaseClient(cfg)

    rows = client.execute_readonly("SELECT [MK002],[MK006] FROM [wferp_test].[dbo].[ACTMK]")

    assert captured["server"] == "127.0.0.1"
    assert captured["port"] == 1433
    assert captured["user"] == "sa"
    assert captured["password"] == "Passw0rd!234"
    assert captured["database"] == "wferp_test"
    assert rows == [{"MK002": "2026", "MK006": 100000.0}]
