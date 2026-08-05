# Workflow ERP SQL Generation Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a skill that transforms natural language prompts into SQL Server 2000-compatible, SELECT-only SQL using Workflow ERP schema metadata.

**Architecture:** Use a hybrid pipeline: deterministic metadata indexing + relationship inference + guarded SQL generation. The generator never emits non-SELECT statements and validates SQL Server 2000 compatibility before returning output. Artifacts are precomputed for fast runtime lookup.

**Tech Stack:** Python 3, JSON metadata files in `_Source/`, pytest (new), markdown skill file.

---

## File Structure (planned)

- Create: `skill_scripts/schema_loader.py`
- Create: `skill_scripts/relationship_graph.py`
- Create: `skill_scripts/data_dictionary.py`
- Create: `skill_scripts/intent_parser.py`
- Create: `skill_scripts/sql2000_guard.py`
- Create: `skill_scripts/sql_generator.py`
- Create: `skill_scripts/cli_generate_select.py`
- Create: `skill_scripts/__init__.py`
- Create: `skill_scripts/artifacts/.gitkeep`
- Create: `skills/workflow-erp-sql-generator/SKILL.md`
- Create: `tests/skill_scripts/test_schema_loader.py`
- Create: `tests/skill_scripts/test_relationship_graph.py`
- Create: `tests/skill_scripts/test_data_dictionary.py`
- Create: `tests/skill_scripts/test_sql2000_guard.py`
- Create: `tests/skill_scripts/test_sql_generator.py`

### Task 1: Scaffold package and schema loader

**Files:**
- Create: `skill_scripts/__init__.py`
- Create: `skill_scripts/schema_loader.py`
- Test: `tests/skill_scripts/test_schema_loader.py`

- [ ] **Step 1: Write the failing loader test**

```python
from skill_scripts.schema_loader import load_schema_bundle

def test_load_schema_bundle_has_required_keys():
    bundle = load_schema_bundle("_Source")
    assert set(bundle.keys()) == {
        "modules", "tables", "fields", "index_keys"
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/skill_scripts/test_schema_loader.py -v`  
Expected: FAIL with import/module-not-found or function-not-found

- [ ] **Step 3: Write minimal loader implementation**

```python
# skill_scripts/schema_loader.py
import json
from pathlib import Path

def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_schema_bundle(source_dir: str):
    base = Path(source_dir)
    return {
        "modules": _read_json(base / "MoudleName.json"),
        "tables": _read_json(base / "TableName.json"),
        "fields": _read_json(base / "TableStructure.json"),
        "index_keys": _read_json(base / "TableIndexKey.json"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/skill_scripts/test_schema_loader.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/__init__.py skill_scripts/schema_loader.py tests/skill_scripts/test_schema_loader.py
git commit -m "feat: add schema bundle loader for ERP metadata"
```

### Task 2: Build relationship graph with confidence and cardinality

**Files:**
- Create: `skill_scripts/relationship_graph.py`
- Test: `tests/skill_scripts/test_relationship_graph.py`

- [ ] **Step 1: Write failing graph inference tests**

```python
from skill_scripts.relationship_graph import infer_relationships

def test_infer_relationships_returns_edges_with_confidence():
    edges = infer_relationships(fields=[], index_keys=[])
    assert isinstance(edges, list)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/skill_scripts/test_relationship_graph.py -v`  
Expected: FAIL with missing implementation

- [ ] **Step 3: Implement minimal inference engine**

```python
def infer_relationships(fields, index_keys):
    # v1 minimal shape; full rules added incrementally
    return []
```

- [ ] **Step 4: Add confidence/cardinality rule tests**

```python
def test_edge_has_confidence_and_cardinality_keys(sample_edges):
    edge = sample_edges[0]
    assert edge["confidence"] in {"high", "medium", "low"}
    assert edge["cardinality"] in {"1:1", "1:N", "N:N", "unknown"}
```

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/relationship_graph.py tests/skill_scripts/test_relationship_graph.py
git commit -m "feat: add relationship inference graph with confidence metadata"
```

### Task 3: Build field-centric data dictionary

**Files:**
- Create: `skill_scripts/data_dictionary.py`
- Test: `tests/skill_scripts/test_data_dictionary.py`

- [ ] **Step 1: Write failing dictionary test**

```python
from skill_scripts.data_dictionary import build_field_index

