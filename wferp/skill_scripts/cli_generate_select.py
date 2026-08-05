import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path

from skill_scripts.database_client import DatabaseClient, DatabaseConfig
from skill_scripts.data_dictionary import build_alias_index, build_field_index
from skill_scripts.execution_validator import AggregateExpectation, ExecutionExpectation
from skill_scripts.governed_query import run_governed_query
from skill_scripts.relationship_graph import build_primary_key_map, infer_relationships
from skill_scripts.schema_loader import load_schema_bundle
from skill_scripts.sql_router import RoutingOptions, route_generate_sql


def _artifacts_dir() -> Path:
    return Path(__file__).resolve().parent / "artifacts"


def _write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_artifacts(source: str):
    bundle = load_schema_bundle(source)
    out = _artifacts_dir()
    out.mkdir(parents=True, exist_ok=True)

    field_index = build_field_index(bundle["fields"])
    alias_index = build_alias_index(bundle["fields"])
    pk_map = build_primary_key_map(bundle["index_keys"])
    edges = infer_relationships(bundle["fields"], bundle["index_keys"])

    _write_json(out / "schema_bundle.json", bundle)
    _write_json(out / "field_index.json", field_index)
    _write_json(out / "alias_index.json", alias_index)
    _write_json(out / "primary_key_map.json", pk_map)
    _write_json(out / "relationship_edges.json", edges)


def _parse_required_columns(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _parse_aggregate_expectations(raw: str) -> list[AggregateExpectation]:
    expectations: list[AggregateExpectation] = []
    # format: sum:MK006:1000:0.1,min:MK006:10
    for chunk in [part.strip() for part in str(raw or "").split(",") if part.strip()]:
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) < 3:
            continue
        operation, column = parts[0], parts[1]
        try:
            expected = float(parts[2])
            tolerance = float(parts[3]) if len(parts) >= 4 else 0.0
        except ValueError:
            continue
        expectations.append(
            AggregateExpectation(
                operation=operation,
                column=column,
                expected_value=expected,
                tolerance=tolerance,
            )
        )
    return expectations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="")
    parser.add_argument("--source", default="_Source")
    parser.add_argument("--build-artifacts", action="store_true")
    parser.add_argument("--mode", choices=["rule", "shadow", "llm-first"], default="llm-first")
    parser.add_argument(
        "--llm-provider",
        choices=["pi", "codex", "opencode", "mock", "openai-compatible", "none"],
        default=os.getenv("WFERP_LLM_PROVIDER", os.getenv("LLM_PROVIDER", "opencode")),
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("WFERP_LLM_MODEL", os.getenv("LLM_MODEL", "")),
    )
    parser.add_argument("--llm-timeout-sec", type=float, default=30.0)
    parser.add_argument("--llm-min-confidence", type=float, default=0.6)
    parser.add_argument("--llm-repair-attempts", type=int, default=2)

    parser.add_argument("--validate-execution", action="store_true")
    parser.add_argument("--allow-non-test-db-execution", action="store_true")
    parser.add_argument("--governed-query-json", action="store_true")
    parser.add_argument("--sample-rows-limit", type=int, default=5)
    parser.add_argument("--required-columns", default="")
    parser.add_argument("--min-rows", type=int, default=0)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--aggregate-checks", default="")

    parser.add_argument("--db-driver", default=None)
    parser.add_argument("--db-connection-string", default=None)
    parser.add_argument("--db-auth-mode", default=None)
    parser.add_argument("--db-env", default=None)
    args = parser.parse_args()

    if args.build_artifacts:
        build_artifacts(args.source)
        print("OK:ARTIFACTS_BUILT")
        return

    bundle = load_schema_bundle(args.source)
    try:
        db_config = DatabaseConfig.from_env().with_overrides(
            driver=args.db_driver,
            connection_string=args.db_connection_string,
            auth_mode=args.db_auth_mode,
            env=args.db_env,
        )

        aggregate_expectations = _parse_aggregate_expectations(args.aggregate_checks)
        execution_expectation = ExecutionExpectation(
            required_columns=_parse_required_columns(args.required_columns),
            min_rows=args.min_rows,
            max_rows=args.max_rows,
            aggregates=aggregate_expectations,
        )

        validate_execution = bool(args.validate_execution or args.governed_query_json)
        routing_options = RoutingOptions(
            mode=args.mode,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_timeout_sec=args.llm_timeout_sec,
            min_confidence=args.llm_min_confidence,
            llm_repair_attempts=args.llm_repair_attempts,
            validate_execution=validate_execution,
            execution_expectation=execution_expectation,
            allow_non_test_execution=args.allow_non_test_db_execution,
        )

        db_client = DatabaseClient(db_config) if validate_execution else None
        if args.governed_query_json:
            governed_result = run_governed_query(
                prompt=args.prompt,
                bundle=bundle,
                options=replace(routing_options, validate_execution=True),
                db_client=db_client,
                sample_rows_limit=args.sample_rows_limit,
            )
            print(json.dumps(asdict(governed_result), ensure_ascii=False))
            return
        sql, meta = route_generate_sql(
            prompt=args.prompt,
            bundle=bundle,
            options=routing_options,
            db_client=db_client,
        )
        print(sql)
        print(f"ROUTE:{meta['route']} REASON:{meta['reason']}")
        if "candidate_sql" in meta:
            print(f"CANDIDATE_SQL:{meta['candidate_sql']}")
    except ValueError as exc:
        print(f"ERROR:{exc}")
    except RuntimeError as exc:
        print(f"ERROR:{exc}")


if __name__ == "__main__":
    main()
