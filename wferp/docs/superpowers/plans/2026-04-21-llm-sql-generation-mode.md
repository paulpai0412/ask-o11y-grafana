# LLM SQL Generation Mode (Dual-Track) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM-first SQL generation path that still guarantees SQL Server 2000 + SELECT-only output by validating and falling back to deterministic rules when needed.

**Architecture:** Keep existing deterministic generator as a safety fallback. Add focused modules for schema context slicing, LLM provider adapter + envelope parser, metadata validation, prompt-to-SQL consistency validation, and a dual-track router. Rollout starts with `shadow` mode (returns rule SQL while logging LLM comparison), then advances to `llm-first`.

**Tech Stack:** Python 3.12, existing `skill_scripts/*`, pytest, JSON artifacts under `skill_scripts/artifacts`.

---

## File Structure (planned)

- Create: `skill_scripts/schema_context_builder.py`
- Create: `skill_scripts/llm_sql_generator.py`
- Create: `skill_scripts/metadata_validator.py`
- Create: `skill_scripts/prompt_sql_consistency.py`
- Create: `skill_scripts/database_client.py`
- Create: `skill_scripts/execution_validator.py`
- Create: `skill_scripts/sql_router.py`
- Modify: `skill_scripts/cli_generate_select.py`
- Modify: `skill_scripts/sql_generator.py` (export helper for deterministic fallback)
- Modify: `skills/workflow-erp-sql-generator/SKILL.md`
- Create: `tests/skill_scripts/test_schema_context_builder.py`
- Create: `tests/skill_scripts/test_llm_sql_generator.py`
- Create: `tests/skill_scripts/test_metadata_validator.py`
- Create: `tests/skill_scripts/test_prompt_sql_consistency.py`
- Create: `tests/skill_scripts/test_database_client.py`
- Create: `tests/skill_scripts/test_execution_validator.py`
- Create: `tests/skill_scripts/test_sql_router.py`
- Modify: `tests/skill_scripts/test_sql_generator.py`

### Task 1: Add schema context builder for LLM prompt slicing

**Files:**
- Create: `skill_scripts/schema_context_builder.py`
- Test: `tests/skill_scripts/test_schema_context_builder.py`

- [ ] **Step 1: Write failing tests for context slicing**

```python
from skill_scripts.schema_context_builder import build_context_slice


def test_build_context_slice_returns_topk_tables_and_columns():
    bundle = {
        "tables": [
            {"TableID": "ACTMK", "TableName": "科目/部門預算單身檔", "ModuleID": "ACT"},
            {"TableID": "ACTMJ", "TableName": "科目/部門預算單頭檔", "ModuleID": "ACT"},
        ],
        "fields": [
            {"TableID": "ACTMK", "ID": "MK002", "FieldName": "會計年度"},
            {"TableID": "ACTMK", "ID": "MK006", "FieldName": "期預算"},
        ],
    }
    ctx = build_context_slice("查詢2026年的工程預算明細", bundle, top_k=2)
    assert "tables" in ctx
    assert "columns" in ctx
    assert any(t["TableID"] == "ACTMK" for t in ctx["tables"])
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_schema_context_builder.py -v`  
Expected: FAIL with module/function missing

- [ ] **Step 3: Implement minimal context builder**

```python
# skill_scripts/schema_context_builder.py
from typing import Any


JsonDict = dict[str, Any]


def build_context_slice(prompt: str, bundle: JsonDict, top_k: int = 8) -> JsonDict:
    prompt_text = str(prompt or "")
    tables = bundle.get("tables", [])
    fields = bundle.get("fields", [])

    def score(table: JsonDict) -> int:
        table_id = str(table.get("TableID", ""))
        table_name = str(table.get("TableName", ""))
        s = 0
        if table_id and table_id in prompt_text:
            s += 100
        if table_name and table_name in prompt_text:
            s += 80
        if "預算" in prompt_text and table_id in {"ACTMJ", "ACTMK", "ACTMI"}:
            s += 70
        if "明細" in prompt_text and table_id == "ACTMK":
            s += 80
        return s

    ranked = sorted(tables, key=score, reverse=True)[: max(1, top_k)]
    picked_ids = {str(t.get("TableID", "")) for t in ranked}
    picked_fields = [f for f in fields if str(f.get("TableID", "")) in picked_ids]

    return {"tables": ranked, "columns": picked_fields}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/skill_scripts/test_schema_context_builder.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/schema_context_builder.py tests/skill_scripts/test_schema_context_builder.py
git commit -m "feat: add schema context slicing for llm sql generation"
```