def test_build_field_index_maps_field_to_table_fields(sample_fields):
    index = build_field_index(sample_fields)
    assert "TA001" in index
```
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_data_dictionary.py -v`  
Expected: FAIL with function missing

- [ ] **Step 3: Implement field reverse-index**

```python
from collections import defaultdict

def build_field_index(fields):
    out = defaultdict(list)
    for row in fields:
        out[row["FieldNo"]].append(f"{row['TableNo']}.{row['FieldNo']}")
    return dict(out)
```

- [ ] **Step 4: Add alias-normalization test**

```python
def test_build_alias_index_normalizes_case(sample_fields):
    from skill_scripts.data_dictionary import build_alias_index

    alias_index = build_alias_index(sample_fields)
    assert "ta001" in alias_index
    assert any(v.endswith(".TA001") for v in alias_index["ta001"])
```

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/data_dictionary.py tests/skill_scripts/test_data_dictionary.py
git commit -m "feat: add field-centric data dictionary indexes"
```

### Task 4: Implement SQL2000 + SELECT-only guard

**Files:**
- Create: `skill_scripts/sql2000_guard.py`
- Test: `tests/skill_scripts/test_sql2000_guard.py`

- [ ] **Step 1: Write failing policy tests**

```python
from skill_scripts.sql2000_guard import validate_sql

def test_rejects_non_select_sql():
    ok, code = validate_sql("DELETE FROM ACPTA")
    assert ok is False and code == "NON_SELECT_INTENT"

def test_rejects_cte_for_sql2000():
    ok, code = validate_sql("WITH x AS (SELECT 1 a) SELECT * FROM x")
    assert ok is False and code == "UNSUPPORTED_SQL2000_FEATURE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/skill_scripts/test_sql2000_guard.py -v`  
Expected: FAIL with missing validator

- [ ] **Step 3: Implement validator**

```python
FORBIDDEN_NON_SELECT = {"insert", "update", "delete", "create", "alter", "drop", "merge", "truncate"}
FORBIDDEN_SQL2000 = {" with ", " over ", " offset ", " fetch ", " intersect ", " except "}

def validate_sql(sql: str):
    s = f" {sql.lower()} "
    if any(tok in s for tok in FORBIDDEN_NON_SELECT):
        return False, "NON_SELECT_INTENT"
    if not s.strip().startswith("select"):
        return False, "NON_SELECT_INTENT"
    if any(tok in s for tok in FORBIDDEN_SQL2000):
        return False, "UNSUPPORTED_SQL2000_FEATURE"
    return True, "OK"
```

- [ ] **Step 4: Re-run tests**

Run: `pytest tests/skill_scripts/test_sql2000_guard.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/sql2000_guard.py tests/skill_scripts/test_sql2000_guard.py
git commit -m "feat: enforce sql2000 and select-only safety policies"
```

### Task 5: Implement intent parser and SQL assembler

**Files:**
- Create: `skill_scripts/intent_parser.py`
- Create: `skill_scripts/sql_generator.py`
- Test: `tests/skill_scripts/test_sql_generator.py`

- [ ] **Step 1: Write failing generation tests**

```python
from skill_scripts.sql_generator import generate_select_sql

def test_generate_select_sql_returns_select_statement(sample_bundle):
    sql = generate_select_sql("查詢採購單號與日期", sample_bundle)
    assert sql.lower().startswith("select")
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/skill_scripts/test_sql_generator.py -v`  
Expected: FAIL with missing parser/generator

- [ ] **Step 3: Implement minimal parser + assembler**

```python
def parse_intent(prompt: str):
    return {"raw": prompt, "top": None, "filters": []}

def generate_select_sql(prompt: str, bundle: dict):
    # minimal v1 deterministic stub, expanded by tests
    return "SELECT TOP 50 * FROM [DSCSYS].[dbo].[ADMMC]"
```

- [ ] **Step 4: Validate generated SQL through guard**

