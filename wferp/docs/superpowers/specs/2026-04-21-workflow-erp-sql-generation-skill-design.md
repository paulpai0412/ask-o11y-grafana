# Workflow ERP NL-to-SQL Skill Design (SQL Server 2000, SELECT-only)

Date: 2026-04-21  
Project: `wferp/schema`  
Target: Build a skill that converts natural language prompts into Microsoft SQL Server 2000 compatible SQL, limited to `SELECT` queries only.

## 1) Problem Statement

We need a dedicated skill for 鼎新 Workflow ERP that:

1. Understands table schemas from repository metadata.
2. Understands table relationships (PK, index keys, inferred FK-like links).
3. Distinguishes relationship cardinality (1:1, 1:N, N:N) where inferable.
4. Builds a data dictionary to locate all tables containing a given field.
5. Generates SQL Server 2000 compatible SQL from natural language prompts.
6. Enforces a strict `SELECT`-only policy (no `CREATE`, `UPDATE`, `DELETE`, `ALTER`, etc.).
7. Includes supporting scripts for fast SQL generation.

## 2) Scope and Non-Goals

### In Scope

- Parse and index these source files:
  - `_Source/TableStructure.json`
  - `_Source/TableName.json`
  - `_Source/TableIndexKey.json`
  - `_Source/MoudleName.json` (for module semantics)
- Build normalized metadata indexes for table/field/key lookup.
- Infer join paths and cardinality heuristics.
- Generate SQL Server 2000 compatible `SELECT` statements only.
- Reject unsafe or unsupported intents and syntax.

### Out of Scope (v1)

- DML/DDL SQL generation.
- Auto-executing SQL against a database.
- Full foreign-key certainty when DSCSYS metadata does not explicitly define FK constraints.
- SQL Server 2005+ features.

## 3) Design Approach

Chosen approach: **Hybrid design**

- **Rule-driven metadata/join planning** for correctness and controllability.
- **Prompt-to-structure parser** for user intent extraction.
- **Strict validator gate** for SQL2000 compatibility and `SELECT`-only enforcement.

Reasoning:

- Pure free-form generation is risky for legacy SQL compatibility.
- Pure template-only generation is too rigid for natural language usage.
- Hybrid gives high control while preserving query flexibility.

## 4) Source Data Model

### 4.1 Table/Field Metadata

From `TableStructure.json`, build:

- `table -> [fields]`
- `field -> {type, length, description, module, multilingual aliases}`
- `table.field -> canonical metadata`

### 4.2 Key Metadata

From `TableIndexKey.json`, build:

- `table -> primary_key_columns`
- `table -> index_key_sets`
- composite key map for path scoring

### 4.3 Table Identity Metadata

From `TableName.json` and `MoudleName.json`, build:

- module-to-table map
- table aliases (Chinese/English/Vietnamese/user synonyms)
- qualified name map (`DB.Schema.Table` style normalization)

## 5) Relationship and Cardinality Inference

Because explicit FK constraints may be incomplete, infer relationships with confidence levels.

### 5.1 Candidate Link Rules

Generate candidate links when one or more rules match:

1. **Exact key-name + datatype/length match** between child columns and parent PK or unique index.
2. **Composite key subset/superset compatibility**.
3. **Common ERP naming motifs** (e.g., document header/detail key conventions).
4. **Description-based alias matching** (lower confidence).

### 5.2 Confidence Scoring

- High: exact match to parent PK or unique index with aligned types.
- Medium: index-based or composite compatibility with partial certainty.
- Low: name/description heuristic only.

Low-confidence joins are **not auto-used** in v1. The generator must return `NO_RELATION_PATH` (or `AMBIGUOUS_FIELD`) unless a high/medium-confidence join path exists.

### 5.3 Cardinality Heuristics

- **1:1**: child key uniquely constrained and maps to one parent key.
- **1:N**: child rows reference parent key without child uniqueness.
- **N:N**: bridge table with two FK-like key groups and composite uniqueness.

When confidence is insufficient, mark as `unknown` and return a structured ambiguity error instead of auto-joining.

## 6) Data Dictionary Design

Build two-way indexes:

- `field_name -> [table.field ...]`
- `normalized_alias -> [table.field ...]`
- `table -> {field list, keys, inferred relations}`

Use cases:

- Data consistency checks.
- Prompt disambiguation (same field appearing in multiple tables).
- Faster candidate selection during SQL generation.

