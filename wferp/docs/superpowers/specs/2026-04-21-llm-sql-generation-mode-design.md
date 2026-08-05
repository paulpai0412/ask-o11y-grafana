# LLM SQL Generation Mode Design (Workflow ERP, SQL Server 2000, SELECT-only)

Date: 2026-04-21  
Project: `wferp/schema`  
Topic: Add an LLM-based SQL generation mode while preserving strict safety and compatibility controls, with execution validation on a test database.

## 1) Background and Current State

Current implementation is deterministic, rule-based SQL generation:

- `skill_scripts/intent_parser.py` parses basic intents (`TOP`, year, non-select keywords).
- `skill_scripts/sql_generator.py` selects one table via scoring and emits template SQL.
- `skill_scripts/sql2000_guard.py` enforces SQL2000 + SELECT-only guardrails.

Strengths:

- predictable output
- high safety control
- easy reproducibility

Gaps:

- weak at complex natural language mapping
- limited multi-table query synthesis
- requires frequent rule additions for new user phrasing

## 2) Goal

Introduce an LLM generation capability to improve semantic understanding and SQL synthesis quality, while keeping:

1. SQL Server 2000 compatibility
2. SELECT-only policy
3. deterministic safety checks
4. debuggable, auditable behavior
5. executable correctness verification in a test DB environment

## 3) Options and Trade-offs

### Option A: Full LLM Replacement

LLM generates all SQL, then guard validates.

Pros:

- fastest path to richer query language support
- best semantic flexibility

Cons:

- higher operational risk if prompts/context are weak
- lower predictability
- harder regression control

### Option B: Dual-Track (Recommended)

Primary path = LLM generation; fallback path = current rule generator when confidence/safety checks fail.

Pros:

- best balance of capability and safety
- graceful degradation
- enables staged rollout and A/B measurement

Cons:

- more moving parts
- requires routing logic and telemetry

### Option C: Targeted LLM Mode

Use LLM only for complex requests (joins/aggregations/multi-constraint), keep simple requests on rules.

Pros:

- lower risk than full replacement
- preserves deterministic behavior for easy prompts

Cons:

- classifier/routing complexity
- ambiguous boundary between simple and complex requests

## 4) Chosen Direction

Adopt **Option B (Dual-Track)** as v2 baseline.

LLM generation mode is explicitly **single-pass SQL generation** (not two-stage LLM).

Decision rationale:

- preserves existing safety guarantees
- enables incremental migration with measurable quality gain
- minimizes rollback risk

## 5) Architecture

### 5.1 Components

1. **Schema Context Builder**
   - builds compact LLM context from artifacts (`schema_bundle`, key maps, data dictionary)
   - includes only relevant subset to control token cost and reduce hallucination

2. **LLM SQL Generator**
   - prompt-in / SQL-out component
   - instructed to output single SQL statement only
   - constrained to SQL Server 2000 compatibility profile
   - uses explicit provider adapter contract (provider/model/timeout/error semantics)
   - single-pass generation: one model call produces the final SQL candidate

2.1 **Data Dictionary Retrieval Layer**
   - when prompt contains expected output fields, use dictionary reverse index (`field -> table.field list`) to retrieve candidate tables/columns
   - expand candidates via relationship graph to include joinable companion tables
   - pass retrieved candidates and relationships into LLM context

3. **SQL Safety Gate (existing + extended)**
   - reuses `validate_sql`
   - adds semantic checks (table/column existence verification against metadata)

3.1 **Execution Validation Gate (new)**
   - executes candidate SQL against test database (SQL Server 2019, compatibility level 80)
   - confirms SQL is executable
   - validates result correctness against verification assertions (row-count/aggregates/expected columns)

4. **Fallback Rule Generator (existing)**
   - current deterministic `generate_select_sql` path

5. **Router**
   - chooses LLM-first path
   - supports `shadow` mode for side-by-side comparison before full cutover
   - fallback to rules on any validation failure
   - emits route reason codes for observability

6. **Explainability Log**
   - stores prompt fingerprint, chosen tables, guard result, fallback reason
   - no sensitive raw data persistence by default

### 5.2 High-Level Flow

1. Parse prompt intent (existing parser + complexity score) and short-circuit non-select requests.
2. Build minimal schema context slice for candidate domains.
3. If prompt includes output field requirements, retrieve candidate tables/columns/relations from data dictionary.
3. Call LLM to generate SQL draft.
4. Validate:
   - syntax policy: SELECT-only + SQL2000
   - metadata policy: referenced tables/columns exist
   - statement policy: single statement only
   - execution policy: SQL executes in test DB and passes result assertions
6. If pass and confidence >= threshold → return LLM SQL.
7. If fail → fallback to rule-based generator and return deterministic SQL.

## 6) Prompting Strategy for LLM

### 6.1 System Constraints (hard)

