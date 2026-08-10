#!/usr/bin/env python3
"""Security and fail-closed negatives for adaptive MCP trust boundaries."""
from __future__ import annotations

import base64
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
    renderer = load_module("adaptive_renderer_security", ROOT / "grafana-renderer-mcp" / "server.py")
    checks: list[dict[str, Any]] = []
    old_org, old_user = os.environ.pop("ANALYSIS_CONTEXT_ORG_ID", None), os.environ.pop("ANALYSIS_CONTEXT_USER_ID", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store = dqp.ArtifactStore(Path(tmp) / "runs")
            for module in [dqp, grafana_query, sandbox, renderer]:
                setattr(module, "ARTIFACTS", store)
            renderer.GRAFANA_URL = "http://grafana.example.invalid"
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
            redacted_error = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "raise RuntimeError('DO_NOT_EXPOSE')", "_server_context": context}, executor=lambda *_: audited_execution({"name": "RuntimeError", "value": "DO_NOT_EXPOSE"}))
            checks.append(require("sandbox_exception_value_redacted", not redacted_error.get("ok") and "DO_NOT_EXPOSE" not in json.dumps(redacted_error), redacted_error))

            png = base64.b64encode(b"\x89PNG\r\n\x1a\nsecurity").decode()
            execution_ref = store.write_json(context, run_id, "sandbox-execution", {"results": [{"mime": {"image/png": png}}], "stdout": [], "stderr": [], "error": None})
            writes: list[dict[str, Any]] = []

            def fake_post(dashboard: dict[str, Any]) -> dict[str, Any]:
                writes.append(dashboard)
                return {"uid": dashboard["uid"], "url": "/d/security"}

            no_approval = renderer.create_dashboard_from_artifacts({"_server_context": context}, post_fn=fake_post)
            checks.append(require("renderer_approval_gate_no_write", not no_approval.get("ok") and not writes, no_approval))
            forged = renderer.create_dashboard_from_artifacts({"approval_ref": f"artifact://{run_id}/render-approval-forged", "_server_context": context}, post_fn=fake_post)
            checks.append(require("renderer_forged_capability_no_write", not forged.get("ok") and not writes, forged))
            prepared = renderer.prepare_dashboard_write({"execution_ref": execution_ref, "_server_context": context})
            renderer_auth = renderer.create_dashboard_from_artifacts({"approval_ref": prepared.get("approval_ref"), "_server_context": other}, post_fn=fake_post)
            checks.append(require("renderer_artifact_authorization_no_write", not renderer_auth.get("ok") and not writes and "context mismatch" in renderer_auth.get("error", ""), renderer_auth))
            raw_output = renderer.prepare_dashboard_write({"execution_ref": execution_ref, "results": [], "_server_context": context})
            checks.append(require("renderer_raw_output_rejected_no_write", not raw_output.get("ok") and not writes, raw_output))

            runtime_tools = {tool["name"] for tool in dqp.TOOLS}
            checks.append(require("legacy_wferp_route_not_exposed", "plan_wferp_query" not in runtime_tools and "plan_wferp_query" not in dqp.HANDLERS, sorted(runtime_tools)))
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
