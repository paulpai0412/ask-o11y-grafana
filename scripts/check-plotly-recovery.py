#!/usr/bin/env python3
"""Isolated real host-contract checks; fake only the remote Python executor."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(os.environ.get("PLOTLY_REVIEW_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    with tempfile.TemporaryDirectory(prefix="plotly-recovery-") as tmp:
        os.environ["ANALYSIS_ARTIFACT_ROOT"] = tmp
        sandbox = load("plotly_recovery_sandbox", ROOT / "sandbox-analysis-mcp/server.py")
        contract = sandbox.ml_plotly_contract
        cap = contract.capability_summary()
        assert cap['format'] == 'ask-o11y-ml-plotly-v2'
        assert cap['plotly_python_version'] and cap['plotly_js_version']
        assert 'trace_keys' not in cap and 'nested_keys' not in cap
        assert (ROOT / "ml_plotly_contract.py").read_bytes() == (ROOT / "sandbox-analysis-mcp/ml_plotly_contract.py").read_bytes()
        for angle in (-180, 180, 270):
            contract.sanitize_figure({"data": [{"type": "bar", "y": [1]}], "layout": {"xaxis": {"tickangle": angle}}})
        for angle in (math.inf, math.nan):
            try:
                contract.sanitize_figure({"data": [{"type": "bar", "y": [1]}], "layout": {"xaxis": {"tickangle": angle}}})
            except ValueError:
                continue
            raise AssertionError("unsafe tickangle accepted")
        serialized_cap = json.dumps(cap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for name in ("execute_python_analysis", "revise_python_analysis"):
            tool = next(t for t in sandbox.TOOLS if t["name"] == name)
            assert serialized_cap in tool["description"], "actual selected tool must carry capabilities"
        context = {"org_id": "1", "user_id": "synthetic", "session_id": "recovery-test"}
        store = sandbox.ARTIFACTS
        run = store.create_run(context)
        frame_ref = store.write_json(context, run, "grafana-frame", [{"schema": {"fields": [{"name": "x"}]}, "data": {"values": [[1, 2]]}}])
        plan = {"business_question": "Inspect synthetic x", "analysis_input_contract": {"validity_rules": []}}
        plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        store.write_json(context, run, "query-plan", plan)
        calls = []

        def executor(bundle, code, seed):
            calls.append((bundle, code, seed))
            axis = {"unsupported_axis_key": 1} if code == "bad" else {"tickangle": -35}
            figure = {"data": [{"type": "bar", "x": [1, 2], "y": [2, 3]}], "layout": {"xaxis": axis}}
            results = [{"display_name": "figure.json", "mime": {"application/vnd.plotly.v1+json": json.dumps(figure)}}]
            plain_outputs = {"scalar": ("application/json", '{"mean":1.5}'), "table": ("text/csv", "x\n1\n2\n"), "text": ("text/plain", "兩筆資料的平均值為 1.5；不是因果證據。")}
            if code in plain_outputs:
                mime, value = plain_outputs[code]
                results = [{"display_name": "result.csv" if code == "table" else "result.json" if code == "scalar" else "summary.txt", "text": value if code == "text" else None, "mime": {} if code == "text" else {mime: value}}]
            return {"results": results, "error": None, "stdout": [], "stderr": [], "input_audit": {"input_rows": 2, "valid_rows": 2, "excluded_rows": 0, "rules": []}}

        original_execute = sandbox.execute_python_analysis
        setattr(sandbox, "execute_python_analysis", lambda args, **kwargs: original_execute(args, **{**kwargs, "executor": executor}))

        def call(name, arguments):
            reply = sandbox.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {**arguments, "_server_context": context}}})
            try:
                result = json.loads(reply["result"]["content"][0]["text"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AssertionError("invalid public tool response") from exc
            assert reply["result"].get("isError", False) == (not result["ok"])
            return result

        partial = call("execute_python_analysis", {"frame_ref": frame_ref, "python_code": "bad"})
        assert partial['ok'] and partial['output_summary']['report_status'] == 'partial', partial
        assert partial['evidence']['presentation_errors'][0]['code'] == 'invalid_native_figure'
        assert not call('repair_generic_report', {'execution_ref': partial['refs']['execution_ref']})['ok']
        # Synthetic historical v1 rejection for recovery/reconciliation compatibility.
        # New executions above must not manufacture the historical rejection status.
        history = store.create_run(context)
        execution_ref = store.write_json(context, history, 'sandbox-execution', store.read_json(context, partial['refs']['execution_ref']))
        prior = store.read_json(context, partial['refs']['provenance_ref'])
        prior['code_ref'] = store.write_json(context, history, 'sandbox-code', store.read_json(context, prior['code_ref']))
        prior.update({'execution_ref': execution_ref, 'report_manifest_ref': None, 'report_status': 'rejected', 'report_error': 'report manifest rejected: unsupported_axis_key', 'report_error_code': 'plotly_figure_contract_violation'})
        provenance_ref = store.write_json(context, history, 'sandbox-provenance', prior)
        bad = {'error': prior['report_error']}
        execution_before = store.read_json(context, execution_ref)
        provenance_before = store.read_json(context, provenance_ref)
        operations = store.root / "operations"
        before = sorted(p.name for p in operations.iterdir())
        rejected = call("repair_generic_report", {"execution_ref": execution_ref})
        assert rejected["evidence"]["original_report_error"] == bad["error"]
        assert rejected["evidence"]["frame_ref"] == frame_ref
        assert sorted(p.name for p in operations.iterdir()) == before
        assert len(calls) == 1
        good = call("execute_python_analysis", {"frame_ref": frame_ref, "python_code": "corrected"})
        assert good["ok"] and good["refs"]["report_manifest_ref"]
        assert len(calls) == 2 and calls[0][0] == calls[1][0] and calls[0][1] != calls[1][1]
        assert store.read_json(context, execution_ref) == execution_before
        assert store.read_json(context, provenance_ref) == provenance_before

        inputs = {"execution_ref": execution_ref, "source_results_sha256": provenance_before["captured_results_sha256"], "source_provenance_sha256": sandbox._canonical_provenance_digest(provenance_before)}
        def interrupted():
            raise RuntimeError("synthetic old interrupted repair")
        try:
            store.run_once(context, "repair_generic_report", inputs, interrupted)
        except RuntimeError as exc:
            assert str(exc) == "synthetic old interrupted repair"
        operation_id = sandbox._repair_operation_id(context, inputs)
        unknown = call("repair_generic_report", {"execution_ref": execution_ref})
        assert unknown["evidence"]["operation_id"] == operation_id
        assert unknown["evidence"]["effect_outcome"] == "indeterminate"
        assert unknown["evidence"]["recovery_action"] == "reconcile_operation"
        assert not unknown["evidence"].get("correction_required") and len(calls) == 2
        assert unknown["evidence"]["original_report_error"] == bad["error"]
        # Persist a terminal historical response: replay must beat changed validation.
        terminal = {"ok": False, "error": "retained terminal failure", "evidence": {"operation_id": operation_id}}
        store._durable_json(operations / operation_id / "completion.json", {"operation_id": operation_id, "result": terminal})
        assert call("repair_generic_report", {"execution_ref": execution_ref}) == terminal

        write_json = store.write_json
        def fail_manifest(ctx, run_id, name, value):
            if name == "report-manifest":
                raise OSError("synthetic persistence failure")
            return write_json(ctx, run_id, name, value)
        store.write_json = fail_manifest
        try:
            failed_write = call("execute_python_analysis", {"frame_ref": frame_ref, "python_code": "valid persistence fixture"})
        finally:
            store.write_json = write_json
        assert failed_write["evidence"]["recovery_action"] == "repair_generic_report"
        assert failed_write["evidence"]["error_code"] == "report_persistence_failed"
        count = len(calls)
        repaired = call("repair_generic_report", {"execution_ref": failed_write["evidence"]["execution_ref"]})
        assert repaired["ok"] and len(calls) == count

        for kind in ("scalar", "table", "text"):
            plain = call("execute_python_analysis", {"frame_ref": frame_ref, "python_code": kind})
            assert plain["ok"], plain
            assert plain["output_summary"]["computation_status"] == "succeeded"
            assert plain["output_summary"]["report_status"] == "not_requested"
            assert "report_manifest_ref" not in plain["refs"]
            assert plain["output_summary"]["inline_results" if kind != "table" else "downloads"]
            prior_ref = plain["refs"]["provenance_ref"]
            prior = store.read_json(context, prior_ref)
            assert prior["input_frame_ref"] == frame_ref and prior["report_manifest_ref"] is None
            raw = store.read_json(context, plain["refs"]["execution_ref"])
            assert len(raw["results"]) == 1 and prior["host_report_source_sha256"] is None
            revised = call("revise_python_analysis", {"provenance_ref": prior_ref, "python_code": kind})
            assert revised["ok"] and revised["provenance"]["parent_provenance_ref"] == prior_ref
            assert revised["output_summary"]["report_status"] == "not_requested"
            assert store.read_json(context, prior_ref) == prior
        assert isinstance(plain["provenance"]["trusted_ml_contract"], bool) and not plain["provenance"]["trusted_ml_contract"]
        assert "requested report" in plain["instruction"], "standalone output must not replace the requested report"

        # Plain output is not an escape hatch for forged metadata or unsupported visuals.
        for result in (
            {"display_name": "REPORT-SOURCE.JSON", "mime": {"application/json": "{}"}},
            {"display_name": "renamed.json", "mime": {"application/json": json.dumps({"format": sandbox.ml_report_contract.REPORT_SOURCE_FORMAT})}},
            {"mime": {"image/svg+xml": "<svg/>"}},
            {"mime": {"text/html": "<script>synthetic</script>"}},
            {"mime": {"application/vnd.plotly.v1+json": "invalid"}},
        ):
            candidate = {"error": None, "results": [result]}
            _, status, error = sandbox._preflight_report(candidate, raw["input_audit"], "plotly", "execute_python_analysis")
            assert status == "rejected" and error, result
        for step in ("profile_dataset", "execute_ml_contract"):
            _, status, error = sandbox._preflight_report({"error": None, "results": []}, raw["input_audit"], "plotly", step)
            assert status == "rejected" and error, "trusted report producers still require their report"
        if path := os.environ.get("PLOTLY_TOOL_FIXTURE_OUT"):
            Path(path).write_text(json.dumps({"tools": sandbox.TOOLS, "result": partial}, ensure_ascii=False))
    print("PASS: native capability/partial delivery; historical rejection preflight, unknown/terminal replay; immutable receipts; same-frame correction; persistence-only repair; standalone execute/revise; source and visual boundaries")


if __name__ == "__main__":
    main()
