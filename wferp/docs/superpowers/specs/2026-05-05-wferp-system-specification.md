# Workflow ERP Schema and SQL Tooling System Specification

Date: 2026-05-05  
Project: `wferp/schema`  
Topic: Define the current system scope, supported workflows, inputs/outputs, and required verification rules for the Workflow ERP schema documentation generator and SQL-generation tooling.

## 1) Purpose

This repository serves two related purposes:

1. publish browsable Workflow ERP schema documentation as static HTML; and
2. generate SQL Server 2000-compatible, read-only SQL from natural-language prompts using the published schema metadata.

The repo is intentionally split between a legacy schema-extraction pipeline in `_Source/` and a newer SQL-generation toolchain in `skill_scripts/`.

## 2) System Boundary

### In scope

- extracting module, table, field, and index metadata from Workflow ERP source databases;
- normalizing and enriching metadata for multilingual schema browsing;
- generating static HTML pages (`index.html`, `HTML/`) from metadata artifacts;
- loading metadata artifacts for SQL-generation workflows;
- producing single-statement, `SELECT`-only SQL;
- validating generated SQL against SQL Server 2000 restrictions, schema metadata, prompt intent, and optional test-database execution;
- providing a skill definition for agent-driven SQL generation.

### Out of scope

- write operations (`INSERT`, `UPDATE`, `DELETE`, DDL, `EXEC`);
- multi-statement SQL batches;
- automated deployment or CI/CD orchestration;
- replacing the ERP source database as the authoritative metadata source;
- business approval of query meaning without execution/result validation.

## 3) Primary Users

- engineers maintaining the schema pipeline or SQL tooling;
- agents generating or validating Workflow ERP SQL;
- operators regenerating documentation artifacts;
- readers browsing `index.html` and per-module HTML pages.

## 4) Source-of-Truth Directories

| Path | Role |
| --- | --- |
| `_Source/` | legacy extraction, encoding repair, HTML generation, and optional SQL artifact generation |
| `skill_scripts/` | current SQL-generation implementation and validation pipeline |
| `tests/skill_scripts/` | pytest verification for `skill_scripts` |
| `test_db/` | Dockerized SQL Server test environment |
| `skills/workflow-erp-sql-generator/` | agent skill definition and supporting references |

## 5) Supported Workflows

### 5.1 Legacy schema publication workflow

1. connect to Workflow ERP MSSQL metadata tables;
2. extract JSON artifacts such as `MoudleName.json`, `TableName.json`, `TableStructure.json`, and `TableIndexKey.json`;
3. repair encoding issues and enrich multilingual field names;
4. generate `index.html` and per-module pages in `HTML/`;
5. optionally generate table-level SQL artifacts.

### 5.2 SQL generation workflow

1. load metadata artifacts from `_Source/`;
2. parse prompt intent and route the request;
3. generate SQL via rule mode or LLM-first mode;
4. enforce SQL2000 and `SELECT`-only restrictions;
5. validate table and column references against metadata;
6. validate prompt/SQL consistency;
7. optionally execute in the test database and validate output shape and values.

## 6) Inputs and Outputs

### Inputs

- ERP metadata stored in Workflow ERP SQL Server tables;
- generated JSON artifacts under `_Source/`;
- natural-language prompts for SQL generation;
- runtime database environment variables for execution validation.

### Outputs

- static documentation artifacts: `index.html`, `HTML/`, optional `SQL/`;
- JSON metadata artifacts in `_Source/` and precomputed lookup artifacts in `skill_scripts/artifacts/`;
- one SQL statement printed by `python3 -m skill_scripts.cli_generate_select`;
- validation results from pytest and test-database execution.

## 7) Functional Requirements

### 7.1 Legacy pipeline requirements

- scripts in `_Source/` must run from that directory because they rely on relative paths;
- generated HTML must keep the existing Windows-style link formatting (`HTML\\...`) used by current output;
- edits touching multilingual content must preserve UTF-8 encoding.

### 7.2 SQL generation requirements

- generated SQL must be one `SELECT` statement only;
- SQL must reject forbidden write or DDL keywords;
- SQL must reject SQL Server 2000-incompatible constructs such as CTEs, window functions, `OFFSET/FETCH`, `EXCEPT`, and `INTERSECT`;
- all referenced tables and columns must exist in the loaded schema bundle;
- execution validation must default to `DB_ENV=test` and require an explicit override for non-test environments.

## 8) Verification Requirements

### Mandatory policy

Any task that generates SQL is incomplete until the SQL has been executed and the returned result has been checked for correctness.

### Verification surfaces

- static-doc changes: manual browser verification of `index.html` and `HTML/` pages;
- SQL-tooling changes: pytest in `tests/skill_scripts/`;
- SQL prompt changes or SQL-generation changes: test database execution through `test_db/` plus result validation.

## 9) Operational Constraints

- no verified CI, lint, formatter, or typecheck pipeline is checked in at repo level;
- dependency setup is manual (`pymssql`, `pandas`, `pytest`, optional DB drivers);
- test database setup is Docker-based and uses SQL Server 2019 configured for SQL Server 2000 syntax compatibility.

## 10) Success Criteria

The system is operating as intended when:

- schema artifacts can be regenerated without breaking existing HTML browsing;
- SQL prompts return compliant, validated `SELECT` statements;
- SQL execution succeeds in the test environment and result checks match user intent;
- agents can determine the correct working area (`_Source/` vs `skill_scripts/`) without ambiguity.

## 11) References

- `README.md`
- `AGENTS.md`
- `INSTALLATION.md`
- `OPERATIONS.md`
- `skills/workflow-erp-sql-generator/SKILL.md`
- `skills/workflow-erp-sql-generator/references/functions.md`
