# Implementation Guide

## Contract

- Input: natural-language request and ERP metadata bundle from `_Source`.
- Output: exactly one `SELECT` statement (`TOP n` optional).
- Table references: bracketed format `[DB].[schema].[Table]`.
- Guard: SQL must pass `skill_scripts.sql2000_guard.validate_sql(sql)` before return.
- Selected columns must include recognizable aliases, e.g. `...[Table].[MK006] AS [本幣借方金額]`.

## Runtime Context

Run commands from `/home/timmypai/apps/wferp/schema` (or set equivalent working directory/PYTHONPATH), because examples assume top-level `skill_scripts` import resolution and default `--source _Source`.

## Connection Baseline Priority

When the user provides explicit connection metadata, treat it as the generation baseline:

- Prefer user-provided `Initial Catalog` as SQL database qualifier.
- Prefer user-provided `Data Source` as execution target host context.
- Keep this baseline unless the user explicitly requests another database/server.

Current recorded baseline from conversation:

- `Data Source = css04`
- `Initial Catalog = CHD`
- Default object context example: `[CHD].[dbo].[View_Customer]`
- Runtime override available: set `WFERP_DEFAULT_DB=CHD` to force rule-mode generated SQL to use `[CHD].[dbo]` qualifiers even when source metadata still carries legacy database names such as `VPIC1.dbo.`

## CLI Entrypoint

- File: `skill_scripts/cli_generate_select.py`
- Entry function: `main()`

Primary arguments:

- `--prompt`, `--source`, `--mode`
- `--llm-provider` (`pi`, `codex`, `opencode`, `mock`, `openai-compatible`, `none`)
- `--llm-model`, `--llm-timeout-sec`, `--llm-min-confidence`, `--llm-repair-attempts`

`pi`, `codex`, and `opencode` are implemented through the Node SDK bridge at
`skill_scripts/llm_sdk/`. Set `WFERP_LLM_PROVIDER` and `WFERP_LLM_MODEL` to
configure defaults without repeating CLI flags.
- `--validate-execution`, `--required-columns`, `--min-rows`, `--max-rows`, `--aggregate-checks`
- `--allow-non-test-db-execution` (unsafe override)
- `--db-driver`, `--db-connection-string`, `--db-auth-mode`, `--db-env`

## Environment Keys

- `DB_DRIVER`
- `DB_CONNECTION_STRING`
- `DB_AUTH_MODE`
- `DB_ENV`
- `DB_HOST`
- `DB_PORT`
- `DB_DATABASE`
- `DB_USERNAME`
- `DB_PASSWORD`
- `DB_DOMAIN`
- `DB_ODBC_DRIVER`
- `WFERP_LLM_PROVIDER`
- `WFERP_LLM_MODEL`
- `WFERP_NODE_EXECUTABLE` (optional Node.js path override)

## Common Mistakes

- Returning more than one SQL statement.
- Using unbracketed table format or incomplete table paths.
- Generating SQL Server 2005+ features (`WITH`, window functions, `OFFSET/FETCH`).
- Validating execution on non-test environment without explicit unsafe override.
- Assuming `--build-artifacts` is required for normal generation; SQL generation/metadata validation reads directly from `--source`, while artifact building is for producing artifact files.
