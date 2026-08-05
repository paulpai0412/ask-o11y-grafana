from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from skill_scripts.execution_validator import ExecutionExpectation, execute_and_validate
from skill_scripts.metadata_validator import validate_metadata_references
from skill_scripts.prompt_sql_consistency import validate_prompt_sql_consistency
from skill_scripts.sql2000_guard import validate_sql
from skill_scripts.sql_router import RoutingOptions, route_generate_sql


@dataclass(frozen=True)
class QueryEvidence:
    prompt: str
    sql: str
    route: str
    route_reason: str
    validation_status: str
    execution_status: str
    execution_timestamp: str
    returned_columns: list[str] = field(default_factory=list)
    row_count: int = 0
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    required_columns: list[str] = field(default_factory=list)
    aggregate_checks: list[dict[str, Any]] = field(default_factory=list)
    candidate_sql: str = ""


@dataclass(frozen=True)
class GovernedQueryResult:
    evidence: QueryEvidence
    approval_state: str = "ready_for_analyst_approval"
    rejection_reason: str = ""


def _serialize_aggregate_checks(expectation: ExecutionExpectation) -> list[dict[str, Any]]:
    return [asdict(aggregate) for aggregate in expectation.aggregates]


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_safe_rows(rows: list[dict[str, Any]], sample_rows_limit: int) -> list[dict[str, Any]]:
    sample_limit = max(0, int(sample_rows_limit))
    return [{key: _json_safe_value(value) for key, value in row.items()} for row in rows[:sample_limit]]


def _build_evidence(
    prompt: str,
    sql: str,
    meta: dict[str, Any],
    expectation: ExecutionExpectation,
    rows: list[dict[str, Any]],
    sample_rows_limit: int,
) -> QueryEvidence:
    returned_columns = list(rows[0].keys()) if rows else []
    return QueryEvidence(
        prompt=prompt,
        sql=sql,
        route=str(meta.get("route", "")),
        route_reason=str(meta.get("reason", "")),
        validation_status="OK",
        execution_status="OK",
        execution_timestamp=datetime.now(timezone.utc).isoformat(),
        returned_columns=returned_columns,
        row_count=len(rows),
        sample_rows=_json_safe_rows(rows, sample_rows_limit),
        required_columns=list(expectation.required_columns),
        aggregate_checks=_serialize_aggregate_checks(expectation),
        candidate_sql=str(meta.get("candidate_sql", "")),
    )


def run_governed_query(
    prompt: str,
    bundle: dict[str, Any],
    options: RoutingOptions,
    db_client,
    sample_rows_limit: int = 5,
) -> GovernedQueryResult:
    if db_client is None:
        raise RuntimeError("DB_CLIENT_REQUIRED")

    config = getattr(db_client, "config", None)
    env = str(getattr(config, "env", "")).strip().lower() if config is not None else ""
    if env != "test" and not options.allow_non_test_execution:
        raise RuntimeError("DB_ENV_NOT_TEST")

    if hasattr(db_client, "health_check"):
        ok_health, code_health = db_client.health_check()
        if not ok_health:
            raise RuntimeError(str(code_health))

    governed_options = replace(options, validate_execution=False)
    sql, meta = route_generate_sql(
        prompt=prompt,
        bundle=bundle,
        options=governed_options,
        db_client=db_client,
    )

    ok_policy, code_policy = validate_sql(sql)
    if not ok_policy:
        raise RuntimeError(code_policy)

    ok_meta, code_meta = validate_metadata_references(sql, bundle)
    if not ok_meta:
        raise RuntimeError(code_meta)

    ok_consistency, code_consistency = validate_prompt_sql_consistency(prompt, sql)
    if not ok_consistency:
        raise RuntimeError(code_consistency)

    expectation = governed_options.execution_expectation or ExecutionExpectation()
    ok_exec, code_exec, rows = execute_and_validate(sql, db_client, expectation)
    if not ok_exec:
        raise RuntimeError(code_exec)

    evidence = _build_evidence(
        prompt=prompt,
        sql=sql,
        meta=meta,
        expectation=expectation,
        rows=rows,
        sample_rows_limit=sample_rows_limit,
    )
    return GovernedQueryResult(evidence=evidence)


def approve_query_result(result: GovernedQueryResult) -> GovernedQueryResult:
    if result.approval_state != "ready_for_analyst_approval":
        raise RuntimeError("QUERY_RESULT_NOT_READY_FOR_APPROVAL")
    return GovernedQueryResult(
        evidence=result.evidence,
        approval_state="approved_for_chart_spec",
        rejection_reason="",
    )


def reject_query_result(result: GovernedQueryResult, reason: str) -> GovernedQueryResult:
    if result.approval_state != "ready_for_analyst_approval":
        raise RuntimeError("QUERY_RESULT_NOT_READY_FOR_APPROVAL")
    rejection_reason = str(reason or "").strip()
    if not rejection_reason:
        raise RuntimeError("REJECTION_REASON_REQUIRED")
    return GovernedQueryResult(
        evidence=result.evidence,
        approval_state="rejected",
        rejection_reason=rejection_reason,
    )


def require_query_result_approved_for_chart_spec(result: GovernedQueryResult) -> None:
    if result.approval_state != "approved_for_chart_spec":
        raise RuntimeError("QUERY_RESULT_NOT_APPROVED")