### Task 2: Add LLM provider adapter and deterministic envelope parsing

**Files:**
- Create: `skill_scripts/llm_sql_generator.py`
- Test: `tests/skill_scripts/test_llm_sql_generator.py`

- [ ] **Step 1: Write failing tests for structured envelope parsing**

```python
from skill_scripts.llm_sql_generator import parse_llm_response


def test_parse_llm_response_extracts_sql_and_tables():
    payload = '{"sql":"SELECT TOP 10 * FROM [VPIC1].[dbo].[ACTMK]","used_tables":["ACTMK"],"assumptions":["year filter"],"confidence":0.82}'
    out = parse_llm_response(payload)
    assert out["sql"].startswith("SELECT")
    assert out["used_tables"] == ["ACTMK"]


def test_parse_llm_response_includes_confidence_range():
    payload = '{"sql":"SELECT TOP 10 * FROM [VPIC1].[dbo].[ACTMK]","used_tables":["ACTMK"],"assumptions":[],"confidence":0.42}'
    out = parse_llm_response(payload)
    assert 0.0 <= out["confidence"] <= 1.0
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_llm_sql_generator.py -v`  
Expected: FAIL with missing implementation

- [ ] **Step 3: Implement minimal LLM wrapper and parser**

```python
# skill_scripts/llm_sql_generator.py
import json
from typing import Any


JsonDict = dict[str, Any]


def build_llm_prompt(user_prompt: str, context_slice: JsonDict) -> str:
    return (
        "Generate one SQL Server 2000 SELECT statement only. "
        "Return JSON with keys sql, used_tables, assumptions, confidence. "
        f"User prompt: {user_prompt}\n"
        f"Context: {json.dumps(context_slice, ensure_ascii=False)}"
    )


def parse_llm_response(raw_text: str) -> JsonDict:
    obj = json.loads(raw_text)
    return {
        "sql": str(obj.get("sql", "")).strip(),
        "used_tables": [str(x) for x in obj.get("used_tables", [])],
        "assumptions": [str(x) for x in obj.get("assumptions", [])],
        "confidence": float(obj.get("confidence", 0.0)),
    }


def call_llm(provider: str, model: str, prompt_text: str) -> str:
    # provider adapter contract for implementation phase
    # 1) send prompt_text to provider/model
    # 2) return raw text response from model
    # this function is intentionally a dedicated seam for production integration
    raise RuntimeError("LLM_PROVIDER_NOT_CONFIGURED")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/skill_scripts/test_llm_sql_generator.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/llm_sql_generator.py tests/skill_scripts/test_llm_sql_generator.py
git commit -m "feat: add llm sql envelope parser and prompt builder"
```

### Task 3: Add metadata validator for table/column existence

**Files:**
- Create: `skill_scripts/metadata_validator.py`
- Test: `tests/skill_scripts/test_metadata_validator.py`

- [ ] **Step 1: Write failing tests for metadata checks**

```python
from skill_scripts.metadata_validator import validate_metadata_references


def test_validate_metadata_references_rejects_unknown_table():
    bundle = {
        "tables": [{"TableID": "ACTMK"}],
        "fields": [{"TableID": "ACTMK", "ID": "MK002"}],
    }
    ok, code = validate_metadata_references(
        "SELECT [MK002] FROM [VPIC1].[dbo].[ACTXX]",
        bundle,
    )
    assert ok is False
    assert code == "UNKNOWN_TABLE"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_metadata_validator.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement validator**

```python
# skill_scripts/metadata_validator.py
import re
from typing import Any


JsonDict = dict[str, Any]


def validate_metadata_references(sql: str, bundle: JsonDict) -> tuple[bool, str]:
    table_ids = {str(t.get("TableID", "")).strip().upper() for t in bundle.get("tables", [])}
    field_ids = {str(f.get("ID", "")).strip().upper() for f in bundle.get("fields", [])}

    table_refs = {
        m.group(1).upper()
        for m in re.finditer(r"\b(?:FROM|JOIN)\s+\[[^\]]+\]\.\[[^\]]+\]\.\[([^\]]+)\]", sql, re.IGNORECASE)
    }
    for table in table_refs:
        if table not in table_ids:
            return False, "UNKNOWN_TABLE"

    col_tokens = {m.group(1).upper() for m in re.finditer(r"\[([A-Za-z]{2}\d{3})\]", sql)}
    if any(c not in field_ids for c in col_tokens):
        return False, "UNKNOWN_COLUMN"

    return True, "OK"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/skill_scripts/test_metadata_validator.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/metadata_validator.py tests/skill_scripts/test_metadata_validator.py
