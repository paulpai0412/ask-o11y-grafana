# Modes and Routing

## Default

- Default mode is `--mode llm-first`.

## `--mode rule`

- Deterministic rule generator only.
- No LLM call.

## `--mode shadow`

- Generate an LLM candidate for comparison.
- Return rule SQL as final output.
- Include reason for shadow evaluation; candidate SQL is included when an LLM candidate was actually produced.

## `--mode llm-first`

- Do not use rule fallback as final output.
- Validate LLM candidate with policy guard + metadata + prompt/SQL consistency.
- If validation/execution fails, ask LLM to repair and retry until attempts are exhausted.
- On repeated failure, raise final repair failure.
