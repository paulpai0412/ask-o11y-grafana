#!/usr/bin/env python3
"""Focused Y5 regression: real Plotly capture/manifest/bridge and host status."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PUBLIC_TRACE: list[dict] = []


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def rejects(fn, marker: str) -> None:
    try:
        fn()
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        if marker not in str(exc):
            raise AssertionError(f"wrong rejection: expected {marker!r}, got {exc}") from exc
        return
    raise AssertionError(f"expected rejection containing {marker!r}")


def parse_json(payload: str, label: str) -> dict:
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"invalid JSON fixture: {label}") from exc
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object fixture: {label}")
    return value


def public_call(module, name: str, arguments: dict, context: dict) -> dict:
    reply = module.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": name, "arguments": {**arguments, "_server_context": context},
    }})
    try:
        result = json.loads(reply["result"]["content"][0]["text"])
        PUBLIC_TRACE.append({"name": ("sandbox-analysis_" if "sandbox" in module.__name__ else "artifact-bridge_") + name,
                             "arguments": arguments, "result": result})
        return result
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"invalid public {name} response") from exc


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="y5-minimal-") as tmp:
        os.environ["ANALYSIS_ARTIFACT_ROOT"] = tmp
        contract = load("y5_plotly_contract", ROOT / "ml_plotly_contract.py")
        sandbox_contract = load("y5_sandbox_plotly_contract", ROOT / "sandbox-analysis-mcp/ml_plotly_contract.py")
        bridge = load("y5_bridge", ROOT / "artifact-bridge-mcp/server.py")
        import plotly.express as px
        import plotly.graph_objects as go

        figures = [
            ("bar.json", px.bar(x=["A", "B", "C"], y=[1.0, 2.0, 3.0])),
            ("scatter.json", px.scatter(x=[1.0, 2.0, 3.0], y=[4.0, 5.0, 6.0])),
            ("go-scatter.json", go.Figure(go.Scatter(x=[3.0, 1.0, 2.0], y=[9.0, 7.0, 8.0]))),
        ]
        results = []
        expected = [(["A", "B", "C"], [1.0, 2.0, 3.0]), ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]), ([3.0, 1.0, 2.0], [9.0, 7.0, 8.0])]
        for (name, figure), (expected_x, expected_y) in zip(figures, expected, strict=True):
            # Figure.to_json() is the exact payload written by capture.emit().
            captured = parse_json(str(figure.to_json()), f"capture:{name}")
            clean = contract.sanitize_figure(captured)
            assert clean["data"][0]["x"] == expected_x, clean
            assert clean["data"][0]["y"] == expected_y, clean
            bridge_clean = bridge._plotly_figure({"mime": {"application/vnd.plotly.v1+json": json.dumps(captured)}})
            assert bridge_clean == clean
            assert sandbox_contract.sanitize_figure(captured) == clean
            results.append({"display_name": name, "mime": {"application/vnd.plotly.v1+json": json.dumps(captured)}})

        source = {
            "format": "ask-o11y-report-source-v1", "purpose": "Plotly producer evidence",
            "conclusion": "The captured values remain ordered and bounded",
            "facts": {"rows": {"kind": "number", "label": "Rows", "value": 3}},
            "artifacts": [{"artifact_id": name.removesuffix(".json"), "fact_refs": ["rows"], "plotly_output_name": name} for name, _ in figures],
        }
        results.insert(0, {"display_name": "report-source.json", "mime": {"application/json": json.dumps(source)}})
        # Importing the public report module separately avoids relying on server internals.
        report = load("y5_report_contract", ROOT / "ml_report_contract.py")
        normalized = report.normalize_report_manifest(execution_ref="artifact://run_y5/sandbox-execution", results=results)
        assert len(normalized["artifacts"]) == 3

        typed = {"data": [{"type": "scatter", "x": {"dtype": "f8", "bdata": base64.b64encode(struct.pack("<3d", 3, 1, 2)).decode()}, "y": [9, 7, 8]}], "layout": {}}
        assert contract.sanitize_figure(typed)["data"][0]["x"] == [3.0, 1.0, 2.0]
        for copy in (contract, sandbox_contract):
            for dtype, fmt, values in (("i8", "q", [-(2**53 - 1), 0, 2**53 - 1]), ("u8", "Q", [0, 2**53 - 1])):
                payload = base64.b64encode(struct.pack("<" + fmt * len(values), *values)).decode()
                figure = {"data": [{"type": "scatter", "x": {"dtype": dtype, "bdata": payload}, "y": [1] * len(values)}], "layout": {}}
                assert copy.sanitize_figure(figure)["data"][0]["x"] == values
            for dtype, fmt, value in (("i8", "q", -(2**53)), ("u8", "Q", 2**53)):
                payload = base64.b64encode(struct.pack("<" + fmt, value)).decode()
                unsafe_integer = {"data": [{"type": "scatter", "x": {"dtype": dtype, "bdata": payload}, "y": [1]}], "layout": {}}
                rejects(lambda unsafe_integer=unsafe_integer: copy.sanitize_figure(unsafe_integer), "safely representable")
            mutated_template = {"data": [{"type": "scatter", "x": [1], "y": [2]}], "layout": {"template": {"layout": {"title": {"text": "mutated"}}}}}
            rejects(lambda mutated_template=mutated_template: copy.sanitize_figure(mutated_template), "installed Plotly default")
        rejects(lambda: contract.sanitize_figure({"data": [{"type": "scatter", "x": {"dtype": "f8", "bdata": "%%%"}, "y": [1]}], "layout": {}}), "base64")
        rejects(lambda: contract.sanitize_figure({"data": [{"type": "scatter", "x": {"dtype": "f8", "bdata": base64.b64encode(b"x").decode()}, "y": [1]}], "layout": {}}), "byte length")
        oversized = base64.b64encode(struct.pack("<" + "d" * 2001, *([1.0] * 2001))).decode()
        rejects(lambda: contract.sanitize_figure({"data": [{"type": "scatter", "x": {"dtype": "f8", "bdata": oversized}, "y": [1]}], "layout": {}}), "points")
        rejects(lambda: contract.sanitize_figure({"data": [{"type": "scatter", "x": [1], "y": [1], "hovertemplate": "unsafe"}], "layout": {}}), "hovertemplate")
        rejects(lambda: contract.sanitize_figure({"data": [{"type": "scatter", "x": [1], "y": [1], "marker": {"pattern": {"shape": "x"}}}], "layout": {}}), "pattern")

        sandbox = load("y5_sandbox", ROOT / "sandbox-analysis-mcp/server.py")
        store = sandbox.ArtifactStore(Path(tmp) / "sandbox-runs")
        setattr(sandbox, "ARTIFACTS", store)
        context = {"org_id": "1", "user_id": "y5-check", "session_id": "y5-session"}
        input_run = store.create_run(context)
        frame_ref = store.write_json(context, input_run, "grafana-frame", [{"schema": {"fields": [{"name": "x"}]}, "data": {"values": [[1, 2]]}}])
        calls = 0

        def rejected_report_executor(bundle: str, code: str, seed: int) -> dict:
            nonlocal calls
            calls += 1
            return {"execution_id": "fake", "execution_count": 1, "exit_code": 0, "results": [{"display_name": "figure.json", "mime": {"application/vnd.plotly.v1+json": json.dumps({"data": [{"type": "scatter", "x": [1, 2], "y": [3, 4], "hovertemplate": "unsafe"}], "layout": {}})}}], "stdout": [], "stderr": [], "error": None, "input_audit": {"input_rows": 2, "valid_rows": 2, "excluded_rows": 0, "rules": []}}

        rejected = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "display(df)", "seed": 4, "presentation_mode": "plotly", "_server_context": context}, executor=rejected_report_executor)
        assert not rejected["ok"] and "report manifest rejected" in rejected["error"], rejected
        assert calls == 1
        evidence_refs = rejected["evidence"]
        assert evidence_refs["computation_status"] == "succeeded" and evidence_refs["report_status"] == "rejected", evidence_refs
        execution_ref = evidence_refs["execution_ref"]
        provenance_ref = evidence_refs["provenance_ref"]
        retained_execution = store.read_json(context, execution_ref)
        retained_provenance = store.read_json(context, provenance_ref)
        assert retained_execution["error"] is None and retained_provenance["report_manifest_ref"] is None
        assert retained_provenance["computation_status"] == "succeeded" and retained_provenance["report_status"] == "rejected"
        blocked = sandbox.reexport_trusted_report({"execution_ref": execution_ref, "_server_context": context})
        assert not blocked["ok"] and "generic Python" in blocked["error"], blocked

        # Public generic repair regression: the producer executes exactly once,
        # loses only the host manifest write, and repairs from retained bytes.
        repair_context = {"org_id": "1", "user_id": "repair-check", "session_id": "repair-session"}
        repair_store = sandbox.ArtifactStore(Path(tmp) / "repair-runs")
        setattr(sandbox, "ARTIFACTS", repair_store)
        setattr(bridge, "ARTIFACTS", repair_store)
        source_run = repair_store.create_run(repair_context)
        repair_frame = [{"schema": {"fields": [{"name": "x"}]}, "data": {"values": [[1, 2]]}}]
        frame_ref = repair_store.write_json(repair_context, source_run, "grafana-frame", repair_frame)
        plan = {"business_question": "Describe x", "analysis_input_contract": {"validity_rules": []}}
        plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        repair_store.write_json(repair_context, source_run, "query-plan", plan)
        executor_calls = {"compute": 0, "query": 0, "profile": 0}
        figure = {"data": [{"type": "scatter", "x": [1, 2], "y": [4, 3]}], "layout": {}}

        def repair_executor(bundle: str, code: str, seed: int) -> dict:
            executor_calls["compute"] += 1
            return {"execution_id": "repair-fake", "execution_count": 1, "exit_code": 0,
                    "results": [{"display_name": "values.json", "mime": {"application/vnd.plotly.v1+json": json.dumps(figure)}}],
                    "stdout": [], "stderr": [], "error": None,
                    "input_audit": {"input_rows": 2, "valid_rows": 2, "excluded_rows": 0, "rules": []}}

        original_write_json = repair_store.write_json
        manifest_failure = {"armed": False}

        def fail_initial_manifest(context_arg, run_id, name, value):
            if name == "report-manifest" and manifest_failure["armed"]:
                manifest_failure["armed"] = False
                raise OSError("one-shot report persistence failure")
            return original_write_json(context_arg, run_id, name, value)

        repair_store.write_json = fail_initial_manifest
        # Exercise the public router, replacing only the remote executor seam.
        execute_analysis = sandbox.execute_python_analysis
        setattr(sandbox, "execute_python_analysis", lambda args: execute_analysis(args, executor=repair_executor))
        normalize_manifest = sandbox.ml_report_contract.normalize_report_manifest

        def reject_preflight_once(*args, **kwargs):
            raise ValueError("synthetic report preflight rejection")

        sandbox.ml_report_contract.normalize_report_manifest = reject_preflight_once
        try:
            initial = public_call(sandbox, "execute_python_analysis", {"frame_ref": frame_ref, "python_code": "display(df)", "seed": 7}, repair_context)
        finally:
            setattr(sandbox, "execute_python_analysis", execute_analysis)
            sandbox.ml_report_contract.normalize_report_manifest = normalize_manifest
        assert not initial["ok"] and initial["evidence"]["report_status"] == "rejected", initial
        source_execution_ref = initial["evidence"]["execution_ref"]
        source_provenance_ref = initial["evidence"]["provenance_ref"]
        source_execution_before = repair_store.read_json(repair_context, source_execution_ref)
        source_provenance_before = repair_store.read_json(repair_context, source_provenance_ref)
        repaired = public_call(sandbox, "repair_generic_report", {"execution_ref": source_execution_ref}, repair_context)
        assert repaired["ok"], repaired
        fresh_refs = repaired["refs"]
        fresh_execution = repair_store.read_json(repair_context, fresh_refs["execution_ref"])
        fresh_provenance = repair_store.read_json(repair_context, fresh_refs["provenance_ref"])
        assert executor_calls == {"compute": 1, "query": 0, "profile": 0}, executor_calls
        assert source_execution_before == repair_store.read_json(repair_context, source_execution_ref)
        assert source_provenance_before == repair_store.read_json(repair_context, source_provenance_ref)
        assert fresh_refs["execution_ref"] != source_execution_ref and fresh_refs["report_manifest_ref"]
        assert isinstance(fresh_provenance["trusted_ml_contract"], bool) and not fresh_provenance["trusted_ml_contract"]
        assert fresh_provenance["generic_repair_of"] == source_execution_ref
        assert parse_json(fresh_execution["results"][0]["mime"]["application/vnd.plotly.v1+json"], "fresh figure") == figure
        replay = public_call(sandbox, "repair_generic_report", {"execution_ref": source_execution_ref}, repair_context)
        assert replay["ok"] and replay["refs"] == fresh_refs, replay
        assert executor_calls == {"compute": 1, "query": 0, "profile": 0}, executor_calls

        # Bridge must rebind the retained source audit, not trust only the
        # fresh report manifest and its self-consistent digests.
        fresh_execution_path = Path(tmp) / "repair-runs" / fresh_refs["execution_ref"].split("/")[2] / "sandbox-execution.json"
        fresh_execution_payload = fresh_execution_path.read_text()
        tampered_fresh = parse_json(fresh_execution_payload, "tampered fresh execution")
        tampered_fresh.pop("error")
        fresh_execution_path.write_text(json.dumps(tampered_fresh))
        try:
            bridge._verify_generic_repair_lineage(repair_context, fresh_refs["execution_ref"], fresh_provenance)
        except bridge.WorkflowContractError as exc:
            assert "concrete success" in str(exc), exc
        else:
            raise AssertionError("missing execution error must be rejected by the Bridge lineage gate")
        rejected_missing_error = public_call(bridge, "prepare_ml_report", {"report_manifest_ref": fresh_refs["report_manifest_ref"]}, repair_context)
        assert not rejected_missing_error["ok"] and "execution is unavailable" in rejected_missing_error["error"], rejected_missing_error
        fresh_execution_path.write_text(fresh_execution_payload)

        source_path = Path(tmp) / "repair-runs" / source_execution_ref.split("/")[2] / "sandbox-execution.json"
        source_payload = source_path.read_text()
        tampered_source_audit = parse_json(source_payload, "tampered source audit")
        tampered_source_audit["input_audit"]["valid_rows"] = 1
        source_path.write_text(json.dumps(tampered_source_audit))
        rejected_source_audit_prepare = public_call(bridge, "prepare_ml_report", {"report_manifest_ref": fresh_refs["report_manifest_ref"]}, repair_context)
        assert not rejected_source_audit_prepare["ok"] and "input audit" in rejected_source_audit_prepare["error"], rejected_source_audit_prepare
        source_path.write_text(source_payload)

        # A late fresh-provenance write failure leaves the reserved repair
        # operation indeterminate; replay must not invoke the executor again.
        manifest_failure["armed"] = True
        late_initial = sandbox.execute_python_analysis({"frame_ref": frame_ref, "python_code": "display(df)", "seed": 8, "presentation_mode": "plotly", "_server_context": repair_context}, executor=repair_executor)
        assert not late_initial["ok"] and late_initial["evidence"]["report_status"] == "rejected", late_initial
        late_source_execution_ref = late_initial["evidence"]["execution_ref"]
        late_source_before = repair_store.read_json(repair_context, late_source_execution_ref)
        delegate_write_json = repair_store.write_json
        late_failure = {"armed": True}

        def fail_late_repair_provenance(context_arg, run_id, name, value):
            if late_failure["armed"] and name == "sandbox-provenance" and run_id != late_source_execution_ref.split("/")[2]:
                late_failure["armed"] = False
                raise OSError("one-shot fresh provenance persistence failure")
            return delegate_write_json(context_arg, run_id, name, value)

        repair_store.write_json = fail_late_repair_provenance
        late_repair = public_call(sandbox, "repair_generic_report", {"execution_ref": late_source_execution_ref}, repair_context)
        assert not late_repair["ok"] and late_repair["evidence"]["effect_outcome"] == "indeterminate", late_repair
        assert isinstance(late_repair["evidence"].get("operation_id"), str)
        assert late_repair["evidence"]["source_execution_ref"] == late_source_execution_ref
        late_replay = public_call(sandbox, "repair_generic_report", {"execution_ref": late_source_execution_ref}, repair_context)
        assert not late_replay["ok"] and late_replay["evidence"]["operation_id"] == late_repair["evidence"]["operation_id"], late_replay
        assert executor_calls["compute"] == 2, executor_calls
        reconciled_late = repair_store.reconcile_operation(repair_context, late_repair["evidence"]["operation_id"])
        assert reconciled_late["status"] == "indeterminate" and not reconciled_late["redispatch_allowed"], reconciled_late
        assert late_source_before == repair_store.read_json(repair_context, late_source_execution_ref)
        repair_store.write_json = original_write_json

        prepared = public_call(bridge, "prepare_ml_report", {"report_manifest_ref": fresh_refs["report_manifest_ref"]}, repair_context)
        assert prepared["ok"], prepared
        assert prepared["report_context"]["analysis_coverage"]["status"] == "not_assessed", prepared
        assert prepared["report_context"]["analysis_coverage"]["business_question"]["value"] == "Describe x", prepared
        artifact = prepared["report_context"]["artifacts"][0]
        inspected = public_call(bridge, "inspect_report_artifacts", {"report_context_ref": prepared["refs"]["report_context_ref"], "artifact_ids": [artifact["artifact_id"]], "mode": "spec"}, repair_context)
        assert inspected["ok"], inspected
        view_id = artifact["figure_spec"]["views"][0]["view_id"]
        evidence = [{"fact_ref": "facts.rows", "format": "integer"}]
        synthesis = {"format": "ask-o11y-report-synthesis-v1", "report_title": "Generic repair", "thesis": "The retained values are available", "thesis_evidence": evidence, "sections": [{"section_id": "results", "title": "Results", "purpose": "Describe retained evidence", "collapsed": False, "narrative_blocks": [], "panels": [{"artifact_id": artifact["artifact_id"], "view_ids": [view_id], "view_narratives": [{"view_id": view_id, "headline": "Retained values", "data_observation": "Values are retained", "visual_observation": None, "interpretation": "Observed only", "limitation": "Generic output is not trusted ML", "next_step": "Review evidence", "evidence": evidence}], "headline": "Retained values", "observation": "Values are retained", "interpretation": "Observed only", "cross_chart_context": "Read with the input", "limitation": "Generic output is not trusted ML", "next_step": "Review evidence", "evidence": evidence, "priority": "primary", "preferred_width": "full"}]}]}
        source_json = parse_json(source_payload, "source audit compose")
        source_path.write_text(json.dumps({**source_json, "input_audit": {**source_json["input_audit"], "valid_rows": 1}}))
        rejected_source_audit_compose = public_call(bridge, "compose_ml_dashboard", {"report_context_ref": prepared["refs"]["report_context_ref"], "inspection_refs": [inspected["refs"]["inspection_ref"]], "synthesis": synthesis, "uid": "repair-check-audit-tampered", "title": "Repair check"}, repair_context)
        assert not rejected_source_audit_compose["ok"] and "input audit" in rejected_source_audit_compose["error"], rejected_source_audit_compose
        source_path.write_text(source_payload)

        fresh_execution_payload = fresh_execution_path.read_text()
        tampered_fresh = parse_json(fresh_execution_payload, "tampered fresh figure")
        tampered_fresh["results"][0]["mime"]["application/vnd.plotly.v1+json"] = json.dumps({"data": [{"type": "scatter", "x": [8], "y": [8]}], "layout": {}})
        fresh_execution_path.write_text(json.dumps(tampered_fresh))
        rejected_prepare_tamper = public_call(bridge, "prepare_ml_report", {"report_manifest_ref": fresh_refs["report_manifest_ref"]}, repair_context)
        assert not rejected_prepare_tamper["ok"] and "manifest" in rejected_prepare_tamper["error"], rejected_prepare_tamper
        fresh_execution_path.write_text(fresh_execution_payload)
        fresh_provenance_path = Path(tmp) / "repair-runs" / fresh_refs["provenance_ref"].split("/")[2] / "sandbox-provenance.json"
        fresh_provenance_payload = fresh_provenance_path.read_text()
        tampered_fresh_provenance = parse_json(fresh_provenance_payload, "tampered fresh provenance")
        tampered_fresh_provenance["generic_repair_source_results_sha256"] = "0" * 64
        fresh_provenance_path.write_text(json.dumps(tampered_fresh_provenance))
        rejected_compose_tamper = public_call(bridge, "compose_ml_dashboard", {"report_context_ref": prepared["refs"]["report_context_ref"], "inspection_refs": [inspected["refs"]["inspection_ref"]], "synthesis": synthesis, "uid": "repair-check-tampered", "title": "Repair check"}, repair_context)
        assert not rejected_compose_tamper["ok"] and "digest" in rejected_compose_tamper["error"], rejected_compose_tamper
        fresh_provenance_path.write_text(fresh_provenance_payload)

        # Co-tampering the fresh source, its digest, and canonical manifest
        # must still fail because the original host receipt is immutable proof.
        fresh_execution_payload = fresh_execution_path.read_text()
        co_tampered_execution = parse_json(fresh_execution_payload, "co-tampered execution")
        source_result = next(item for item in co_tampered_execution["results"] if item.get("display_name") == "report-source.json")
        co_tampered_source = parse_json(source_result["mime"]["application/json"], "co-tampered source")
        co_tampered_source["facts"]["rows"]["value"] = 99
        source_result["mime"]["application/json"] = json.dumps(co_tampered_source, ensure_ascii=False, separators=(",", ":"))
        fresh_execution_path.write_text(json.dumps(co_tampered_execution))
        co_tampered_digest = hashlib.sha256(json.dumps(co_tampered_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        co_tampered_provenance = parse_json(fresh_provenance_payload, "co-tampered provenance")
        co_tampered_provenance["generic_repair_report_source_sha256"] = co_tampered_digest
        fresh_provenance_path.write_text(json.dumps(co_tampered_provenance))
        co_tampered_manifest = bridge.ml_report_contract.normalize_report_manifest(execution_ref=fresh_refs["execution_ref"], results=co_tampered_execution["results"])
        manifest_path = Path(tmp) / "repair-runs" / fresh_refs["report_manifest_ref"].split("/")[2] / "report-manifest.json"
        manifest_payload = manifest_path.read_text()
        manifest_path.write_text(json.dumps(co_tampered_manifest))
        rejected_co_tamper = public_call(bridge, "prepare_ml_report", {"report_manifest_ref": fresh_refs["report_manifest_ref"]}, repair_context)
        assert not rejected_co_tamper["ok"] and "original host receipt" in rejected_co_tamper["error"], rejected_co_tamper
        fresh_execution_path.write_text(fresh_execution_payload)
        fresh_provenance_path.write_text(fresh_provenance_payload)
        manifest_path.write_text(manifest_payload)

        composed = public_call(bridge, "compose_ml_dashboard", {"report_context_ref": prepared["refs"]["report_context_ref"], "inspection_refs": [inspected["refs"]["inspection_ref"]], "synthesis": synthesis, "uid": "repair-check", "title": "Repair check"}, repair_context)
        assert composed["ok"], composed
        partial = public_call(bridge, "compose_ml_dashboard", {"report_context_ref": prepared["refs"]["report_context_ref"], "inspection_refs": [inspected["refs"]["inspection_ref"]], "synthesis": synthesis, "uid": "repair-check-partial", "title": "Repair check", "delivery_status": "partial"}, repair_context)
        assert partial["ok"], partial
        if fixture_path := os.environ.get("Y5_DELIVERY_FIXTURE_OUT"):
            # Unmodified public responses, consumed by the real Go loop replay.
            def trace_for(values):
                return [next(item for item in PUBLIC_TRACE if item["result"] is value) for value in values]
            Path(fixture_path).write_text(json.dumps({
                "cases": [
                    {"name": "generic_standard", "question": "Describe x", "status": "not_assessed", "calls": trace_for([initial, repaired, prepared, inspected, composed])},
                    {"name": "generic_partial", "question": "Describe x", "status": "partial", "calls": trace_for([initial, repaired, prepared, inspected, partial])},
                    {"name": "retained_generic", "question": "Describe x", "status": "not_assessed", "calls": trace_for([prepared, inspected, composed])},
                ], "original_compute_calls": executor_calls["compute"], "repair_added_compute_calls": 0,
                "originals_unchanged": source_execution_before == repair_store.read_json(repair_context, source_execution_ref) and source_provenance_before == repair_store.read_json(repair_context, source_provenance_ref),
            }, ensure_ascii=False, indent=2))

        # Missing, foreign, indeterminate, and tampered retained evidence are
        # rejected before any fresh report version is accepted.
        source_path = Path(tmp) / "repair-runs" / source_execution_ref.split("/")[2] / "sandbox-execution.json"
        provenance_path = Path(tmp) / "repair-runs" / source_provenance_ref.split("/")[2] / "sandbox-provenance.json"
        source_payload = source_path.read_text()
        provenance_payload = provenance_path.read_text()
        missing_digest = parse_json(provenance_payload, "missing digest")
        missing_digest.pop("captured_results_sha256", None)
        provenance_path.write_text(json.dumps(missing_digest))
        rejected_missing = public_call(sandbox, "repair_generic_report", {"execution_ref": source_execution_ref}, repair_context)
        assert not rejected_missing["ok"] and "digest" in rejected_missing["error"], rejected_missing
        provenance_path.write_text(provenance_payload)
        tampered = parse_json(source_payload, "tampered retained execution")
        tampered["results"][0]["mime"]["application/vnd.plotly.v1+json"] = json.dumps({"data": [{"type": "scatter", "x": [9], "y": [9]}], "layout": {}})
        source_path.write_text(json.dumps(tampered))
        rejected_tamper = public_call(sandbox, "repair_generic_report", {"execution_ref": source_execution_ref}, repair_context)
        assert not rejected_tamper["ok"] and "digest" in rejected_tamper["error"], rejected_tamper
        source_path.write_text(source_payload)
        indeterminate = parse_json(provenance_payload, "indeterminate provenance")
        indeterminate["computation_status"] = "indeterminate"
        provenance_path.write_text(json.dumps(indeterminate))
        rejected_indeterminate = public_call(sandbox, "repair_generic_report", {"execution_ref": source_execution_ref}, repair_context)
        assert not rejected_indeterminate["ok"] and "succeeded" in rejected_indeterminate["error"], rejected_indeterminate
        provenance_path.write_text(provenance_payload)
        foreign = public_call(sandbox, "repair_generic_report", {"execution_ref": source_execution_ref}, {"org_id": "1", "user_id": "repair-check", "session_id": "other-session"})
        assert not foreign["ok"], foreign

    print("ok: Y5 generic public repair retains outputs, avoids redispatch, and bridge verifies fresh lineage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