- You may output exactly one SQL statement.
- SQL must start with `SELECT`.
- Forbidden tokens: INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/MERGE/TRUNCATE/EXEC/EXECUTE.
- Forbidden SQL2000-incompatible features: WITH CTE, OVER/PARTITION, OFFSET/FETCH, EXCEPT/INTERSECT, window functions.
- Use bracketed identifiers (`[DB].[dbo].[Table]`, `[Column]`) when generated.

### 6.2 Context Payload

- candidate table list (top-k)
- candidate columns for each table
- key relationships (high/medium confidence only)
- mapping aliases from data dictionary

### 6.3 Output Contract

Machine-readable envelope:

```json
{
  "sql": "SELECT TOP 20 [TB005], [TB007] FROM [VPIC1].[dbo].[ACTTB]",
  "used_tables": ["ACTTA", "ACTTB"],
  "assumptions": ["Mapped natural-language time phrase to TA003 date filter"],
  "confidence": 0.0
}
```

If model cannot generate compliant SQL, it must return `NO_SQL` and reason.

Provider contract minimum fields:

- provider name
- model name
- timeout seconds
- deterministic error code mapping (`LLM_PROVIDER_NOT_CONFIGURED`, `LLM_TIMEOUT`, `LLM_BAD_RESPONSE`)

## 7) Verification Strategy

### 7.1 Automatic Validation Pipeline

1. `validate_sql` (existing): SELECT-only + SQL2000
2. metadata verifier (new): table/column existence check
3. prompt/sql consistency verifier: year/TOP/domain-alignment checks
4. execution verifier (new): run SQL in test DB and assert correctness

### 7.2 Prompt-to-SQL Consistency Checks

For each generated SQL, derive and compare constraints:

- year mentioned in prompt should appear in SQL where clause
- top-n mentioned in prompt should appear as `TOP n`
- requested domain keywords should map to expected table families

Any mismatch triggers fallback or explicit warning.

### 7.3 Execution-Based Correctness Validation (new baseline)

Each benchmark case includes:

- input prompt
- generated SQL
- expected output checks:
  - required columns set
  - minimum/maximum row count bounds (or exact row count when deterministic)
  - aggregate expectations (sum/count/min/max where applicable)

Validation passes only when:

1. SQL executes successfully in test DB
2. output schema matches expectation
3. output values satisfy assertion rules

### 7.4 Database Connectivity and Environment Promotion

Use pluggable DB client configuration so runtime can move from test to production by configuration only:

- `DB_DRIVER`
- `DB_CONNECTION_STRING`
- `DB_AUTH_MODE`
- `DB_ENV` (`test` or `prod`)
- optional read-only guard setting for production

Code uses a stable `DatabaseClient` interface (`health_check`, `execute_readonly`) and must not hardcode environment-specific connection logic.

### 7.5 Test Corpus

- fixed benchmark prompt set (at least 100 prompts)
- expected table-family and filter assertions
- expected execution assertions (columns, row bounds, aggregate checks)
- pass-rate metrics:
  - safety pass
  - semantic alignment pass
  - execution pass
  - fallback rate

## 8) Rollout Plan

### Phase 1: Shadow Mode

- generate both LLM SQL and rule SQL
- return rule SQL only
- compare semantic alignment offline and emit route logs

### Phase 2: Controlled LLM-First

- route low-risk domains to LLM-first
- auto-fallback on any failure

### Phase 3: Broader Enablement

- expand domain coverage
- continuously tune prompts/context slice

## 9) Risks and Mitigations

1. **Hallucinated tables/columns**
   - mitigation: strict metadata verifier + fallback

2. **Prompt injection / policy bypass**
   - mitigation: hard guard after generation (never trust model output directly)

3. **Cost/latency growth**
   - mitigation: schema context slicing, caching, and complexity-based routing

4. **Inconsistent outputs**
   - mitigation: deterministic prompt template and normalized decoding settings

5. **SQL syntactically valid but semantically wrong**
   - mitigation: execution-based result assertions in test DB before acceptance

## 10) Success Criteria

1. Zero non-select outputs returned to users.
2. Zero SQL2000-incompatible outputs returned to users.
3. Improved semantic match rate versus rule-only baseline.
4. Execution validation pass rate meets target threshold.
5. Fallback rate decreases over iterative tuning.

## 11) Scope Boundaries for This Change

In scope:

- LLM generation path + router + validators + fallback integration
- observability hooks for explainability and route reason
- test DB execution validation framework and seed dataset strategy
- environment-configurable DB connection abstraction

Out of scope:

- executing SQL against production DB
- write/DDL statement support
- replacing existing guardrails

## 12) Implementation Readiness

Design is ready to convert into an implementation plan.

User confirmation:

- User selected option **2**: **Dual-Track** architecture.
- User requested **single-pass LLM generation** (no two-stage LLM).
- User requested execution-based validation on test DB and config-driven connection promotion to production.
