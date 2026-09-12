#!/usr/bin/env python3
"""Offline regression of the real MCP handlers; never runs generated code on the host."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "ANALYSIS_ARTIFACT_ROOT": directory + "/runs",
        "UPLOAD_DATASET_ROOT": directory + "/uploads",
        "ANALYSIS_CSV_OUTPUT_DIR": directory + "/outputs",
        "MCP_SHARED_TOKEN": "fixture-token-not-a-credential-123456789",
    }):
        sandbox = load("minimal_sandbox", ROOT / "sandbox-analysis-mcp/server.py")
        bridge = load("minimal_bridge", ROOT / "artifact-bridge-mcp/server.py")
        query = load("minimal_query", ROOT / "grafana-query-mcp/server.py")
        context = {"org_id": "1", "user_id": "7", "session_id": "minimal-session"}
        run = sandbox.ARTIFACTS.create_run(context)
        frame = {"schema": {"fields": [{"name": "x", "type": "number"}]}, "data": {"values": [[1, 2, 3]]}}
        frame_ref = sandbox.ARTIFACTS.write_json(context, run, "grafana-frame", [frame])
        upload = query.uploaded_datasets.store_upload(context=context, session_id=context["session_id"], filename="tiny.csv", raw=b"x\n1\n2\n3\n")
        response = {"results": {"A": {"status": 200, "frames": [frame]}}}
        with patch.object(query, "get_grafana", return_value={"uid": "csv-poc", "type": "yesoreyeram-infinity-datasource"}), patch.object(query, "post_grafana", return_value=response) as post:
            query_args = {"dataset_id": upload["id"], "_server_context": context, "_server_session_id": context["session_id"]}
            rpc = query.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "query_dataset", "arguments": query_args}})
            queried = json.loads(rpc["result"]["content"][0]["text"])
            assert queried["ok"] and "plan_ref" not in queried["refs"], queried
            frame_ref = queried["frame_ref"]
            assert query.ARTIFACTS.read_json(context, frame_ref) == [frame]
            post.assert_called_once()
            assert post.call_args.args[0] == "/api/ds/query"
            assert post.call_args.args[1]["queries"][0]["datasource"]["uid"] == "csv-poc"
            for overrides in ({"sql": "SELECT 1"}, {"time_range": {"from": "now-9999999999d", "to": "now"}}, {"time_range": {"from": "now", "to": "now-1d"}}, {"dataset_id": "not-authorized"}, {"_server_session_id": "foreign-session"}):
                assert not query.tool_query_dataset({**query_args, **overrides})["ok"]
                assert post.call_count == 1, "invalid query reached Grafana"
        bundle = query.wferp_sql.load_metadata()["bundle"]
        table = bundle["tables"][0]["TableID"]
        column = next(field["ID"] for field in bundle["fields"] if field["TableID"] == table)
        sql = f"SELECT TOP 3 [t].[{column}] FROM [WFERP_TEST].[dbo].[{table}] AS [t]"
        with patch.object(query, "get_grafana", return_value={"type": "mssql"}), patch.object(query, "post_grafana", return_value=response) as post:
            sql_args = {"dataset_id": "wferp", "sql": sql, "_server_context": context, "_server_session_id": context["session_id"]}
            assert query.tool_query_dataset(sql_args)["ok"]
            assert post.call_count == 1
            for forbidden in ("DROP TABLE x", sql + "; DELETE FROM x", sql.replace("[WFERP_TEST]", "[master]"), sql.replace(f"[{column}]", "[not_a_column]")):
                assert not query.tool_query_dataset({**sql_args, "sql": forbidden})["ok"]
                assert post.call_count == 1, "unsafe SQL reached Grafana"
        calls = []
        failure_source = "# first\n# second\nfail"
        figure = {"data": [{"type": "scatterpolar", "r": [1, 2, 3], "theta": [0, 120, 240]}], "layout": {"title": {"text": "Retained polar"}}}

        def executor(bundle, code, seed):
            assert json.loads(bundle)["frame"] == frame
            assert not json.loads(bundle).get("semantic_contract")
            assert not json.loads(bundle).get("validity_rules")
            calls.append(code)
            if code == "unknown":
                raise TimeoutError("must not leak")
            error = {"name": "ValueError", "value": "private-row-value", "traceback": ['File "<generated-analysis>", line 3', "ValueError: private-row-value"]} if code == failure_source else None
            output_figure = {**figure, "layout": {"images": [{"source": "https://external.invalid/image.png"}]}} if code == "unsafe" else figure
            return {"execution_id": "test-execution", "complete": None if code == "incomplete" else {"timestamp": 1}, "exit_code": None,
                    "results": [{"mime": {"application/vnd.plotly.v1+json": json.dumps(output_figure)}, "display_name": "polar.json"}],
                    "stdout": [], "stderr": [], "error": error,
                    "input_audit": {"input_rows": 3, "valid_rows": 3, "excluded_rows": 0, "rules": []}}

        args = {"frame_ref": frame_ref, "python_code": "figure", "_server_context": context}
        result = sandbox.execute_python_analysis(args, executor=executor)
        assert result["ok"], result
        assert "report_manifest_ref" not in result["refs"]
        assert result["provenance"]["trusted_ml_contract"] is False
        assert result["output_summary"]["figures"][0]["output_index"] == 0
        assert sandbox.execute_python_analysis(args, executor=executor) == result
        assert calls == ["figure"], "completed retry must use its existing receipt"
        for changed in ({"user_id": "8"}, {"org_id": "2"}, {"session_id": "other-session"}):
            rejected = sandbox.execute_python_analysis({**args, "_server_context": {**context, **changed}}, executor=executor)
            assert not rejected["ok"] and calls == ["figure"], rejected

        failed = sandbox.execute_python_analysis({**args, "python_code": failure_source}, executor=executor)
        assert not failed["ok"] and failed["recoverable"], failed
        assert "ValueError" in json.dumps(failed) and "private-row-value" not in json.dumps(failed)
        assert failed["evidence"]["python_error"]["line_numbers"] == [3]
        fixed = sandbox.execute_python_analysis({**args, "python_code": "fixed"}, executor=executor)
        assert fixed["ok"] and calls == ["figure", failure_source, "fixed"]
        unknown_args = {**args, "python_code": "unknown"}
        unknown = sandbox.execute_python_analysis(unknown_args, executor=executor)
        assert not unknown["ok"] and unknown["evidence"]["effect_outcome"] == "indeterminate"
        assert not sandbox.execute_python_analysis(unknown_args, executor=executor)["ok"]
        assert calls.count("unknown") == 1
        incomplete = {**args, "python_code": "incomplete"}
        assert sandbox.execute_python_analysis(incomplete, executor=executor)["evidence"]["effect_outcome"] == "indeterminate"
        assert not sandbox.execute_python_analysis(incomplete, executor=executor)["ok"]
        assert calls.count("incomplete") == 1

        dashboard = {"title": "minimal fixture", "panels": [{"type": "asko11y-plotly-panel", "options": {"renderMode": "plotly", "figureFormat": sandbox.ml_plotly_contract.FIGURE_FORMAT, "figure": "$plotly_original"},
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_original", "$execution_ref": result["refs"]["execution_ref"], "output_index": 0, "plugin_id": "asko11y-plotly-panel"}]}]}
        resolved = bridge.resolve_dashboard_refs({"dashboard": dashboard, "_server_context": context})
        assert resolved["ok"], resolved
        assert resolved["dashboard"]["panels"][0]["options"]["figure"]["data"][0] == figure["data"][0]
        assert dashboard["panels"][0]["options"]["figure"] == "$plotly_original"
        assert not bridge.resolve_dashboard_refs({"dashboard": dashboard, "_server_context": {**context, "user_id": "8"}})["ok"]
        unsafe = sandbox.execute_python_analysis({**args, "python_code": "unsafe"}, executor=executor)
        assert unsafe["ok"], "figure safety is separate from completed computation"
        unsafe_dashboard = json.loads(json.dumps(dashboard))
        unsafe_dashboard["panels"][0]["askO11yPlotlyBindings"][0]["$execution_ref"] = unsafe["refs"]["execution_ref"]
        assert not bridge.resolve_dashboard_refs({"dashboard": unsafe_dashboard, "_server_context": context})["ok"]
        assert sandbox.ARTIFACTS.read_json(context, unsafe["refs"]["execution_ref"])["results"], "original output must remain intact"
        names = {tool["name"] for tool in sandbox.TOOLS}
        retired = {"execute_ml_contract", "profile_dataset", "repair_generic_report", "reexport_trusted_report", "get_ml_capabilities"}
        assert not names & retired
        for name in retired:
            assert "error" in sandbox.handle_rpc({"method": "tools/call", "id": 1, "params": {"name": name}})
        for name in ("prepare_ml_report", "inspect_report_artifacts", "compose_ml_dashboard"):
            assert "error" in bridge.handle_rpc({"method": "tools/call", "id": 1, "params": {"name": name}})
        assert all(tool["annotations"]["readOnlyHint"] for tool in query.TOOLS)
        settings = load("minimal_settings", ROOT / "scripts/configure-ask-o11y-workflow-tools.py")
        configured = settings.build_json_data({"defaultSystemPrompt": "saved custom prompt", "approvalPolicy": "approved"}, True)
        settings.validate_payload({"jsonData": configured})
        assert configured["defaultSystemPrompt"] == "saved custom prompt"
        assert configured["approvalPolicy"] == "approval-gated-writes"
        services = {"grafana-query": query, "sandbox-analysis": sandbox, "artifact-bridge": bridge}
        for server in settings.SERVER_SPECS:
            assert set(server["enabled_tools"]) == {tool["name"] for tool in services[server["id"]].TOOLS}
        assert settings.SYSTEM_PROMPT == (ROOT / "ask-o11y/pkg/plugin/analyst_prompt.md").read_text()
        existing = {"defaultSystemPrompt": "saved custom prompt", "maxTotalTokens": 12345, "builtInMCPToolSelections": {"delete_dashboard": False}}
        with patch.object(settings, "auth_headers", return_value={}), patch.object(settings, "secure_mcp_headers", return_value={}), patch.object(settings.urllib.request, "urlopen", side_effect=[io.BytesIO(json.dumps({"jsonData": existing}).encode()), io.BytesIO(b"{}")]) as http:
            settings.apply_settings("http://unused.invalid", {"jsonData": settings.build_json_data({}, True)})
            applied = json.loads(http.call_args.args[0].data)["jsonData"]
            assert all(applied[key] == value for key, value in existing.items()), "migration changed unrelated settings or saved prompt/tool permissions"
            assert applied["approvalPolicy"] == "approval-gated-writes"
        with patch.object(sys, "argv", ["configure", "--local-defaults", "--self-check", "--out", directory + "/candidate.json"]), patch("sys.stdout", new_callable=io.StringIO):
            assert settings.main() == 0, "self-check must support an explicit output outside the repo"
    print("PASS: plan-free Python, replay receipt, actor/session isolation, safe failure repair, unknown no replay, native figure binding")


if __name__ == "__main__":
    main()