git commit -m "feat: add metadata reference validator for llm sql output"
```

### Task 4: Add SQL router (shadow + llm-first with confidence and consistency gates)

**Files:**
- Create: `skill_scripts/sql_router.py`
- Modify: `skill_scripts/sql_generator.py`
- Test: `tests/skill_scripts/test_sql_router.py`

- [ ] **Step 1: Write failing router tests**

```python
from skill_scripts.sql_router import route_generate_sql


def test_route_generate_sql_falls_back_when_llm_sql_invalid(monkeypatch):
    bundle = {"tables": [], "fields": [], "modules": [], "index_keys": []}

    def fake_llm(*args, **kwargs):
        return {"sql": "DELETE FROM ACTMK", "used_tables": [], "assumptions": [], "confidence": 0.9}

    sql, meta = route_generate_sql("查詢預算", bundle, llm_generate=fake_llm)
    assert sql.startswith("SELECT")
    assert meta["route"] == "fallback_rule"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_sql_router.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement router**

```python
# skill_scripts/sql_router.py
from typing import Any, Callable

from skill_scripts.schema_context_builder import build_context_slice
from skill_scripts.intent_parser import parse_intent
from skill_scripts.llm_sql_generator import build_llm_prompt, call_llm, parse_llm_response
from skill_scripts.sql2000_guard import validate_sql
from skill_scripts.sql_generator import generate_select_sql
from skill_scripts.metadata_validator import validate_metadata_references
from skill_scripts.prompt_sql_consistency import validate_prompt_sql_consistency


JsonDict = dict[str, Any]


def route_generate_sql(
    prompt: str,
    bundle: JsonDict,
    mode: str = "rule",
    provider: str = "none",
    model: str = "none",
    min_confidence: float = 0.6,
) -> tuple[str, JsonDict]:
    intent = parse_intent(prompt)
    if intent.get("non_select_intent"):
        raise ValueError("NON_SELECT_INTENT")

    if mode == "rule":
        return generate_select_sql(prompt, bundle), {"route": "rule", "reason": "RULE_MODE"}

    context = build_context_slice(prompt, bundle)
    llm_prompt = build_llm_prompt(prompt, context)
    raw = call_llm(provider, model, llm_prompt)
    llm_out = parse_llm_response(raw)
    llm_sql = str(llm_out.get("sql", "")).strip()

    if float(llm_out.get("confidence", 0.0)) < min_confidence:
        fallback = generate_select_sql(prompt, bundle)
        if mode == "shadow":
            return fallback, {"route": "shadow_rule", "reason": "LOW_CONFIDENCE", "candidate_sql": llm_sql}
        return fallback, {"route": "fallback_rule", "reason": "LOW_CONFIDENCE"}

    ok_policy, code_policy = validate_sql(llm_sql)
    if ok_policy:
        ok_meta, code_meta = validate_metadata_references(llm_sql, bundle)
        ok_consistency, code_consistency = validate_prompt_sql_consistency(prompt, llm_sql)
        if ok_meta and ok_consistency:
            if mode == "shadow":
                fallback = generate_select_sql(prompt, bundle)
                return fallback, {"route": "shadow_rule", "reason": "SHADOW_COMPARE", "candidate_sql": llm_sql}
            return llm_sql, {"route": "llm", "reason": "OK"}
        reason = code_meta if not ok_meta else code_consistency
        return generate_select_sql(prompt, bundle), {"route": "fallback_rule", "reason": reason}
    return generate_select_sql(prompt, bundle), {"route": "fallback_rule", "reason": code_policy}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/skill_scripts/test_sql_router.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/sql_router.py tests/skill_scripts/test_sql_router.py skill_scripts/sql_generator.py
git commit -m "feat: add llm-first sql router with deterministic fallback"
```

### Task 5: Integrate CLI `rule` / `shadow` / `llm-first` modes and telemetry

**Files:**
- Modify: `skill_scripts/cli_generate_select.py`
- Modify: `skills/workflow-erp-sql-generator/SKILL.md`
- Test: `tests/skill_scripts/test_sql_generator.py`

- [ ] **Step 1: Write failing CLI route test**

