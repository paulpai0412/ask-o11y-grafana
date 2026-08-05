---
name: workflow-erp-sql-generator
description: Use when converting Workflow ERP natural-language data requests into SQL Server 2000-compatible read-only queries that must enforce SELECT-only safety rules, strict identifier validation, and optional execution/result checks.
---

# Workflow ERP SQL Generator

## Overview

Generate one SQL Server 2000-compatible `SELECT` statement from natural language and ERP metadata in `_Source`. Use this skill when correctness and safety constraints matter more than free-form SQL generation.

## When to Use

Use this skill when you need to:

- convert a user request into a single ERP `SELECT` query;
- enforce SQL Server 2000 compatibility (no CTE/window/OFFSET features);
- validate table/column identifiers against ERP metadata;
- optionally execute in test environment and validate columns, row counts, or aggregates.

Do not use this skill for write operations (`INSERT`/`UPDATE`/`DELETE`/DDL/`EXEC`) or multi-statement SQL batches.

## Safety Policy

- Reject non-SELECT intent and forbidden tokens:
  - `insert`, `update`, `delete`, `create`, `alter`, `drop`, `merge`, `truncate`, `exec`, `execute`
- Reject SQL Server 2000-incompatible constructs:
  - CTE `WITH ... AS (...)`
  - `OVER`, `PARTITION BY`, `ROW_NUMBER`, `RANK`, `DENSE_RANK`
  - `OFFSET`, `FETCH`
  - `EXCEPT`, `INTERSECT`
- Reject multi-statement SQL and batch separators.
- Reject unknown tables/columns that are not in metadata.
- Execution validation is test-only by default; non-test execution requires explicit unsafe override `--allow-non-test-db-execution`.
- Generated SQL must present selected columns with recognizable field aliases (`AS [可識別欄位名]`), and avoid raw code-only output as final result.
- If the user provides connection baseline metadata (for example ODC `Data Source` / `Initial Catalog`), generated SQL must use that database/catalog as the default qualifier unless the user explicitly overrides it.

## Quick Reference

| Goal | Command |
| --- | --- |
| Build artifacts | `python3 -m skill_scripts.cli_generate_select --build-artifacts` |
| Generate SQL | `python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"` |
| Rule mode only | `python3 -m skill_scripts.cli_generate_select --prompt "..." --mode rule` |
| Execution validation | `python3 -m skill_scripts.cli_generate_select --prompt "..." --validate-execution --required-columns MK002,MK006 --min-rows 1` |

LLM provider selection is configurable with `--llm-provider` or
`WFERP_LLM_PROVIDER`: `pi`, `codex`, and `opencode` use the official SDK bridge
under `skill_scripts/llm_sdk/`. Set `--llm-model` or `WFERP_LLM_MODEL` when a
specific model is required. `--mode rule` does not call an LLM.

## Supporting References

Use these references as needed (kept separate to keep this main skill lightweight):

- [Implementation Guide](references/implementation.md)
- [Modes and Routing](references/modes.md)
- [Error Codes](references/error-codes.md)
- [Examples](references/examples.md)