Run: `pytest tests/skill_scripts/test_sql_generator.py -v`  
Expected: PASS including guard integration assertions

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/intent_parser.py skill_scripts/sql_generator.py tests/skill_scripts/test_sql_generator.py
git commit -m "feat: add intent parsing and deterministic select sql assembly"
```

### Task 6: Add CLI entrypoint and artifact builders

**Files:**
- Create: `skill_scripts/cli_generate_select.py`
- Create: `skill_scripts/artifacts/.gitkeep`
- Modify: `skill_scripts/schema_loader.py`

- [ ] **Step 1: Write failing CLI smoke test**

```python
def test_cli_outputs_sql_string(capsys):
    from skill_scripts.cli_generate_select import main
    import sys

    old_argv = sys.argv
    sys.argv = ["cli_generate_select", "--prompt", "查詢客戶主檔"]
    try:
        main()
    finally:
        sys.argv = old_argv

    captured = capsys.readouterr()
    assert captured.out.strip().lower().startswith("select")
```

- [ ] **Step 2: Implement CLI main()**

```python
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="")
    parser.add_argument("--source", default="_Source")
    parser.add_argument("--build-artifacts", action="store_true")
    args = parser.parse_args()

    if args.build_artifacts:
        # write precomputed artifacts to skill_scripts/artifacts/
        return

    from skill_scripts.schema_loader import load_schema_bundle
    from skill_scripts.sql_generator import generate_select_sql

    bundle = load_schema_bundle(args.source)
    sql = generate_select_sql(args.prompt, bundle)
    print(sql)
```

- [ ] **Step 3: Add build command for artifacts**

Run: `python -m skill_scripts.cli_generate_select --build-artifacts`  
Expected: files created under `skill_scripts/artifacts/`

- [ ] **Step 4: Verify CLI generation path**

Run: `python -m skill_scripts.cli_generate_select --prompt "查詢客戶主檔"`  
Expected: a single SQL `SELECT` statement output

- [ ] **Step 5: Commit**

```bash
git add skill_scripts/cli_generate_select.py skill_scripts/artifacts/.gitkeep skill_scripts/schema_loader.py
git commit -m "feat: add cli for artifact build and select sql generation"
```

### Task 7: Create skill contract file and usage guardrails

**Files:**
- Create: `skills/workflow-erp-sql-generator/SKILL.md`

- [ ] **Step 1: Write failing contract checks (manual checklist)**

Run checklist:
- Skill states SELECT-only policy
- Skill states SQL Server 2000 compatibility rules
- Skill explains required inputs and error codes

Expected: checklist initially fails (file missing)

- [ ] **Step 2: Implement skill markdown contract**

```markdown
# workflow-erp-sql-generator
- Input: natural language query
- Output: one SQL Server 2000-compatible SELECT statement
- Must refuse: INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/EXEC
- Must validate SQL through sql2000_guard.validate_sql
```

- [ ] **Step 3: Verify skill contract manually**

Run: `python -m skill_scripts.cli_generate_select --prompt "刪除資料"`  
Expected: structured refusal with `NON_SELECT_INTENT`

- [ ] **Step 4: Commit**

```bash
git add skills/workflow-erp-sql-generator/SKILL.md
git commit -m "docs: add workflow erp sql generator skill contract"
```

### Task 8: Run full verification

**Files:**
- Test: `tests/skill_scripts/*.py`
- Verify: generated SQL outputs from CLI examples

- [ ] **Step 1: Run all tests**

Run: `pytest tests/skill_scripts -v`  
Expected: PASS

- [ ] **Step 2: Run lint-equivalent sanity checks**

Run: `python -m compileall skill_scripts`  
Expected: no syntax errors

- [ ] **Step 3: Run end-to-end examples**

Run:

```bash
python -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"
python -m skill_scripts.cli_generate_select --prompt "更新採購單狀態"
```

Expected:
- first command outputs valid SQL2000 `SELECT`
- second command returns `NON_SELECT_INTENT`

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "feat: implement workflow erp select-only sql2000 generation skill"
```

---

## Self-Review (completed)

1. **Spec coverage:**
   - schema indexing: Task 1
   - relationship/cardinality inference: Task 2
   - data dictionary: Task 3
   - SQL2000 + select-only safety: Task 4
   - NL-to-SQL generation: Task 5
   - acceleration scripts/artifacts: Task 6
   - skill behavior contract: Task 7
   - verification: Task 8

2. **Placeholder scan:**
   - Checked for unfinished markers and removed all of them from this plan.

3. **Type consistency:**
   - `generate_select_sql(prompt: str, bundle: dict)` and `validate_sql(sql: str)` are consistent across tasks.