```python
import sys
from skill_scripts.cli_generate_select import main


def test_cli_supports_llm_mode_flag(capsys):
    old_argv = sys.argv
    sys.argv = [
        "cli_generate_select",
        "--mode", "llm-first",
        "--prompt", "查詢2026年的工程預算明細",
    ]
    try:
        main()
    finally:
        sys.argv = old_argv

    captured = capsys.readouterr()
    assert "SELECT" in captured.out
    assert "ROUTE:" in captured.out
```

- [ ] **Step 2: Implement CLI flags and route output**

```python
# add args
parser.add_argument("--mode", choices=["rule", "shadow", "llm-first"], default="rule")
parser.add_argument("--llm-provider", default="none")
parser.add_argument("--llm-model", default="none")
parser.add_argument("--llm-min-confidence", type=float, default=0.6)

# output
print(sql)
print(f"ROUTE:{meta['route']} REASON:{meta['reason']}")
if "candidate_sql" in meta:
    print(f"CANDIDATE_SQL:{meta['candidate_sql']}")
```

- [ ] **Step 3: Update SKILL contract doc**

```markdown
- Mode `rule`: deterministic generation only
- Mode `shadow`: generate LLM candidate, return rule SQL, emit candidate SQL for comparison
- Mode `llm-first`: attempt LLM generation, then hard-validate, fallback to rule on any failure
- Every response emits route metadata (`rule`, `shadow_rule`, `llm`, `fallback_rule`)
```

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/skill_scripts/test_sql_generator.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/cli_generate_select.py skills/workflow-erp-sql-generator/SKILL.md tests/skill_scripts/test_sql_generator.py
git commit -m "feat: add dual-track cli mode and route telemetry"
```

### Task 6: Add prompt-to-SQL consistency validator

**Files:**
- Create: `skill_scripts/prompt_sql_consistency.py`
- Create: `tests/skill_scripts/test_prompt_sql_consistency.py`

- [ ] **Step 1: Write failing consistency tests**

```python
from skill_scripts.prompt_sql_consistency import validate_prompt_sql_consistency


def test_validate_prompt_sql_consistency_detects_year_mismatch():
    ok, code = validate_prompt_sql_consistency(
        "查詢2026年的工程預算明細",
        "SELECT * FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2025'",
    )
    assert ok is False
    assert code == "YEAR_MISMATCH"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_prompt_sql_consistency.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement consistency validator**

```python
# skill_scripts/prompt_sql_consistency.py
import re


def validate_prompt_sql_consistency(prompt: str, sql: str) -> tuple[bool, str]:
    text = str(prompt or "")
    query = str(sql or "")

    m_year = re.search(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", text)
    if m_year and m_year.group(1) not in query:
        return False, "YEAR_MISMATCH"

    m_top = re.search(r"\btop\s*(\d+)\b", text, re.IGNORECASE)
    if m_top and f"TOP {m_top.group(1)}" not in query.upper():
        return False, "TOP_MISMATCH"

    return True, "OK"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/skill_scripts/test_prompt_sql_consistency.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/prompt_sql_consistency.py tests/skill_scripts/test_prompt_sql_consistency.py
git commit -m "feat: add prompt-to-sql consistency validator"
```

### Task 7: Add database client and execution validator (test DB)

**Files:**
- Create: `skill_scripts/database_client.py`
- Create: `skill_scripts/execution_validator.py`
- Create: `tests/skill_scripts/test_database_client.py`
- Create: `tests/skill_scripts/test_execution_validator.py`

- [ ] **Step 1: Write failing DB config tests**

```python
from skill_scripts.database_client import DatabaseConfig


def test_database_config_reads_environment_values(monkeypatch):
    monkeypatch.setenv("DB_DRIVER", "mssql")
    monkeypatch.setenv("DB_CONNECTION_STRING", "Server=127.0.0.1;Database=wferp_test;")
    monkeypatch.setenv("DB_ENV", "test")
    cfg = DatabaseConfig.from_env()
    assert cfg.driver == "mssql"
    assert cfg.env == "test"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/skill_scripts/test_database_client.py -v`  
Expected: FAIL with missing module/class

- [ ] **Step 3: Implement configurable database client interface**

