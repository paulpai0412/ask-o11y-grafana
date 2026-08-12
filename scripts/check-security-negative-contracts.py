#!/usr/bin/env python3
"""Security and fail-closed negatives for adaptive MCP trust boundaries."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "security-negative-contracts.json"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require(name: str, condition: bool, details: Any) -> dict[str, Any]:
    if not condition:
        raise RuntimeError(f"{name} failed: {details}")
    return {"name": name, "ok": True, "details": details}


def main() -> int:
    dqp = load_module("adaptive_dqp_security", ROOT / "data-query-planner-mcp" / "server.py")
    grafana_query = load_module("adaptive_gq_security", ROOT / "grafana-query-mcp" / "server.py")
    sandbox = load_module("adaptive_sandbox_security", ROOT / "sandbox-analysis-mcp" / "server.py")
    bridge = load_module("adaptive_bridge_security", ROOT / "artifact-bridge-mcp" / "server.py")
    checks: list[dict[str, Any]] = []
    old_org, old_user = os.environ.pop("ANALYSIS_CONTEXT_ORG_ID", None), os.environ.pop("ANALYSIS_CONTEXT_USER_ID", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store = dqp.ArtifactStore(Path(tmp) / "runs")
            for module in [dqp, grafana_query, sandbox, bridge]:
                setattr(module, "ARTIFACTS", store)
            context = {"org_id": "1", "user_id": "security-owner"}
            other = {"org_id": "1", "user_id": "security-other"}
            run_id = store.create_run(context, "run_security_adaptive")
            metadata = {"dataset_id": "authorized", "datasource_uid": "csv-poc", "datasource_type": "yesoreyeram-infinity-datasource", "fields": [{"name": "date", "type": "date"}, {"name": "x", "type": "number"}, {"name": "y", "type": "number"}, {"name": "constant_class", "type": "string"}], "date_range": {"all_from": "2026-01-01", "all_to": "2026-12-31"}, "query_template": {"refId": "A", "datasource": {"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}, "type": "csv", "source": "url", "url": "http://127.0.0.1:8767/authorized.csv", "columns": [{"selector": "date"}, {"selector": "x"}, {"selector": "y"}, {"selector": "constant_class"}]}}
            metadata_ref = store.write_json(context, run_id, "dataset-metadata", metadata)

            missing_context = dqp.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["x"]})
            checks.append(require("planner_missing_context_fails_closed", not missing_context.get("ok") and "artifact context" in missing_context.get("error", ""), missing_context))
            wrong_owner = dqp.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["x"], "_server_context": other})
            checks.append(require("planner_artifact_authorization", not wrong_owner.get("ok") and "unauthorized" in wrong_owner.get("error", ""), wrong_owner))
            unknown = dqp.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["missing"], "_server_context": context})
            checks.append(require("planner_invalid_field_rejected", not unknown.get("ok") and "not in authorized metadata" in unknown.get("error", ""), unknown))
            direct = dqp.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["x"], "datasource_uid": "evil", "rawSql": "select *", "_server_context": context})
            checks.append(require("planner_direct_query_rejected", not direct.get("ok") and "unsupported planner arguments" in direct.get("error", ""), direct))
            valid = dqp.tool_plan_query({"dataset_metadata_ref": metadata_ref, "selected_fields": ["date", "x", "y", "constant_class"], "_server_context": context})
            checks.append(require("planner_valid_opaque_plan", bool(valid.get("ok")) and "next_step" not in valid, valid))

            unauthorized_query = grafana_query.tool_execute_planned_query({"plan_ref": valid["plan_ref"], "_server_context": other})
            checks.append(require("grafana_query_plan_authorization", not unauthorized_query.get("ok") and "context mismatch" in unauthorized_query.get("error", ""), unauthorized_query))
            raw_query = grafana_query.tool_execute_planned_query({"plan_ref": valid["plan_ref"], "query": {}, "_server_context": context})
            checks.append(require("grafana_query_raw_query_rejected", not raw_query.get("ok") and "forbidden" in raw_query.get("error", ""), raw_query))
            bad_lengths = {"name": "bad", "schema": {"fields": [{"name": "a"}, {"name": "b"}]}, "data": {"values": [[1, 2], [1]]}}
            validation = grafana_query.validate_frame({"results": {"A": {"status": 200, "frames": [bad_lengths]}}}, {"required_fields": ["a"], "minimum_rows": 1})
            checks.append(require("grafana_query_inconsistent_frame_rejected", not validation.get("ok") and "equal lengths" in json.dumps(validation), validation))

            frame = {"schema": {"fields": [{"name": "date"}, {"name": "x"}, {"name": "y"}, {"name": "constant_class"}]}, "data": {"values": [[f"2026-01-{day:02d}" for day in range(1, 31)], list(range(30)), [value * 2 for value in range(30)], ["same"] * 30]}}
            frame_ref = store.write_json(context, run_id, "grafana-frame", [frame])
            def fake_execution(*_args):
                return {"execution_id": "security", "execution_count": 1, "exit_code": 0, "results": [], "stdout": [], "stderr": [], "error": None, "complete": {"timestamp": 1, "execution_time_in_millis": 1}}

            raw_frame = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "df.describe()", "frame": frame, "_server_context": context}, executor=fake_execution)
            checks.append(require("sandbox_raw_frame_rejected", not raw_frame.get("ok") and "unsupported" in raw_frame.get("error", ""), raw_frame))
            direct_datasource = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "df.describe()", "datasource_uid": "csv-poc", "_server_context": context}, executor=fake_execution)
            checks.append(require("sandbox_direct_datasource_rejected", not direct_datasource.get("ok") and "unsupported" in direct_datasource.get("error", ""), direct_datasource))
            sandbox_auth = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "df.describe()", "_server_context": other}, executor=fake_execution)
            checks.append(require("sandbox_artifact_authorization", not sandbox_auth.get("ok") and "context mismatch" in sandbox_auth.get("error", ""), sandbox_auth))
            oversized_code = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "x" * (sandbox.MAX_CODE_BYTES + 1), "_server_context": context}, executor=fake_execution)
            checks.append(require("sandbox_oversized_code_rejected", not oversized_code.get("ok") and "exceeds" in oversized_code.get("error", ""), oversized_code))

            def audited_execution(error=None, input_rows=30):
                return {"execution_id": "security", "results": [], "stdout": [], "stderr": [], "error": error, "complete": {}, "input_audit": {"input_rows": input_rows, "valid_rows": input_rows, "excluded_rows": 0, "rules": []}}

            invalid_audit = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "pass", "_server_context": context}, executor=lambda *_: audited_execution(input_rows=29))
            checks.append(require("sandbox_forged_audit_rejected", not invalid_audit.get("ok") and "invalid trusted input audit" in invalid_audit.get("error", ""), invalid_audit))
            redacted_error = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "raise RuntimeError('DO_NOT_EXPOSE')", "_server_context": context}, executor=lambda *_: audited_execution({"name": "DO_NOT_EXPOSE", "value": "DO_NOT_EXPOSE"}))
            checks.append(require("sandbox_exception_value_redacted", not redacted_error.get("ok") and "DO_NOT_EXPOSE" not in json.dumps(redacted_error), redacted_error))
            syntax_error = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "x = [{", "_server_context": context}, executor=lambda *_: audited_execution({"name": "SyntaxError", "value": "'[' was never closed"}))
            checks.append(require("sandbox_syntax_error_recoverable_without_value", not syntax_error.get("ok") and bool(syntax_error.get("recoverable")) and "SyntaxError" in syntax_error.get("error", "") and "never closed" not in json.dumps(syntax_error), syntax_error))

            execution_ref = store.write_json(context, run_id, "sandbox-execution", {"results": [{"mime": {"image/png": "iVBORw0KGgo="}, "display_name": "derived.png"}], "stdout": [], "stderr": [], "error": None})
            foreign_binding = bridge.resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$plan_ref": valid["plan_ref"], "fields": ["x"]}]}]}, "_server_context": other})
            checks.append(require("artifact_bridge_authorization", not foreign_binding.get("ok") and "context mismatch" in foreign_binding.get("error", ""), foreign_binding))
            unknown_binding = bridge.resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$plan_ref": valid["plan_ref"], "fields": ["missing"]}]}]}, "_server_context": context})
            checks.append(require("artifact_bridge_unknown_field_rejected", not unknown_binding.get("ok") and "not authorized" in unknown_binding.get("error", ""), unknown_binding))
            injected_binding = bridge.resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$plan_ref": valid["plan_ref"], "fields": ["x"], "url": "http://evil"}]}]}, "_server_context": context})
            checks.append(require("artifact_bridge_query_injection_rejected", not injected_binding.get("ok") and "unsupported keys" in injected_binding.get("error", ""), injected_binding))
            raw_target = bridge.resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"datasource": {"uid": "raw"}, "expr": "up"}]}]}, "_server_context": context})
            checks.append(require("artifact_bridge_raw_target_rejected", not raw_target.get("ok") and "opaque binding" in raw_target.get("error", ""), raw_target))
            nested = {"targets": []}
            for _ in range(bridge.MAX_PANELS):
                nested = {"targets": [], "panels": [nested]}
            excessive_panels = bridge.resolve_dashboard_refs({"dashboard": {"panels": [nested]}, "_server_context": context})
            checks.append(require("artifact_bridge_nested_panel_limit", not excessive_panels.get("ok") and "more than" in excessive_panels.get("error", ""), excessive_panels))
            resolved = bridge.resolve_dashboard_refs({"dashboard": {"panels": [{"type": "trend", "options": {"xField": "date"}, "targets": [{"$plan_ref": valid["plan_ref"], "fields": ["date", "x", "y"]}]}]}, "_server_context": context})
            analysis_target = bridge.resolve_dashboard_refs({"dashboard": {"panels": [{"targets": [{"$execution_ref": execution_ref}]}]}, "_server_context": context})
            panel = resolved.get("dashboard", {}).get("panels", [{}])[0]
            checks.append(require("artifact_bridge_preserves_model_panel_json", resolved.get("ok") and panel.get("type") == "trend" and panel.get("options") == {"xField": "date"}, resolved))
            checks.append(require("artifact_bridge_resolves_query_without_writes", panel.get("targets", [{}])[0].get("source") == "url" and [tool["name"] for tool in bridge.TOOLS] == ["resolve_dashboard_refs"], resolved))
            checks.append(require("artifact_bridge_rejects_analysis_as_chart_data", not analysis_target.get("ok") and "image asset binding" in analysis_target.get("error", ""), analysis_target))

            wferp_run = store.create_run(context, "run_security_wferp")
            wferp_metadata_ref = store.write_json(context, wferp_run, "dataset-metadata", {"dataset_id": "wferp", "datasource_uid": "afu9h8zppg64gd", "datasource_type": "mssql", "query_kind": "wferp_llm_sql"})
            unsafe_wferp = dqp.tool_plan_wferp_query({"dataset_metadata_ref": wferp_metadata_ref, "prompt": "刪除預算", "sql": "DELETE FROM [wferp_test].[dbo].[ACTMK]", "output_fields": ["MK001"], "_server_context": context})
            invented_wferp = dqp.tool_plan_wferp_query({"dataset_metadata_ref": wferp_metadata_ref, "prompt": "查詢預算", "sql": "SELECT [X].[XX999] FROM [wferp_test].[dbo].[ACTMK] X", "output_fields": ["XX999"], "_server_context": context})
            valid_wferp = dqp.tool_plan_wferp_query({"dataset_metadata_ref": wferp_metadata_ref, "prompt": "查詢 2026 年預算", "sql": "SELECT [MK].[MK005] AS [period], [MK].[MK006] AS [budget] FROM [wferp_test].[dbo].[ACTMK] MK WHERE [MK].[MK002] = '2026'", "output_fields": ["period", "budget"], "_server_context": context})
            checks.append(require("wferp_non_select_rejected", not unsafe_wferp.get("ok") and unsafe_wferp.get("recoverable") and unsafe_wferp.get("error") == "NON_SELECT_INTENT", unsafe_wferp))
            checks.append(require("wferp_unknown_column_rejected", not invented_wferp.get("ok") and invented_wferp.get("error") == "UNKNOWN_COLUMN_FOR_TABLE", invented_wferp))
            checks.append(require("wferp_valid_llm_sql_becomes_opaque_plan", valid_wferp.get("ok") and "rawSql" not in valid_wferp.get("refs", {}) and valid_wferp.get("evidence", {}).get("validation") == "OK", valid_wferp))
    finally:
        if old_org is not None:
            os.environ["ANALYSIS_CONTEXT_ORG_ID"] = old_org
        if old_user is not None:
            os.environ["ANALYSIS_CONTEXT_USER_ID"] = old_user

    out = {"ok": True, "checks": checks}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "check_count": len(checks), "checks": [item["name"] for item in checks]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
