# Workflow ERP Schema and SQL Tooling Architecture Design

Date: 2026-05-05  
Project: `wferp/schema`  
Topic: Describe the current architecture, subsystem boundaries, data flow, and extension points for the Workflow ERP schema documentation and SQL-generation stack.

## 1) Architecture Overview

The repository contains two coupled but distinct subsystems:

1. a **legacy publishing pipeline** in `_Source/` that extracts ERP metadata and turns it into static documentation; and
2. a **current SQL-generation stack** in `skill_scripts/` that consumes those metadata artifacts to generate and validate SQL.

The design deliberately keeps metadata extraction separate from SQL generation. `_Source/` remains the producer of canonical artifacts, while `skill_scripts/` acts as a consumer plus query-generation layer.

## 2) Major Components

### 2.1 Legacy publishing pipeline

| File | Responsibility |
| --- | --- |
| `_Source/1_mssql_to_json.py` | extract module/table/field metadata from MSSQL |
| `_Source/2_FieldNameConvert2utf8.py` | repair mojibake and enrich multilingual field labels |
| `_Source/3_CreateIndexHtml.py` | build top-level navigation page |
| `_Source/4_CreateTableStructureHtml.py` | generate module/table HTML detail pages |
| `_Source/5_CreateTableStructureSQL.py` | optionally emit SQL artifacts |

Design intent: keep the publishing path sequential and artifact-driven. Each later stage depends on output from the earlier stage.

### 2.2 Metadata consumption layer

| File | Responsibility |
| --- | --- |
| `skill_scripts/schema_loader.py` | load normalized schema bundle from `_Source/` JSON files |
| `skill_scripts/data_dictionary.py` | build field and alias lookup indexes |
| `skill_scripts/relationship_graph.py` | infer structural relationships from key metadata |
| `skill_scripts/schema_context_builder.py` | assemble relevant schema slices for prompt processing |

Design intent: keep raw artifact loading separate from derived indexes and context slicing so the SQL-generation path can remain composable.

### 2.3 SQL-generation layer

| File | Responsibility |
| --- | --- |
| `skill_scripts/cli_generate_select.py` | CLI entrypoint and runtime option parsing |
| `skill_scripts/intent_parser.py` | derive query intent (`TOP`, year, non-select intent) |
| `skill_scripts/sql_generator.py` | deterministic rule-based SQL generation |
| `skill_scripts/llm_sql_generator.py` | structured LLM prompt/response path |
| `skill_scripts/sql_router.py` | route between rule, shadow, and llm-first flows |

Design intent: isolate strategy-specific generation from orchestration. `sql_router.py` owns the decision tree, while individual generators stay focused on SQL candidate creation.

### 2.4 Validation and execution layer

| File | Responsibility |
| --- | --- |
| `skill_scripts/sql2000_guard.py` | enforce `SELECT`-only and SQL2000 syntax policy |
| `skill_scripts/metadata_validator.py` | verify identifiers exist in the schema bundle |
| `skill_scripts/prompt_sql_consistency.py` | compare prompt constraints with generated SQL |
| `skill_scripts/execution_validator.py` | execute SQL and validate returned rows/columns/aggregates |
| `skill_scripts/database_client.py` | provide DB driver abstraction and connection policy |

Design intent: make safety checks layered and independently testable. A candidate query should fail as early as possible.

### 2.5 Skill and operator interface layer

| Path | Responsibility |
| --- | --- |
| `skills/workflow-erp-sql-generator/SKILL.md` | declare the agent-facing skill contract |
| `skills/workflow-erp-sql-generator/references/` | hold implementation-facing reference docs |
| `test_db/` | provide a reusable validation environment |
| `tests/skill_scripts/` | define regression coverage for the tooling stack |

## 3) Data Flow

### 3.1 Legacy artifact flow

`ERP MSSQL` → `_Source/1_mssql_to_json.py` → JSON artifacts → `_Source/2_FieldNameConvert2utf8.py` → `_Source/3_CreateIndexHtml.py` / `_Source/4_CreateTableStructureHtml.py` → published HTML

### 3.2 SQL generation flow

`user prompt` → `intent_parser.py` → `schema_loader.py` + context helpers → `sql_router.py` → `sql_generator.py` or `llm_sql_generator.py` → validation chain → optional execution against `test_db` → final SQL/result status

### 3.3 Validation flow

`candidate SQL` → `sql2000_guard.py` → `metadata_validator.py` → `prompt_sql_consistency.py` → `execution_validator.py` (optional but mandatory for task completion when SQL is generated)

## 4) Runtime Modes

### Rule mode

- deterministic;
- low operational risk;
- used when predictable output is preferred.

### Shadow mode

- generates a rule answer while retaining an LLM candidate for comparison;
- useful for safe evaluation before relying on LLM output.

### LLM-first mode

- attempts LLM generation first;
- falls back or fails when confidence or validation checks do not pass.

## 5) Boundary Decisions

### Why `_Source/` remains separate

The extraction/publishing scripts are path-sensitive and produce the canonical artifacts consumed by the rest of the repo. Folding them into `skill_scripts/` would mix batch publication concerns with runtime query tooling.

### Why validation is multi-layered

The system must reject unsafe or invalid SQL before any database execution. Each validator catches a different failure class: syntax policy, metadata mismatch, prompt drift, and result mismatch.

### Why `test_db/` is treated as an architectural component

This repo defines SQL correctness operationally, not just syntactically. That makes the Dockerized test database part of the design, not merely a convenience.

## 6) Extension Points

- add new prompt-routing heuristics in `intent_parser.py` and `sql_router.py`;
- add richer schema scoring or table/field matching in `schema_context_builder.py` or `sql_generator.py`;
- add new LLM backends in `llm_sql_generator.py`;
- add stricter output assertions in `execution_validator.py`;
- add new reference docs under `skills/workflow-erp-sql-generator/references/` without changing the runtime contract.

## 7) Failure Handling

- missing DB credentials or drivers should fail through `database_client.py` with explicit runtime errors;
- unsupported SQL features should fail in `sql2000_guard.py` before execution;
- unknown identifiers should fail in `metadata_validator.py`;
- mismatched results should fail in `execution_validator.py` even when the SQL executes successfully;
- non-test execution validation should be blocked unless explicitly overridden.

## 8) Verification Strategy

- architectural changes to `skill_scripts/` should be covered by pytest in `tests/skill_scripts/`;
- execution-path changes should be checked against `test_db/`;
- publishing-path changes should be validated by regenerating artifacts and loading the HTML pages.

## 9) Reference Map

- system scope: `docs/superpowers/specs/2026-05-05-wferp-system-specification.md`
- function catalog: `skills/workflow-erp-sql-generator/references/functions.md`
- installation guide: `INSTALLATION.md`
- operations guide: `OPERATIONS.md`