## 7) NL-to-SQL Pipeline

1. **Intent Parse**
   - Extract metrics, dimensions, filters, sort, top-N, group-by signals.
2. **Entity Resolution**
   - Map natural language terms to table/field candidates via dictionary and aliases.
3. **Join Planning**
   - Build minimal-cost join path from relationship graph.
4. **Query Assembly**
   - Emit canonical SQL2000 `SELECT ... FROM ... JOIN ... WHERE ... GROUP BY ... HAVING ... ORDER BY`.
5. **Safety & Compatibility Validation**
   - Ensure `SELECT`-only and SQL2000 compatibility.
6. **Output**
   - Return SQL plus optional rationale (selected tables, join basis, confidence).

## 8) SQL Server 2000 Compatibility Guardrails

### 8.1 Hard Forbid List

Reject generated SQL containing modern or non-supported constructs, including:

- `WITH` (CTE usage)
- `OVER`, `PARTITION BY`, `ROW_NUMBER`, `RANK`, `DENSE_RANK`
- `OFFSET`, `FETCH`
- `EXCEPT`, `INTERSECT`

### 8.2 SQL2000-Compatible Constraints

- Use `TOP n` style (no modern paging syntax).
- Prefer ANSI joins (`INNER/LEFT/RIGHT/FULL`) for clarity.
- Use bracketed identifiers when needed (`[Table]`, `[Column]`).
- Avoid syntax/forms introduced in later SQL Server versions.

## 9) SELECT-only Safety Policy

### 9.1 Intent-Level Policy

If user asks for data modification or schema changes, return refusal with guidance.

### 9.2 SQL-Level Policy

Reject if final SQL contains any write/DDL/control tokens (case-insensitive), including:

- `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`
- `CREATE`, `ALTER`, `DROP`, `RENAME`
- `GRANT`, `REVOKE`, `EXEC`, `EXECUTE`

### 9.3 Multi-Statement Policy

- v1 default: single statement only.
- Reject batches with `;` followed by additional statements.

## 10) Required Scripts (Performance and Maintainability)

Add scripts under a dedicated `skill_scripts/` folder at repo root (explicit path choice for v1):

1. `build_schema_index.py`
   - Parse JSON metadata and emit normalized index artifacts.
2. `build_relationship_graph.py`
   - Infer FK-like links and cardinality with confidence scores.
3. `build_data_dictionary.py`
   - Emit field-centric reverse index and alias index.
4. `validate_sql2000_select_only.py`
   - Enforce SQL2000 + SELECT-only constraints.
5. `generate_select_sql.py`
   - Consume parsed intent + indexes and emit final SQL.

Optimization tactics:

- Precompute and cache graph/index files.
- Use normalized lowercase token maps for fast lookup.
- Keep per-query runtime mostly in intent parse + join path scoring.

## 11) Error Handling and User Feedback

Return structured errors:

- `AMBIGUOUS_FIELD`: field exists in multiple tables; ask for module/table hint.
- `NO_RELATION_PATH`: cannot safely infer join path.
- `UNSUPPORTED_SQL2000_FEATURE`: request implies unsupported legacy syntax pattern.
- `NON_SELECT_INTENT`: request asks for forbidden write/DDL behavior.

## 12) Validation and Acceptance Criteria

### Functional

- Natural language prompts can resolve to valid `SELECT` SQL for core ERP modules.
- Field-to-table reverse lookup works for data dictionary use cases.
- Relationship/cardinality metadata is queryable by downstream generator logic.

### Safety

- 100% of generated SQL passes `SELECT`-only guard.
- SQL2000 incompatible syntax is rejected pre-output.

### Quality

- Join plans include confidence metadata.
- Ambiguous mappings produce explicit clarification messages.

## 13) Risks and Mitigations

1. **Implicit FK uncertainty**
   - Mitigation: confidence scoring + explicit ambiguity handling.
2. **Schema naming variance**
   - Mitigation: alias dictionary with multilingual terms.
3. **Legacy syntax edge cases**
   - Mitigation: strict validator and compatibility test corpus.

## 14) Implementation Readiness

This design is ready for implementation planning.

Planned next phase:

- Decompose into executable tasks (index builder, relationship inference, validator, generator, test corpus).
- Define interfaces and artifact formats for each script.
- Sequence implementation with incremental verification.
