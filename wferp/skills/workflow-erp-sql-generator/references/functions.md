# Workflow ERP SQL Tooling Function Reference

## Purpose

This document lists the main functions, entrypoints, and operator-facing commands in the current repo. It is organized by workflow rather than by source file so future engineers and agents can quickly choose the right path.

## 1) Legacy schema publication functions

### Extract ERP metadata

- **Entry point:** `_Source/1_mssql_to_json.py`
- **Purpose:** connect to Workflow ERP MSSQL tables and export metadata JSON artifacts.
- **Primary inputs:** MSSQL connection settings in the script, source ERP database.
- **Primary outputs:** `MoudleName.json`, `TableName.json`, `TableStructure.json`, `TableIndexKey.json`.
- **Verification:** inspect generated JSON and continue the pipeline from `_Source/`.

### Repair encoding and enrich names

- **Entry point:** `_Source/2_FieldNameConvert2utf8.py`
- **Purpose:** fix mojibake and enrich Vietnamese-friendly labels.
- **Primary inputs:** JSON artifacts produced by step 1.
- **Primary outputs:** normalized metadata artifacts used by HTML generation and SQL tooling.
- **Verification:** confirm multilingual content stays readable and UTF-8 encoded.

### Generate static documentation

- **Entry points:** `_Source/3_CreateIndexHtml.py`, `_Source/4_CreateTableStructureHtml.py`
- **Purpose:** generate `index.html` and per-module detail pages in `HTML/`.
- **Primary inputs:** normalized JSON metadata.
- **Primary outputs:** published static HTML documentation.
- **Verification:** open `index.html`, click several modules, and confirm iframe pages load.

### Generate optional SQL artifacts

- **Entry point:** `_Source/5_CreateTableStructureSQL.py`
- **Purpose:** emit per-table SQL files under `SQL/` when needed.
- **Primary inputs:** schema metadata artifacts.
- **Primary outputs:** SQL artifact files.
- **Verification:** inspect generated SQL files and confirm expected module/table coverage.

## 2) SQL tooling functions

### Build runtime artifacts

- **Command:** `python3 -m skill_scripts.cli_generate_select --build-artifacts`
- **Purpose:** precompute lookup artifacts for the SQL-generation pipeline.
- **Implementation path:** `skill_scripts/cli_generate_select.py` → `schema_loader.py` → `data_dictionary.py` / `relationship_graph.py`.
- **Primary outputs:** JSON files under `skill_scripts/artifacts/`.
- **Verification:** confirm the command exits successfully and artifact files are written.

### Generate SQL from a prompt

- **Command:** `python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"`
- **Purpose:** return a single SQL statement for a natural-language request.
- **Implementation path:** `cli_generate_select.py` → `sql_router.py` → `sql_generator.py` or `llm_sql_generator.py`.
- **Primary outputs:** SQL plus route metadata printed to stdout.
- **Verification:** run execution validation before treating the SQL as correct.

### Route generation modes

- **Modes:** `rule`, `shadow`, `llm-first`
- **Purpose:** choose between deterministic generation, comparison mode, and LLM-first generation.
- **Implementation path:** `skill_scripts/sql_router.py`
- **Verification:** use pytest (`tests/skill_scripts/test_sql_router.py`) for behavior changes.

### Parse user intent

- **Implementation path:** `skill_scripts/intent_parser.py`
- **Purpose:** detect `TOP`, year filters, and non-select intent.
- **Consumers:** `sql_generator.py`, `sql_router.py`.
- **Verification:** update and run tests that cover prompt parsing behavior.

### Build schema context

- **Implementation paths:** `schema_loader.py`, `schema_context_builder.py`, `data_dictionary.py`, `relationship_graph.py`
- **Purpose:** transform legacy metadata artifacts into a runtime context for generation and validation.
- **Verification:** run `pytest tests/skill_scripts/test_schema_loader.py -v` and related context/index tests.

## 3) Validation functions

### SQL policy validation

- **Implementation path:** `skill_scripts/sql2000_guard.py`
- **Purpose:** reject non-`SELECT` SQL, multi-statement SQL, and SQL Server 2000-incompatible syntax.
- **Verification:** `pytest tests/skill_scripts/test_sql2000_guard.py -v`.

### Metadata validation

- **Implementation path:** `skill_scripts/metadata_validator.py`
- **Purpose:** ensure all referenced tables and columns exist in the schema bundle.
- **Verification:** `pytest tests/skill_scripts/test_metadata_validator.py -v`.

### Prompt/SQL consistency validation

- **Implementation path:** `skill_scripts/prompt_sql_consistency.py`
- **Purpose:** check that prompt constraints like year or domain terms are reflected in generated SQL.
- **Verification:** `pytest tests/skill_scripts/test_prompt_sql_consistency.py -v`.

### Execution validation

- **Implementation path:** `skill_scripts/execution_validator.py`
- **Purpose:** execute SQL and verify rows, columns, and aggregate expectations.
- **Verification:** run targeted pytest and then execute against `test_db/` when validating real SQL behavior.

### Database access

- **Implementation path:** `skill_scripts/database_client.py`
- **Purpose:** provide driver selection, connection-string handling, health checks, and read-only execution.
- **Verification:** `pytest tests/skill_scripts/test_database_client.py -v` plus live DB validation when changing runtime behavior.

## 4) Test and environment functions

### Run the SQL-tooling test suite

- **Command:** `pytest tests/skill_scripts/ -v`
- **Purpose:** verify `skill_scripts/` behavior.
- **Notes:** `tests/conftest.py` adds `schema/` to `sys.path`.

### Start the test database

- **Command:** `docker compose -f test_db/docker-compose.testdb.yml up -d`
- **Purpose:** start the SQL Server validation environment.
- **Verification:** wait for container health check to pass.

### Seed the test database

- **Command:** `docker exec -i wferp-mssql-test /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P Passw0rd\!234 -i /init/01_create_wferp_test.sql`
- **Purpose:** initialize schema and seed data for SQL validation.
- **Verification:** run a known good query or a prompt with `--validate-execution`.

## 5) Function selection guide

| Need | Use |
| --- | --- |
| Regenerate schema docs from ERP | `_Source/` pipeline |
| Build/query SQL tooling artifacts | `skill_scripts/cli_generate_select.py --build-artifacts` |
| Generate SQL from a prompt | `skill_scripts/cli_generate_select.py --prompt ...` |
| Validate generated SQL correctness | `test_db/` + execution validation |
| Update agent-facing usage rules | `skills/workflow-erp-sql-generator/SKILL.md` and reference docs |
