# AGENTS.md

## Repo At A Glance
- Repo root is `/home/timmypai/apps/grafana/wferp`; some docs still call this checkout `schema/`.
- This repo has two coupled surfaces: static Workflow ERP schema docs and newer natural-language-to-SQL tooling.
- Published static artifacts live at root: `index.html`, `df_style.css`, `HTML/`.
- Canonical schema metadata artifacts live in `_Source/` and are consumed by both HTML generation and `skill_scripts/`.

## High-Value Structure
- `_Source/1_mssql_to_json.py` extracts ERP metadata from MSSQL `DSCSYS` tables into JSON.
- `_Source/2_FieldNameConvert2utf8.py` repairs mojibake (`iso-8859-1` -> Big5) and enriches Vietnamese names from `language.json`.
- `_Source/3_CreateIndexHtml.py` and `_Source/4_CreateTableStructureHtml.py` generate `index.html` and `HTML/`.
- `_Source/5_CreateTableStructureSQL.py` optionally emits table SQL files under `SQL/`.
- `skill_scripts/` is the current SQL-generation and validation stack; `skill_scripts/cli_generate_select.py` is the CLI entrypoint.
- `tests/skill_scripts/` is the pytest suite for SQL tooling; `test_db/` is the SQL Server execution-validation environment.
- `skills/workflow-erp-sql-generator/` contains the agent-facing SQL-generation skill and references.

## Setup
- There is no checked-in dependency manifest. Install the verified Python dependencies manually:

```bash
python3 -m pip install pymssql pandas pytest
```

- `pyodbc` is optional and only needed for the `DB_DRIVER=pyodbc` path in `skill_scripts/database_client.py`.

## Legacy Static-Doc Regeneration
- Run from `_Source/`; these scripts use relative paths and will read/write the wrong locations from repo root.
- Before step 1, edit `_Source/1_mssql_to_json.py` credentials: `SERVER_IP`, `USERNAME`, `PASSWORD`, `DATABASE`.

```bash
python3 1_mssql_to_json.py
python3 2_FieldNameConvert2utf8.py
python3 3_CreateIndexHtml.py
python3 4_CreateTableStructureHtml.py
# optional
python3 5_CreateTableStructureSQL.py
```

- Static-doc verification: open `index.html` in a browser, click several modules, and confirm iframe pages under `HTML/` load.

## SQL Tooling Commands
- Build derived artifacts after changing `_Source/` JSON, schema loading, dictionary, or relationship logic:

```bash
python3 -m skill_scripts.cli_generate_select --build-artifacts
```

- Generate SQL from repo root. Default mode is `llm-first`; use `--mode rule` for deterministic behavior.

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆" --mode rule
pytest tests/skill_scripts/ -v
pytest tests/skill_scripts/test_schema_loader.py -v
```

## SQL Execution Validation
- Any task that generates SQL is incomplete until the SQL executes and returned rows/columns/aggregates match the prompt intent.
- Use the containerized test DB by default; it runs SQL Server 2019 at compatibility level 80 for SQL Server 2000 syntax targeting.

```bash
docker compose -f test_db/docker-compose.testdb.yml up -d
docker exec -i wferp-mssql-test /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P Passw0rd\!234 -i /init/01_create_wferp_test.sql
export DB_DRIVER=mssql
export DB_AUTH_MODE=sql_auth
export DB_CONNECTION_STRING="server=127.0.0.1:1433;user=sa;password=Passw0rd!234;database=wferp_test"
export DB_ENV=test
python3 -m skill_scripts.cli_generate_select --prompt "查詢2026年的工程預算明細" --validate-execution --required-columns MK002,MK006 --min-rows 1
```

- `skill_scripts/sql_router.py` blocks execution validation outside `DB_ENV=test` unless `--allow-non-test-db-execution` is explicitly passed.

## Repo-Specific Gotchas
- Preserve UTF-8 when editing Chinese/Vietnamese text or generated JSON/HTML.
- Keep existing Windows-style generated paths such as `HTML\\...` and `SQL\\...`; several generators intentionally emit them.
- `_Source/MoudleName.json` and code spell “Moudle” incorrectly; keep that filename/API spelling unless doing a coordinated migration.
- `_Source/` contains scripts plus generated JSON; do not delete generated JSON unless intentionally regenerating all dependent artifacts.
- There is no verified GitHub Actions, pre-commit, lint, formatter, or typecheck pipeline checked in at repo level.

## Instruction Sources
- Install/run details: `INSTALLATION.md` and `OPERATIONS.md`.
- Current system/architecture context: `docs/superpowers/specs/2026-05-05-wferp-system-specification.md` and `docs/superpowers/specs/2026-05-05-wferp-architecture-design.md`.
- Issues and PRDs use GitHub repo `paulpai0412/wferp`; see `docs/agents/issue-tracker.md`.
- Triage label vocabulary and domain-doc expectations are in `docs/agents/triage-labels.md` and `docs/agents/domain.md`.
