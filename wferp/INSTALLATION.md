# Installation Guide

## Purpose

This guide covers local setup for the Workflow ERP schema documentation and SQL-generation repo.

## 1) Prerequisites

- Python 3
- `pip`
- Docker and Docker Compose
- access to a Workflow ERP SQL Server instance if you need to regenerate `_Source/` artifacts from scratch

## 2) Repository root

Run the commands in this guide from `schema/` unless a step explicitly says `_Source/`.

## 3) Python environment

Create and activate a virtual environment if needed:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the verified dependencies used by this repo:

```bash
python3 -m pip install pymssql pandas pytest
npm install --prefix skill_scripts/llm_sdk
```

The Node bridge uses official SDKs for Pi, Codex, and OpenCode. OpenCode also
requires the `opencode` executable in `PATH` because its SDK starts the local
OpenCode server.

Notes:

- there is no checked-in dependency manifest;
- `pytest` is used by `tests/skill_scripts/` even though it is not declared in a repo-level requirements file;
- `pyodbc` is only needed if you choose that driver path in `database_client.py`.

## 4) Test database setup

Start the Dockerized SQL Server container:

```bash
docker compose -f test_db/docker-compose.testdb.yml up -d
```

Initialize schema and seed data after the container becomes healthy:

```bash
docker exec -i wferp-mssql-test /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P $MSSQL_SA_PASSWORD -i /init/01_create_wferp_test.sql
```

Export the default test environment variables:

```bash
export DB_DRIVER=mssql
export DB_AUTH_MODE=sql_auth
export DB_CONNECTION_STRING="server=127.0.0.1:1433;user=sa;password=$MSSQL_SA_PASSWORD;database=wferp_test"
export DB_ENV=test
```

## 5) First-run verification

Verify the schema loader smoke test:

```bash
pytest tests/skill_scripts/test_schema_loader.py -v
```

Build SQL-tooling artifacts:

```bash
python3 -m skill_scripts.cli_generate_select --build-artifacts
```

Run a sample SQL prompt:

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"
```

Select the LLM SDK explicitly when needed:

```bash
python3 -m skill_scripts.cli_generate_select --llm-provider pi --llm-model openai-codex/gpt-5.4 --prompt "查詢採購單前 20 筆"
python3 -m skill_scripts.cli_generate_select --llm-provider codex --llm-model gpt-5.4 --prompt "查詢採購單前 20 筆"
python3 -m skill_scripts.cli_generate_select --llm-provider opencode --llm-model opencode/claude-opus-4-6 --prompt "查詢採購單前 20 筆"
```

Provider defaults can be configured with `WFERP_LLM_PROVIDER` and
`WFERP_LLM_MODEL`. Pi and Codex use their own local authentication stores;
OpenCode uses its local CLI configuration.

## 6) Optional legacy regeneration setup

If you need to rebuild the legacy schema artifacts, edit credentials in `_Source/1_mssql_to_json.py` and then run the `_Source/` pipeline from the `_Source/` directory.

## 7) Troubleshooting

### `pytest` not found

Install it in the active environment:

```bash
python3 -m pip install pytest
```

### `DB_DRIVER_NOT_INSTALLED`

Install the required Python driver for the selected DB mode, typically `pymssql` for the default configuration.

### Connection failures in execution validation

- confirm the `wferp-mssql-test` container is healthy;
- confirm `DB_CONNECTION_STRING` and `DB_ENV=test` are exported in the current shell;
- rerun the seed command if the schema is missing.