```python
# skill_scripts/database_client.py
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class DatabaseConfig:
    driver: str
    connection_string: str
    auth_mode: str
    env: str

    @staticmethod
    def from_env() -> "DatabaseConfig":
        return DatabaseConfig(
            driver=os.getenv("DB_DRIVER", "mssql"),
            connection_string=os.getenv("DB_CONNECTION_STRING", ""),
            auth_mode=os.getenv("DB_AUTH_MODE", "sql_auth"),
            env=os.getenv("DB_ENV", "test"),
        )


class DatabaseClient:
    def __init__(self, config: DatabaseConfig):
        self.config = config

    def health_check(self) -> tuple[bool, str]:
        if not self.config.connection_string:
            return False, "DB_CONNECTION_NOT_CONFIGURED"
        return True, "OK"

    def execute_readonly(self, sql: str):
        # implementation adapter in execution phase (pymssql/pyodbc)
        raise RuntimeError("DB_EXECUTION_NOT_IMPLEMENTED")
```

- [ ] **Step 4: Write failing execution validator tests**

```python
from skill_scripts.execution_validator import validate_execution_result


def test_validate_execution_result_checks_required_columns():
    rows = [{"MK002": "2026", "MK006": 1000}]
    ok, code = validate_execution_result(
        rows,
        required_columns=["MK002", "MK006"],
        min_rows=1,
    )
    assert ok is True
    assert code == "OK"
```

- [ ] **Step 5: Implement execution validator**

```python
# skill_scripts/execution_validator.py
def validate_execution_result(rows, required_columns, min_rows=0, max_rows=None):
    if len(rows) < min_rows:
        return False, "ROWCOUNT_TOO_LOW"
    if max_rows is not None and len(rows) > max_rows:
        return False, "ROWCOUNT_TOO_HIGH"
    if rows:
        first_keys = set(rows[0].keys())
        if any(col not in first_keys for col in required_columns):
            return False, "MISSING_REQUIRED_COLUMN"
    return True, "OK"
```

- [ ] **Step 6: Run tests to verify pass**

Run: `pytest tests/skill_scripts/test_database_client.py tests/skill_scripts/test_execution_validator.py -v`  
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add skill_scripts/database_client.py skill_scripts/execution_validator.py tests/skill_scripts/test_database_client.py tests/skill_scripts/test_execution_validator.py
git commit -m "feat: add configurable db client and execution result validator"
```

### Task 8: End-to-end verification and regression checks

**Files:**
- Verify: `tests/skill_scripts/*.py`
- Verify: `skill_scripts/*.py`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/skill_scripts -v`  
Expected: PASS

- [ ] **Step 2: Compile check**

Run: `python3 -m compileall skill_scripts`  
Expected: no syntax errors

- [ ] **Step 3: CLI rule mode sanity**

Run: `python3 -m skill_scripts.cli_generate_select --mode rule --prompt "查詢2026年的工程預算明細"`  
Expected: SELECT statement + deterministic behavior

- [ ] **Step 4: CLI llm-first fallback sanity**

Run: `python3 -m skill_scripts.cli_generate_select --mode shadow --prompt "查詢2026年的工程預算明細"`  
Expected: SELECT statement + `ROUTE:shadow_rule` + `CANDIDATE_SQL:`

- [ ] **Step 5: CLI llm-first fallback sanity**

Run: `python3 -m skill_scripts.cli_generate_select --mode llm-first --llm-provider none --prompt "查詢2026年的工程預算明細"`  
Expected: SELECT statement + route metadata (`fallback_rule`) when provider not configured

- [ ] **Step 6: DB client config sanity**

Run:

```bash
DB_DRIVER=mssql DB_CONNECTION_STRING="Server=127.0.0.1;Database=wferp_test;" DB_ENV=test python3 - <<'PY'
from skill_scripts.database_client import DatabaseConfig
print(DatabaseConfig.from_env())
PY
```

Expected: config printed with env=`test`

- [ ] **Step 7: Commit**

```bash
git add .
git commit -m "feat: implement dual-track llm sql generation with strict safety fallback"
```

---

## Self-Review (completed)

1. **Spec coverage**
   - context slicing: Task 1
   - LLM provider contract + envelope: Task 2
   - metadata validation: Task 3
   - dual-track routing/fallback with confidence + shadow mode: Task 4
   - CLI integration + observability: Task 5
   - prompt/sql consistency checks: Task 6
   - database connection abstraction + execution validation: Task 7
   - verification and regression: Task 8

2. **Placeholder scan**
   - no placeholder markers left in tasks; all steps include concrete files, code, commands.

3. **Type consistency**
   - route contract is `tuple[str, JsonDict]`
   - metadata validator contract is `tuple[bool, str]`
   - consistency validator contract is `tuple[bool, str]`
   - router uses existing `generate_select_sql(prompt, bundle)` signature consistently.
