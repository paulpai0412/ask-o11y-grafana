from dataclasses import dataclass, replace
import importlib
import os
from typing import Any

from skill_scripts.sql2000_guard import validate_sql


@dataclass(frozen=True)
class DatabaseConfig:
    driver: str
    connection_string: str
    auth_mode: str
    env: str
    host: str
    port: int
    database: str
    username: str
    password: str
    domain: str
    odbc_driver: str

    @staticmethod
    def from_env() -> "DatabaseConfig":
        return DatabaseConfig(
            driver=os.getenv("DB_DRIVER", "mssql").strip().lower(),
            connection_string=os.getenv("DB_CONNECTION_STRING", "").strip(),
            auth_mode=os.getenv("DB_AUTH_MODE", "sql_auth").strip().lower(),
            env=os.getenv("DB_ENV", "test").strip().lower(),
            host=os.getenv("DB_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("DB_PORT", "1433").strip()),
            database=os.getenv("DB_DATABASE", "wferp_test").strip(),
            username=os.getenv("DB_USERNAME", "sa").strip(),
            password=os.getenv("DB_PASSWORD", "").strip(),
            domain=os.getenv("DB_DOMAIN", "").strip(),
            odbc_driver=os.getenv("DB_ODBC_DRIVER", "ODBC Driver 18 for SQL Server").strip(),
        )

    def with_overrides(
        self,
        *,
        driver: str | None = None,
        connection_string: str | None = None,
        auth_mode: str | None = None,
        env: str | None = None,
    ) -> "DatabaseConfig":
        updated = self
        if driver is not None:
            updated = replace(updated, driver=driver.strip().lower())
        if connection_string is not None:
            updated = replace(updated, connection_string=connection_string.strip())
        if auth_mode is not None:
            updated = replace(updated, auth_mode=auth_mode.strip().lower())
        if env is not None:
            updated = replace(updated, env=env.strip().lower())
        return updated

    def resolved_connection_string(self) -> str:
        if self.connection_string:
            return self.connection_string

        if self.driver == "mssql":
            user = self.username
            if self.auth_mode == "windows_domain" and self.domain:
                user = f"{self.domain}\\{self.username}"
            return (
                f"server={self.host}:{self.port};"
                f"user={user};"
                f"password={self.password};"
                f"database={self.database}"
            )

        if self.driver == "pyodbc":
            if self.auth_mode == "windows_domain":
                return (
                    f"DRIVER={{{self.odbc_driver}}};"
                    f"SERVER={self.host},{self.port};"
                    f"DATABASE={self.database};"
                    "Trusted_Connection=yes;"
                )
            return (
                f"DRIVER={{{self.odbc_driver}}};"
                f"SERVER={self.host},{self.port};"
                f"DATABASE={self.database};"
                f"UID={self.username};PWD={self.password};"
                "Encrypt=no;"
            )

        raise RuntimeError("DB_DRIVER_UNSUPPORTED")


class DatabaseClient:
    def __init__(self, config: DatabaseConfig):
        self.config = config

    @staticmethod
    def _parse_kv_connection_string(conn_str: str) -> dict[str, str]:
        parts = [segment.strip() for segment in str(conn_str or "").split(";") if segment.strip()]
        parsed: dict[str, str] = {}
        for part in parts:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            parsed[key.strip().lower()] = value.strip()
        return parsed

    @staticmethod
    def _split_server_and_port(server: str) -> tuple[str, int | None]:
        text = str(server or "").strip()
        if not text:
            return "", None
        if ":" in text:
            host, port_text = text.rsplit(":", 1)
            try:
                return host.strip(), int(port_text.strip())
            except ValueError:
                return text, None
        return text, None

    def health_check(self) -> tuple[bool, str]:
        conn_str = self.config.resolved_connection_string().strip()
        if not conn_str:
            return False, "DB_CONNECTION_NOT_CONFIGURED"

        if self.config.driver == "mssql" and self.config.auth_mode == "sql_auth" and not self.config.password:
            return False, "DB_PASSWORD_MISSING"

        if self.config.auth_mode == "windows_domain" and not self.config.username:
            return False, "DB_USERNAME_MISSING"

        return True, "OK"

    def _connect(self):
        conn_str = self.config.resolved_connection_string()

        if self.config.driver == "mssql":
            try:
                pymssql = importlib.import_module("pymssql")
            except ImportError as exc:
                raise RuntimeError("DB_DRIVER_NOT_INSTALLED") from exc
            try:
                cfg = self._parse_kv_connection_string(conn_str)
                server = cfg.get("server", self.config.host)
                user = cfg.get("user", cfg.get("uid", self.config.username))
                password = cfg.get("password", cfg.get("pwd", self.config.password))
                database = cfg.get("database", cfg.get("initial catalog", self.config.database))

                host, parsed_port = self._split_server_and_port(server)
                kwargs: dict[str, Any] = {
                    "server": host or self.config.host,
                    "user": user,
                    "password": password,
                    "database": database,
                }
                if parsed_port is not None:
                    kwargs["port"] = parsed_port
                return pymssql.connect(**kwargs)
            except Exception as exc:
                raise RuntimeError("DB_CONNECTION_FAILED") from exc

        if self.config.driver == "pyodbc":
            try:
                pyodbc = importlib.import_module("pyodbc")
            except ImportError as exc:
                raise RuntimeError("DB_DRIVER_NOT_INSTALLED") from exc
            try:
                return pyodbc.connect(conn_str)
            except Exception as exc:
                raise RuntimeError("DB_CONNECTION_FAILED") from exc

        raise RuntimeError("DB_DRIVER_UNSUPPORTED")

    def execute_readonly(self, sql: str) -> list[dict[str, Any]]:
        ok, code = validate_sql(sql)
        if not ok:
            raise RuntimeError(code)

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [str(col[0]) for col in cur.description] if cur.description else []
            rows = cur.fetchall()
            return [{columns[i]: row[i] for i in range(len(columns))} for row in rows]
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("DB_EXECUTION_FAILED") from exc
        finally:
            try:
                conn.close()
            except Exception:
                pass
