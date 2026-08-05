# Operation Manual

## Purpose

This runbook covers the day-to-day workflows for regenerating documentation, building SQL artifacts, validating SQL, and running tests.

## 1) Legacy schema regeneration

Run these commands from `schema/_Source/` because the scripts use relative paths:

```bash
python3 1_mssql_to_json.py
python3 2_FieldNameConvert2utf8.py
python3 3_CreateIndexHtml.py
python3 4_CreateTableStructureHtml.py
# optional
python3 5_CreateTableStructureSQL.py
```

Before step 1, update `SERVER_IP`, `USERNAME`, `PASSWORD`, and `DATABASE` inside `1_mssql_to_json.py`.

### Verification

1. open `schema/index.html` in a browser;
2. click several modules;
3. confirm iframe pages under `HTML/` load;
4. if SQL artifacts were regenerated, inspect the `SQL/` output as well.

## 2) Build SQL-tooling artifacts

Run from `schema/`:

```bash
python3 -m skill_scripts.cli_generate_select --build-artifacts
```

Use this after changing schema-loading, relationship, or dictionary logic, or when `_Source/` artifacts change.

## 3) Generate SQL from a prompt

### Default command

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"
```

### Rule-only mode

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆" --mode rule
```

### Shadow mode

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢2026年的工程預算明細" --mode shadow
```

### LLM-first mode

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢2026年的工程預算明細" --mode llm-first
```

The default provider is `opencode`. Override it per command or with
`WFERP_LLM_PROVIDER`:

```bash
python3 -m skill_scripts.cli_generate_select --llm-provider pi --llm-model openai-codex/gpt-5.4 --prompt "查詢2026年的工程預算明細"
python3 -m skill_scripts.cli_generate_select --llm-provider codex --llm-model gpt-5.4 --prompt "查詢2026年的工程預算明細"
python3 -m skill_scripts.cli_generate_select --llm-provider opencode --llm-model opencode/claude-opus-4-6 --prompt "查詢2026年的工程預算明細"
```

`pi` uses the Pi SDK and Pi credentials, `codex` uses the Codex SDK and Codex
credentials, and `opencode` uses the OpenCode SDK to start the local OpenCode
server. `--mode rule` remains completely LLM-free.

## 4) Validate SQL execution and result correctness

Start and seed the test DB if needed:

```bash
docker compose -f test_db/docker-compose.testdb.yml up -d
docker exec -i wferp-mssql-test /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P $MSSQL_SA_PASSWORD -i /init/01_create_wferp_test.sql
```

Export the test environment variables in the current shell:

```bash
export DB_DRIVER=mssql
export DB_AUTH_MODE=sql_auth
export DB_CONNECTION_STRING="server=127.0.0.1:1433;user=sa;password=$MSSQL_SA_PASSWORD;database=wferp_test"
export DB_ENV=test
```

Run a prompt with execution validation:

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢2026年的工程預算明細" --validate-execution --required-columns MK002,MK006 --min-rows 1
```

### Required operator rule

Do not stop at SQL string inspection. A SQL-generation task is only complete after:

1. the SQL executes successfully; and
2. the returned result matches the prompt intent.

## 5) Run tests

Run the full SQL-tooling suite:

```bash
pytest tests/skill_scripts/ -v
```

Run one focused test file:

```bash
pytest tests/skill_scripts/test_schema_loader.py -v
```

Use focused tests while iterating and the full suite before finalizing tooling changes.

## 6) Operational decision guide

| Task | Work area |
| --- | --- |
| Source ERP metadata changed | `_Source/` |
| SQL generation logic changed | `skill_scripts/` + `tests/skill_scripts/` |
| Query execution validation changed | `skill_scripts/` + `test_db/` |
| Agent usage guidance changed | `skills/workflow-erp-sql-generator/` |

## 7) Common failure cases

### Generated SQL looks valid but fails task intent

Run execution validation with required columns or aggregate checks. Do not accept the query based only on syntax.

### Test DB rejects execution

- check container health;
- confirm the DB environment variables are exported;
- confirm the seed SQL ran successfully.

### HTML output breaks after regeneration

- verify you ran the scripts from `_Source/`;
- verify generated links still keep the expected Windows-style `HTML\\...` format;
- rerun the pipeline in sequence instead of skipping intermediate steps.

## 8) Related references

- `AGENTS.md`
- `INSTALLATION.md`
- `skills/workflow-erp-sql-generator/SKILL.md`
- `skills/workflow-erp-sql-generator/references/functions.md`
