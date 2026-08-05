# Workflow ERP Agent Skill Guide

## Purpose

This guide explains how engineers and coding agents should work safely with the Workflow ERP SQL-generation skill and the surrounding repo.

## 1) Where to work

- the real git repo root is `schema/`;
- `_Source/` is the legacy artifact producer;
- `skill_scripts/` is the current SQL-generation implementation;
- `test_db/` is the default SQL validation environment;
- planning docs under `docs/` are helpful context but are not the executable source of truth.

## 2) When to use the skill

Use `skills/workflow-erp-sql-generator/SKILL.md` when the task is to convert a Workflow ERP natural-language request into one SQL statement and safety matters more than free-form generation.

Typical cases:

- turn a Chinese business prompt into SQL;
- enforce SQL Server 2000 compatibility;
- verify referenced identifiers against ERP metadata;
- execute and validate the SQL in the test environment.

Do not use the skill for:

- write operations or DDL;
- multi-statement SQL batches;
- generic database administration unrelated to Workflow ERP metadata.

## 3) Required safety rules

- SQL must be `SELECT`-only;
- SQL must stay compatible with SQL Server 2000 restrictions;
- generated SQL must reference known tables and columns from the metadata bundle;
- SQL generation is not complete until execution and result validation succeed in the test environment.

## 4) Required verification flow

1. generate or inspect the SQL candidate;
2. run policy validation implicitly through the tooling;
3. start and seed `test_db/` if execution validation is needed;
4. execute the SQL in test mode;
5. verify returned columns, row counts, and aggregate expectations match prompt intent.

## 5) Recommended workflow for agents

### For legacy pipeline tasks

- work in `_Source/`;
- run scripts from `_Source/`, not from repo root;
- preserve UTF-8 and current Windows-style HTML link formatting.

### For SQL-tooling tasks

- work in `skill_scripts/` and `tests/skill_scripts/`;
- use pytest for module-level verification;
- use `test_db/` for real SQL verification;
- update `skills/workflow-erp-sql-generator/references/` when behavior changes require new operator guidance.

## 6) Minimum context an agent should read first

1. `AGENTS.md`
2. `README.md`
3. `skills/workflow-erp-sql-generator/SKILL.md`
4. `INSTALLATION.md`
5. `OPERATIONS.md`

## 7) Common mistakes to avoid

- treating `/home/timmypai/apps/wferp` as the real code repo instead of `schema/`;
- changing SQL behavior without updating or running `tests/skill_scripts/`;
- assuming syntactically valid SQL is correct without running it against `test_db/`;
- editing generated artifacts casually when the right fix belongs in `_Source/` or `skill_scripts/`.

## 8) Related files

- `skills/workflow-erp-sql-generator/SKILL.md`
- `skills/workflow-erp-sql-generator/references/functions.md`
- `docs/superpowers/specs/2026-05-05-wferp-system-specification.md`
- `OPERATIONS.md`
