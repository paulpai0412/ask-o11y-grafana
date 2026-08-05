from dataclasses import dataclass
from typing import Any

from skill_scripts.execution_validator import ExecutionExpectation, execute_and_validate
from skill_scripts.intent_parser import parse_intent
from skill_scripts.llm_sql_generator import build_llm_prompt, call_llm, parse_llm_response
from skill_scripts.metadata_validator import validate_metadata_references
from skill_scripts.prompt_sql_consistency import validate_prompt_sql_consistency
from skill_scripts.schema_context_builder import build_context_slice
from skill_scripts.sql2000_guard import validate_sql
from skill_scripts.sql_generator import generate_select_sql


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class RoutingOptions:
    mode: str = "llm-first"
    llm_provider: str = "opencode"
    llm_model: str = "none"
    llm_timeout_sec: float = 30.0
    min_confidence: float = 0.6
    llm_repair_attempts: int = 2
    validate_execution: bool = False
    execution_expectation: ExecutionExpectation | None = None
    allow_non_test_execution: bool = False


def _fallback(prompt: str, bundle: JsonDict, reason: str, route: str = "fallback_rule", candidate_sql: str = "") -> tuple[str, JsonDict]:
    sql = generate_select_sql(prompt, bundle)
    meta: JsonDict = {"route": route, "reason": reason}
    if candidate_sql:
        meta["candidate_sql"] = candidate_sql
    return sql, meta


def _validate_llm_candidate(
    prompt: str,
    sql: str,
    bundle: JsonDict,
    db_client,
    options: RoutingOptions,
) -> tuple[bool, str]:
    ok_policy, code_policy = validate_sql(sql)
    if not ok_policy:
        return False, code_policy

    ok_meta, code_meta = validate_metadata_references(sql, bundle)
    if not ok_meta:
        return False, code_meta

    ok_consistency, code_consistency = validate_prompt_sql_consistency(prompt, sql)
    if not ok_consistency:
        return False, code_consistency

    if options.validate_execution and db_client is not None:
        expectation = options.execution_expectation or ExecutionExpectation()
        try:
            ok_exec, code_exec, _ = execute_and_validate(sql, db_client, expectation)
        except Exception:
            return False, "EXECUTION_VALIDATION_ERROR"
        if not ok_exec:
            return False, code_exec

    if options.validate_execution and db_client is None:
        return False, "DB_CLIENT_REQUIRED"

    return True, "OK"


def _build_repair_prompt(user_prompt: str, context: JsonDict, failed_sql: str, failed_reason: str) -> str:
    base = build_llm_prompt(user_prompt, context)
    if not failed_sql:
        return base
    return (
        f"{base}\n"
        "Previous SQL candidate failed validation/execution. "
        f"Failure code: {failed_reason}.\n"
        f"Previous SQL: {failed_sql}\n"
        "Rewrite SQL to fix the failure and return JSON only with keys: sql, used_tables, assumptions, confidence."
    )


def route_generate_sql(
    prompt: str,
    bundle: JsonDict,
    options: RoutingOptions,
    db_client=None,
) -> tuple[str, JsonDict]:
    intent = parse_intent(prompt)
    if intent.get("non_select_intent"):
        raise ValueError("NON_SELECT_INTENT")

    mode = str(options.mode or "rule").strip().lower()
    if mode == "rule":
        return generate_select_sql(prompt, bundle), {"route": "rule", "reason": "RULE_MODE"}

    if options.validate_execution and db_client is not None and not options.allow_non_test_execution:
        config = getattr(db_client, "config", None)
        env = str(getattr(config, "env", "")).strip().lower() if config is not None else ""
        if env != "test":
            if mode == "shadow":
                return _fallback(prompt, bundle, reason="DB_ENV_NOT_TEST", route="shadow_rule", candidate_sql="")
            raise RuntimeError("DB_ENV_NOT_TEST")

    if options.validate_execution and db_client is not None and hasattr(db_client, "health_check"):
        ok_health, code_health = db_client.health_check()
        if not ok_health:
            if mode == "shadow":
                return _fallback(prompt, bundle, reason=code_health, route="shadow_rule", candidate_sql="")
            raise RuntimeError(str(code_health))

    context = build_context_slice(prompt, bundle)
    failed_reason = ""
    failed_sql = ""
    attempts = max(1, int(options.llm_repair_attempts) + 1)

    for attempt in range(attempts):
        llm_prompt = _build_repair_prompt(prompt, context, failed_sql=failed_sql, failed_reason=failed_reason)

        try:
            raw_response = call_llm(
                provider=options.llm_provider,
                model=options.llm_model,
                prompt_text=llm_prompt,
                timeout_sec=options.llm_timeout_sec,
            )
            llm_out = parse_llm_response(raw_response)
        except RuntimeError as exc:
            reason = str(exc)
            if mode == "shadow":
                return _fallback(prompt, bundle, reason=reason, route="shadow_rule", candidate_sql="")
            if attempt >= attempts - 1:
                raise
            failed_reason = reason
            failed_sql = ""
            continue

        candidate_sql = str(llm_out.get("sql", "")).strip()
        confidence = float(llm_out.get("confidence", 0.0))

        ok = False
        reason = "LOW_CONFIDENCE"
        if confidence >= options.min_confidence:
            ok, reason = _validate_llm_candidate(
                prompt=prompt,
                sql=candidate_sql,
                bundle=bundle,
                db_client=db_client,
                options=options,
            )

        if mode == "shadow":
            return _fallback(
                prompt,
                bundle,
                reason="SHADOW_COMPARE" if ok else reason,
                route="shadow_rule",
                candidate_sql=candidate_sql,
            )

        if ok:
            return candidate_sql, {"route": "llm", "reason": "OK"}

        failed_reason = reason
        failed_sql = candidate_sql

    raise RuntimeError(f"LLM_REPAIR_FAILED:{failed_reason or 'UNKNOWN_ERROR'}")
