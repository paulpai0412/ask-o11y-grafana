#!/usr/bin/env python3
"""Public report MCP regression; host-owned artifact fixtures, no live approval/ML run."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OWNER = {"org_id": "1", "user_id": "coverage-check", "session_id": "coverage-session"}


def call(bridge, name: str, arguments: dict, context: dict = OWNER) -> dict:
    reply = bridge.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": name, "arguments": {**arguments, "_server_context": context},
    }})
    try:
        return json.loads(reply["result"]["content"][0]["text"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AssertionError("MCP response is not a JSON tool result") from exc


def report(bridge, provenance: dict, facts: dict | None = None, *, purpose: str = "Compare model errors") -> str:
    # Seed retained host artifacts at the storage boundary, not an invented
    # validator response. These fixtures do not prove human approval or ML math.
    source_run = bridge.ARTIFACTS.create_run(OWNER)
    frame = [{"schema": {"fields": [{"name": "response"}]}, "data": {"values": [[1, 2]]}}]
    frame_ref = bridge.ARTIFACTS.write_json(OWNER, source_run, "grafana-frame", frame)
    plan = {"business_question": provenance.get("business_question"), "analysis_contract": provenance.get("analysis_contract"), "analysis_input_contract": {"validity_rules": []}}
    plan = {key: value for key, value in plan.items() if value is not None}
    plan["plan_sha256"] = __import__("hashlib").sha256(json.dumps({key: value for key, value in plan.items() if key != "plan_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    bridge.ARTIFACTS.write_json(OWNER, source_run, "query-plan", plan)
    run_id = bridge.ARTIFACTS.create_run(OWNER)
    code = "trusted fixture code"
    code_sha256 = __import__("hashlib").sha256(code.encode("utf-8")).hexdigest()
    code_ref = bridge.ARTIFACTS.write_json(OWNER, run_id, "sandbox-code", {"sha256": code_sha256, "source": code})
    provenance.update({"input_frame_ref": frame_ref, "input_frame_sha256": __import__("hashlib").sha256(json.dumps(frame[0], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(), "plan_sha256": plan["plan_sha256"], "code_sha256": code_sha256, "code_ref": code_ref})
    source = {
        "format": "ask-o11y-report-source-v1", "purpose": purpose,
        "conclusion": "Authored prose is not proof of completed comparison",
        "facts": {"rows": {"kind": "number", "label": "Input rows", "value": 120}},
        "artifacts": [{"artifact_id": "overview", "fact_refs": ["rows"], "plotly_output_name": "overview.json"}],
    }
    source["facts"].update({name: {"kind": "number", "label": name, "value": value} for name, value in (facts or {}).items()})
    results = [
        {"display_name": "report-source.json", "mime": {"application/json": json.dumps(source)}},
        {"display_name": "overview.json", "mime": {"application/vnd.plotly.v1+json": json.dumps({
            "data": [{"type": "bar", "x": ["available"], "y": [120]}], "layout": {},
        })}},
    ]
    execution_ref = bridge.ARTIFACTS.write_json(OWNER, run_id, "sandbox-execution", {"results": results, "error": None})
    manifest = bridge.ml_report_contract.normalize_report_manifest(execution_ref=execution_ref, results=results)
    manifest_ref = bridge.ARTIFACTS.write_json(OWNER, run_id, "report-manifest", manifest)
    bridge.ARTIFACTS.write_json(OWNER, run_id, "sandbox-provenance", {
        **provenance, "report_manifest_ref": manifest_ref,
        "computation_status": provenance.get("computation_status", "succeeded"),
        "report_status": provenance.get("report_status", "accepted"),
    })
    return manifest_ref


def synthesis() -> dict:
    evidence = [{"fact_ref": "facts.rows", "format": "integer"}]
    return {
        "format": "ask-o11y-report-synthesis-v1", "report_title": "Comparison report",
        "thesis": "The requested comparison is complete", "thesis_evidence": evidence,
        "sections": [{
            "section_id": "results", "title": "Results", "purpose": "Answer the question",
            # Minimal prose must still obey every comparison/uncertainty/lineage gate below.
            "panels": [{
                "artifact_id": "overview", "view_ids": ["figure"],
                "headline": "Input overview", "observation": "Rows are available", "interpretation": "Describe the data",
                "limitation": "Not causal evidence", "evidence": evidence,
            }],
        }],
    }


def composition_args(bridge, prepared: dict) -> dict:
    report_context_ref = prepared["refs"]["report_context_ref"]
    inspected = call(bridge, "inspect_report_artifacts", {
        "report_context_ref": report_context_ref, "artifact_ids": ["overview"], "mode": "spec",
    })
    assert inspected["ok"], inspected
    return {
        "report_context_ref": report_context_ref, "inspection_refs": [inspected["refs"]["inspection_ref"]],
        "synthesis": synthesis(), "uid": "coverage-check", "title": "Coverage check",
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="analysis-coverage-") as tmp:
        os.environ["ANALYSIS_ARTIFACT_ROOT"] = tmp
        spec = importlib.util.spec_from_file_location("coverage_bridge", ROOT / "artifact-bridge-mcp/server.py")
        assert spec and spec.loader
        bridge = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = bridge
        spec.loader.exec_module(bridge)
        input_run = bridge.ARTIFACTS.create_run(OWNER)
        planned = {
            "analysis_contract": {"task_kind": "regression", "algorithms": ["ridge"], "target": "response"},
            "business_question": "Compare model errors", "plan_sha256": "a" * 64, "input_frame_sha256": "b" * 64,
            "input_frame_ref": f"artifact://{input_run}/grafana-frame",
            "executor_kind": "profile_dataset", "trusted_ml_contract": False,
        }
        profile_ref = report(bridge, planned, purpose="Generic profile")
        prepared = call(bridge, "prepare_ml_report", {"report_manifest_ref": profile_ref})
        assert prepared["ok"], prepared
        coverage = prepared["report_context"].get("analysis_coverage")
        assert coverage is not None, "report preparation hides the gap between planned ML and profiling"
        assert coverage["status"] == "incomplete", coverage
        assert coverage["business_question"]["value"] == "Compare model errors", coverage
        assert coverage["gaps"][0]["code"] == "PLANNED_ANALYSIS_NOT_EXECUTED", coverage
        assert coverage["requirement"]["task_kind"] == "regression", coverage
        assert isinstance(coverage["automatic_retry"], bool), coverage
        assert coverage["actions_granted"] == [] and not coverage["automatic_retry"], coverage
        print("PASS: profile cannot stand in for the plan-bound ML execution; actionable gap, no new authority")
        compose_args = composition_args(bridge, prepared)
        partial_profile = call(bridge, "compose_ml_dashboard", {**compose_args, "delivery_status": "partial", "output_mode": "full"})
        assert partial_profile["ok"], partial_profile
        profile_intro = partial_profile["dashboard"]["panels"][0]
        assert profile_intro["askO11yBusinessQuestion"] == "Compare model errors", profile_intro
        assert "Generic profile" not in profile_intro["options"]["content"].split("Question:", 1)[-1], profile_intro
        assert "Partial report" in profile_intro["options"]["content"], profile_intro
        composed = call(bridge, "compose_ml_dashboard", compose_args)
        assert not composed["ok"], "profile-only evidence was composed as a completed planned comparison"
        assert composed["evidence"]["repair_kind"] == "missing_analysis_evidence", composed
        assert composed["evidence"]["analysis_coverage"]["status"] == "incomplete", composed
        assert composed["recoverable"] and "existing approval" in composed["instruction"], composed
        assert "dashboard_ref" not in composed.get("refs", {}), composed
        print("PASS: compose blocks missing analysis evidence and returns bounded LLM repair guidance")
        trusted = {**planned, "executor_kind": "execute_ml_contract", "trusted_ml_contract": True}
        facts = {"selected_metrics_mae": 12, "baseline_metrics_holdout_mae": 9}
        count_only = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, trusted)})
        assert count_only["ok"], count_only
        checked = count_only["report_context"]["analysis_coverage"]
        assert checked["status"] == "incomplete", "trusted executor identity alone was mistaken for comparison evidence"
        assert checked["gaps"][0]["code"] == "COMPARISON_FACTS_UNAVAILABLE", checked
        print("PASS: trusted execution without exported comparison values is not complete")
        indeterminate = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {**trusted, "computation_status": "indeterminate"}, facts)})
        assert indeterminate["ok"] and indeterminate["report_context"]["analysis_coverage"]["status"] == "not_assessed", indeterminate
        missing_error_manifest = report(bridge, trusted, facts)
        missing_error = bridge.ARTIFACTS.read_json(OWNER, missing_error_manifest)
        missing_error_run, _ = bridge.parse_artifact_ref(missing_error["execution_ref"])
        missing_error_path = Path(tmp) / missing_error_run / "sandbox-execution.json"
        missing_error_payload = bridge.ARTIFACTS.read_json(OWNER, missing_error["execution_ref"])
        missing_error_payload.pop("error", None)
        missing_error_path.write_text(json.dumps(missing_error_payload), encoding="utf-8")
        refused_missing_error = call(bridge, "prepare_ml_report", {"report_manifest_ref": missing_error_manifest})
        assert not refused_missing_error["ok"], refused_missing_error
        print("PASS: indeterminate and missing-error execution states fail closed before evidence promotion")
        missing_facts = call(bridge, "compose_ml_dashboard", composition_args(bridge, count_only))
        assert not missing_facts["ok"], missing_facts
        assert missing_facts["evidence"]["repair_kind"] == "report_evidence", "missing exported facts incorrectly requested another analysis"
        assert "do not rerun analysis" in missing_facts["instruction"], missing_facts
        print("PASS: missing exported facts request evidence repair, not repeated computation")
        # Existing regression schema: lower MAE is better, but worse-than-baseline
        # evidence is still a valid outcome. No success threshold is imposed.
        verified = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, trusted, facts)})
        assert verified["ok"], verified
        verified_coverage = verified["report_context"]["analysis_coverage"]
        assert verified_coverage["status"] == "evidence_available", verified_coverage
        assert verified_coverage["business_question_status"] == "assessed", verified_coverage
        assert verified_coverage["business_question"]["value"] == "Compare model errors", verified_coverage
        assert verified_coverage["business_question"]["plan_sha256"] == planned["plan_sha256"], verified_coverage
        assert verified_coverage["business_question"]["input_frame_sha256"] == planned["input_frame_sha256"], verified_coverage
        assert verified_coverage["business_question"]["report_manifest_ref"] == verified["refs"]["report_manifest_ref"], verified_coverage
        assert verified_coverage["evidence_fact_refs"] == ["facts.selected_metrics_mae", "facts.baseline_metrics_holdout_mae"], verified_coverage
        missing_question_provenance = {key: value for key, value in trusted.items() if key != "business_question"}
        missing_question = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, missing_question_provenance, facts)})
        assert missing_question["ok"], missing_question
        assert missing_question["report_context"]["analysis_coverage"]["status"] == "incomplete", missing_question
        assert missing_question["report_context"]["analysis_coverage"]["gaps"][0]["code"] == "BUSINESS_QUESTION_NOT_RECORDED", missing_question
        assert not call(bridge, "compose_ml_dashboard", composition_args(bridge, missing_question))["ok"]
        mismatched_question = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {**trusted, "business_question": "Different question"}, facts)})
        assert not mismatched_question["ok"] and not mismatched_question["recoverable"], mismatched_question
        assert verified["report_context"]["facts"]["facts.selected_metrics_mae"]["value"] == 12
        comparison_args = composition_args(bridge, verified)
        uncited = call(bridge, "compose_ml_dashboard", comparison_args)
        assert not uncited["ok"], "available comparison facts were omitted from the purported completed report"
        assert uncited["evidence"]["repair_kind"] == "report_synthesis", uncited
        assert set(uncited["evidence"]["missing_fact_refs"]) == set(verified_coverage["evidence_fact_refs"]), uncited
        comparison_args["synthesis"]["thesis_evidence"] = [
            {"fact_ref": ref, "format": "number_2"} for ref in verified_coverage["evidence_fact_refs"]
        ]
        accepted = call(bridge, "compose_ml_dashboard", comparison_args)
        assert accepted["ok"] and accepted["refs"]["dashboard_ref"], accepted
        assert accepted["evidence"]["analysis_coverage"]["status"] == "evidence_available", accepted
        direct = call(bridge, "inspect_report_artifacts", {"report_manifest_ref": verified["refs"]["report_manifest_ref"]})
        assert direct["ok"] and direct["evidence"]["remaining_artifact_count"] == 0, direct
        cursor_comparison = {key: value for key, value in comparison_args.items() if key not in {"report_context_ref", "inspection_refs"}}
        cursor_comparison["inspection_ref"] = direct["refs"]["inspection_ref"]
        cursor_success = call(bridge, "compose_ml_dashboard", cursor_comparison)
        assert cursor_success["ok"] and cursor_success["evidence"]["analysis_coverage"] == accepted["evidence"]["analysis_coverage"], cursor_success
        cursor_missing = call(bridge, "compose_ml_dashboard", {**cursor_comparison, "synthesis": synthesis()})
        assert not cursor_missing["ok"] and cursor_missing["evidence"]["missing_fact_refs"], cursor_missing
        regression_uncertainty_facts = {
            "selected_metrics_mae": 12.0, "selected_metrics_mae_interval_0": 10.0, "selected_metrics_mae_interval_1": 14.0,
            "baseline_metrics_holdout_mae": 9.0, "baseline_metrics_holdout_mae_interval_0": 8.0, "baseline_metrics_holdout_mae_interval_1": 10.0,
            "process_uncertainty_confidence": 0.95, "process_uncertainty_samples": 1000,
        }
        regression_uncertainty = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, trusted, regression_uncertainty_facts)})
        assert regression_uncertainty["ok"], regression_uncertainty
        regression_coverage = regression_uncertainty["report_context"]["analysis_coverage"]
        assert regression_coverage["uncertainty"]["status"] == "available", regression_coverage
        regression_args = composition_args(bridge, regression_uncertainty)
        regression_args["synthesis"]["thesis_evidence"] = [{"fact_ref": ref, "format": "number_2"} for ref in regression_coverage["evidence_fact_refs"]]
        regression_missing = call(bridge, "compose_ml_dashboard", regression_args)
        assert not regression_missing["ok"] and regression_missing["evidence"]["missing_uncertainty_fact_refs"], regression_missing
        cursor_regression = {key: value for key, value in regression_args.items() if key not in {"report_context_ref", "inspection_refs"}}
        cursor_regression["inspection_ref"] = regression_args["inspection_refs"][0]
        cursor_missing = call(bridge, "compose_ml_dashboard", cursor_regression)
        assert not cursor_missing["ok"] and cursor_missing["evidence"]["missing_uncertainty_fact_refs"], cursor_missing
        regression_args["synthesis"]["thesis_evidence"].extend({"fact_ref": ref, "format": "number_2"} for ref in regression_coverage["uncertainty"]["fact_refs"])
        assert call(bridge, "compose_ml_dashboard", regression_args)["ok"]
        assert call(bridge, "compose_ml_dashboard", cursor_regression)["ok"]
        generic = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **planned, "executor_kind": "execute_python_analysis",
        }, facts)})
        assert generic["ok"] and generic["report_context"]["analysis_coverage"]["status"] == "incomplete", generic
        assert not call(bridge, "compose_ml_dashboard", composition_args(bridge, generic))["ok"]
        descriptive = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            "executor_kind": "profile_dataset", "trusted_ml_contract": False,
        })})
        assert descriptive["ok"], descriptive
        assert descriptive["report_context"]["analysis_coverage"]["status"] == "not_assessed", descriptive
        descriptive_args = composition_args(bridge, descriptive)
        descriptive_args["synthesis"]["thesis"] = "This report describes the available inputs"
        assert call(bridge, "compose_ml_dashboard", descriptive_args)["ok"]
        assert not call(bridge, "prepare_ml_report", {"report_manifest_ref": profile_ref, "completed": True})["ok"]
        assert not call(bridge, "compose_ml_dashboard", {**compose_args, "analysis_coverage": {"status": "evidence_available"}})["ok"]
        print("PASS: real report contract values close the bounded gap; generic/self-declared success does not; descriptive reports remain usable")
        denied = call(bridge, "compose_ml_dashboard", compose_args, {**OWNER, "user_id": "other-user"})
        assert not denied["ok"], denied
        assert not denied["recoverable"], "authorization failure incorrectly invited LLM retry"
        print("PASS: authorization failures stop rather than request synthesis repair")
        classification_facts = {"results_selected_accuracy": 0.4, "results_baseline_accuracy": 0.9}
        roc_contract = {**trusted, "analysis_contract": {"task_kind": "binary_classification", "objective": "roc_auc"}, "planned_objective": "roc_auc"}
        wrong_metric = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, roc_contract, classification_facts)})
        assert wrong_metric["ok"], wrong_metric
        assert wrong_metric["report_context"]["analysis_coverage"]["status"] == "incomplete", "accuracy was used to satisfy the requested ROC AUC comparison"
        print("PASS: a different metric cannot satisfy the retained objective")
        # Real bounded local computation feeding the public report seam. The
        # retained authority fixtures still do not stand in for human approval.
        sys.path.insert(0, str(ROOT / "sandbox-analysis-mcp"))
        import pandas as pd
        autoresearch_module = bridge.load_module("coverage_autoresearch", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
        y_train = pd.Series([0, 0, 0, 1] * 20)
        y_hold = pd.Series([0, 0, 0, 1] * 10)
        comparison = autoresearch_module.run_multi_model_comparison(
            pd.DataFrame({"signal": y_train.astype(float)}), y_train,
            pd.DataFrame({"signal": y_hold.astype(float)}), y_hold,
            kinds=["logistic_regression"], objective="roc_auc", n_iter=1, cv_folds=2,
        )
        assert comparison["uncertainty"]["method"] == "nonparametric_bootstrap_fixed_holdout_predictions", comparison
        assert comparison["uncertainty"]["confidence"] == 0.95 and comparison["uncertainty"]["samples"] == 1000, comparison
        assert "model_selection" in comparison["uncertainty"]["limitations"], comparison
        for metrics in (comparison["best_result"]["metrics"], comparison["baseline_metrics"]):
            for metric in ("accuracy", "roc_auc", "pr_auc"):
                interval = metrics.get(f"{metric}_interval")
                point = metrics[metric]
                assert isinstance(interval, list) and len(interval) == 2, f"{metric}: {metrics}"
                assert all(isinstance(value, float) for value in interval), f"{metric}: {interval}"
                assert interval[0] <= interval[1] and isinstance(point, float), f"{metric}: {point} not in {interval}"
        computed_facts = {
            f"results_{side}_{name}_{index}" if index is not None else f"results_{side}_{name}": item
            for side, metrics in (("selected", comparison["best_result"]["metrics"]), ("baseline", comparison.get("baseline_metrics", {})))
            for name, raw_value in metrics.items()
            for index, item in (enumerate(raw_value) if isinstance(raw_value, list) else [(None, raw_value)])
            if isinstance(item, (int, float)) and not isinstance(item, bool)
        }
        computed = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, roc_contract, computed_facts)})
        assert computed["ok"], computed
        assert computed["report_context"]["analysis_coverage"]["status"] == "evidence_available", "trusted comparison omitted its objective-matched baseline evidence"
        uncertainty_facts = {**computed_facts, "process_uncertainty_confidence": comparison["uncertainty"]["confidence"], "process_uncertainty_samples": comparison["uncertainty"]["samples"]}
        uncertainty_report = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, roc_contract, uncertainty_facts)})
        assert uncertainty_report["ok"], uncertainty_report
        uncertainty_args = composition_args(bridge, uncertainty_report)
        uncertainty_args["synthesis"]["thesis_evidence"] = [
            {"fact_ref": ref, "format": "number_2"} for ref in uncertainty_report["report_context"]["analysis_coverage"]["evidence_fact_refs"]
        ]
        missing_uncertainty = call(bridge, "compose_ml_dashboard", uncertainty_args)
        assert not missing_uncertainty["ok"], "available uncertainty intervals may be omitted from synthesis"
        assert missing_uncertainty["evidence"]["repair_kind"] == "report_synthesis", missing_uncertainty
        assert missing_uncertainty["evidence"]["missing_uncertainty_fact_refs"], missing_uncertainty
        uncertainty_args["synthesis"]["thesis_evidence"].extend({"fact_ref": ref, "format": "number_2"} for ref in uncertainty_report["report_context"]["analysis_coverage"]["uncertainty"]["fact_refs"])
        assert call(bridge, "compose_ml_dashboard", uncertainty_args)["ok"]
        catalog = computed["report_context"]["facts"]
        assert catalog["facts.results_baseline_accuracy"]["value"] == 0.75
        assert catalog["facts.results_baseline_roc_auc"]["value"] == 0.5
        assert catalog["facts.results_baseline_pr_auc"]["value"] == 0.25
        assert catalog["facts.results_selected_roc_auc_interval_0"]["value"] <= catalog["facts.results_selected_roc_auc"]["value"] <= catalog["facts.results_selected_roc_auc_interval_1"]["value"], catalog
        presentation = bridge.load_module("coverage_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")
        source = presentation.build_report_source({
            "data": {"rows": len(y_hold)}, "process": {"uncertainty": comparison["uncertainty"]},
            "results": {"selected": comparison["best_result"]["metrics"], "baseline": comparison["baseline_metrics"]},
            "guards": {"selection_holdout_separation": True, "preprocessing_fit_scope_training_only": True, "independence_assumption_verified": False, "causal_identification_established": False, "multiplicity_adjusted_inference": False},
            "artifacts": [],
        })
        assert {"results_selected_roc_auc_interval_0", "results_selected_roc_auc_interval_1", "results_baseline_pr_auc_interval_0", "process_uncertainty_confidence", "process_uncertainty_samples", "selection_holdout_separation", "preprocessing_fit_scope_training_only", "independence_assumption_verified", "causal_identification_established", "multiplicity_adjusted_inference"} <= set(source["facts"]), source
        assert "fixed-holdout nonparametric bootstrap" in source["conclusion"], source
        malformed_source_manifest = {
            "data": {"rows": len(y_hold)}, "process": {"uncertainty": {**comparison["uncertainty"], "confidence": "95%"}},
            "results": {"selected": comparison["best_result"]["metrics"], "baseline": comparison["baseline_metrics"]},
            "artifacts": [],
        }
        try:
            presentation.build_report_source(malformed_source_manifest)
        except ValueError as exc:
            assert "uncertainty metadata" in str(exc), str(exc)
        else:
            raise AssertionError("malformed uncertainty metadata was accepted")
        for invalid_samples in (99, 10_001, True):
            try:
                autoresearch_module.run_multi_model_comparison(
                    pd.DataFrame({"signal": y_train.astype(float)}), y_train,
                    pd.DataFrame({"signal": y_hold.astype(float)}), y_hold,
                    kinds=["logistic_regression"], objective="roc_auc", n_iter=1, cv_folds=2,
                    bootstrap_samples=invalid_samples,
                )
            except ValueError as exc:
                assert "bootstrap_samples" in str(exc), str(exc)
            else:
                raise AssertionError("invalid bootstrap bound was accepted")
        print("PASS: bootstrap intervals survive source/manifest/catalog and bounds are enforced")
        sandbox_module = bridge.load_module("coverage_sandbox", ROOT / "sandbox-analysis-mcp/server.py")
        input_contract = {"execution_template": "ask_o11y_logistic_regression_v1", "preprocessing_fit_scope": "training_only", "autoresearch": {"objective": "accuracy", "search_budget": 1}}
        analysis = {"task_kind": "binary_classification", "kind": "logistic_regression", "split": {"kind": "stratified_holdout", "test_fraction": 0.25}}
        # Seed the retained packet from the actual semantic validation result;
        # assertions stay at the report API, not on a mocked validator response.
        semantic = sandbox_module.validate_ml_execution_contract(input_contract, analysis)
        effective = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **trusted, "analysis_contract": analysis, "planned_objective": semantic.get("planned_objective"),
        }, classification_facts)})
        assert effective["ok"] and effective["report_context"]["analysis_coverage"]["status"] == "evidence_available", "effective plan objective was not retained for reporting"
        assert effective["report_context"]["analysis_coverage"]["requirement"]["objective"] == "accuracy", effective
        print("PASS: effective objective, including plan-side configuration, survives to the report")
        malformed_objective = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **trusted, "analysis_contract": {"task_kind": "binary_classification", "objective": "accuracy"}, "planned_objective": False,
        }, classification_facts)})
        assert not malformed_objective["ok"], "malformed retained objective silently fell back to the declared metric"
        print("PASS: invalid recorded objectives do not silently fall back")
        objective_args = composition_args(bridge, computed)
        objective_args["synthesis"]["thesis_evidence"] = [
            {"fact_ref": "facts.results_selected_accuracy", "format": "percent_1"},
            {"fact_ref": "facts.results_baseline_accuracy", "format": "percent_1"},
        ]
        wrong_citations = call(bridge, "compose_ml_dashboard", objective_args)
        assert not wrong_citations["ok"], wrong_citations
        assert set(wrong_citations["evidence"]["missing_fact_refs"]) == {"facts.results_selected_roc_auc", "facts.results_baseline_roc_auc"}, wrong_citations
        objective_args["synthesis"]["thesis_evidence"] = [
            {"fact_ref": ref, "format": "number_2"} for ref in computed["report_context"]["analysis_coverage"]["evidence_fact_refs"]
        ]
        assert call(bridge, "compose_ml_dashboard", objective_args)["ok"]
        pr = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **trusted, "analysis_contract": {"task_kind": "binary_classification", "objective": "pr_auc"}, "planned_objective": "pr_auc",
        }, computed_facts)})
        assert pr["ok"] and pr["report_context"]["analysis_coverage"]["evidence_fact_refs"] == ["facts.results_selected_pr_auc", "facts.results_baseline_pr_auc"], pr
        unknown_objective = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **trusted, "analysis_contract": {"task_kind": "binary_classification"},
        }, computed_facts)})
        assert unknown_objective["ok"], unknown_objective
        assert unknown_objective["report_context"]["analysis_coverage"]["gaps"][0]["code"] == "PLANNED_OBJECTIVE_NOT_RECORDED", unknown_objective
        unknown_composed = call(bridge, "compose_ml_dashboard", composition_args(bridge, unknown_objective))
        assert not unknown_composed["ok"] and unknown_composed["evidence"]["repair_kind"] == "report_evidence", unknown_composed
        conflict = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **roc_contract, "planned_objective": "accuracy",
        }, computed_facts)})
        assert not conflict["ok"] and not conflict["recoverable"], conflict
        print("PASS: citations follow the requested metric; missing/conflicting objectives cannot be guessed")
        classification = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **trusted, "analysis_contract": {"task_kind": "binary_classification", "objective": "accuracy"},
        }, classification_facts)})
        assert classification["ok"] and classification["report_context"]["analysis_coverage"]["status"] == "evidence_available", classification
        future = call(bridge, "prepare_ml_report", {"report_manifest_ref": report(bridge, {
            **trusted, "analysis_contract": {"task_kind": "future-task"},
        }, classification_facts)})
        assert future["ok"] and future["report_context"]["analysis_coverage"]["status"] == "not_assessed", "an unknown contract was misclassified as a supported comparison"
        print("PASS: existing classification schema is supported; unknown requirement semantics are not guessed")
        partial_args = {**compose_args, "delivery_status": "partial", "output_mode": "full", "synthesis": synthesis()}
        partial_args["synthesis"]["thesis"] = "Available input overview; comparison is not complete"
        partial = call(bridge, "compose_ml_dashboard", partial_args)
        assert partial["ok"], partial
        assert partial["delivery_status"] == "partial", partial
        assert partial["evidence"]["analysis_coverage"]["status"] == "incomplete", partial
        assert partial["dashboard"]["askO11yDeliveryStatus"] == "partial", partial
        intro = partial["dashboard"]["panels"][0]
        assert "Partial report" in intro["options"]["content"], intro
        assert "trusted ML execution" in intro["options"]["content"], intro
        assert "Automated checks do not certify that the business question is fully answered" in intro["options"]["content"], intro
        assert intro["askO11yBusinessQuestionSource"] == "retained_question_unverified", intro
        assert "artifact://" not in intro["options"]["content"], intro
        assert partial["dashboard"]["tags"][0] == "ask-o11y-preview", partial
        assert partial["dashboard"]["panels"][1]["gridPos"]["y"] >= intro["gridPos"]["h"], partial
        assert partial["evidence"]["analysis_coverage"]["actions_granted"] == [], partial
        exported_partial = call(bridge, "compose_ml_dashboard", {**composition_args(bridge, count_only), "delivery_status": "partial"})
        assert exported_partial["ok"] and exported_partial["delivery_status"] == "partial", exported_partial
        assert exported_partial["evidence"]["analysis_coverage"]["gaps"][0]["code"] == "COMPARISON_FACTS_UNAVAILABLE", exported_partial
        assert not call(bridge, "compose_ml_dashboard", {**partial_args, "inspection_refs": []})["ok"]
        forged_partial = {**partial_args, "synthesis": synthesis()}
        forged_partial["synthesis"]["thesis_evidence"] = [{"fact_ref": "facts.fabricated", "format": "integer"}]
        assert not call(bridge, "compose_ml_dashboard", forged_partial)["ok"]
        uncited_partial = {**comparison_args, "synthesis": synthesis(), "delivery_status": "partial"}
        assert not call(bridge, "compose_ml_dashboard", uncited_partial)["ok"]
        for invalid_delivery in ("complete", True, None, []):
            assert not call(bridge, "compose_ml_dashboard", {**partial_args, "delivery_status": invalid_delivery})["ok"]
        assert not call(bridge, "compose_ml_dashboard", {**partial_args, "partial_notice": []})["ok"]
        unauthorized_partial = call(bridge, "compose_ml_dashboard", partial_args, {**OWNER, "user_id": "other-user"})
        assert not unauthorized_partial["ok"] and not unauthorized_partial["recoverable"], unauthorized_partial
        print("PASS: partial delivery retains gaps/Preview state; source, inspection, citation and authorization gates still apply")
        # Citation placement is not a fixed report layout: split the pair across
        # a section block and a view narrative rather than requiring the thesis.
        distributed = {**comparison_args, "synthesis": synthesis()}
        section = distributed["synthesis"]["sections"][0]
        section["narrative_blocks"] = [{
            "block_id": "comparison", "title": "Comparison evidence", "body": "Read the observed errors",
            "priority": "primary", "evidence": [{"fact_ref": "facts.selected_metrics_mae", "format": "number_2"}],
        }]
        section["panels"][0]["view_narratives"] = [{
            "view_id": "figure", "headline": "Input overview", "data_observation": "Rows are available",
            "interpretation": "Describe the data", "limitation": "Not causal evidence",
            "evidence": [{"fact_ref": "facts.baseline_metrics_holdout_mae", "format": "number_2"}],
        }]
        assert call(bridge, "compose_ml_dashboard", distributed)["ok"]
        for actor in ({**OWNER, "org_id": "other-org"}, {**OWNER, "session_id": "other-session"}):
            refused = call(bridge, "compose_ml_dashboard", comparison_args, actor)
            assert not refused["ok"] and not refused["recoverable"], refused
        # Simulate damaged retained provenance after preparation; compose must
        # recheck the receipt, not rely on its cached evidence_available status.
        provenance_ref = verified_coverage["provenance_ref"]
        provenance = bridge.ARTIFACTS.read_json(OWNER, provenance_ref)
        run_id, _parts = bridge.parse_artifact_ref(provenance_ref)
        # Deliberate filesystem fault: normal ArtifactStore writes correctly
        # forbid replacing immutable provenance. Only this temporary fixture is changed.
        provenance_path = Path(tmp) / run_id / "sandbox-provenance.json"
        provenance_path.write_text(json.dumps({**provenance, "analysis_contract": None}), encoding="utf-8")
        erased = call(bridge, "compose_ml_dashboard", comparison_args)
        assert not erased["ok"] and not erased["recoverable"], "erased provenance silently downgraded verified coverage to not_assessed"
        provenance_path.write_text(json.dumps({**provenance, "report_manifest_ref": profile_ref}), encoding="utf-8")
        stale = call(bridge, "compose_ml_dashboard", comparison_args)
        assert not stale["ok"] and not stale["recoverable"], stale
        assert stale["evidence"]["repair_kind"] == "report_identity", stale
        print("PASS: flexible citation placement; cross-org/session denial; stale/mispaired receipt stops composition")
        provenance_path.unlink()  # Simulate loss of this test's retained receipt.
        missing_receipt = call(bridge, "prepare_ml_report", {"report_manifest_ref": verified["refs"]["report_manifest_ref"]})
        assert not missing_receipt["ok"] and not missing_receipt["recoverable"], "a missing receipt was re-prepared as harmless not_assessed coverage"
        missing_partial = call(bridge, "compose_ml_dashboard", {**comparison_args, "delivery_status": "partial"})
        assert not missing_partial["ok"] and not missing_partial["recoverable"], missing_partial
        print("PASS: missing provenance cannot be downgraded by preparing again or choosing partial delivery")

        # Public cross-service positive seam: sandbox re-export returns fresh
        # refs, then the bridge verifies the authenticated parent packet and
        # promotes only the retained comparison facts.
        sandbox = bridge.load_module("coverage_reexport_sandbox", ROOT / "sandbox-analysis-mcp/server.py")
        sandbox.ARTIFACTS = bridge.ARTIFACTS
        source_run = bridge.ARTIFACTS.create_run(OWNER)
        snapshot = sandbox.ontology_contract.snapshot_identity(sandbox.ontology_contract.load_snapshot())
        analysis_contract = {
            "kind": "gradient_boosting", "target": "income", "features": ["age"],
            "split": {"kind": "stratified_holdout", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only", "seed": 42},
            "seed": 42, "autotune": True, "objective": "accuracy", "search_budget": 2,
            "ontology_snapshot_sha256": snapshot["sha256"], "positive_class": ">50K",
            "purpose": "predict income", "conclusion": "review",
        }
        plan = {
            "business_question": "Compare model errors", "analysis_input_contract": {
                "execution_template": "ask_o11y_gradient_boosting_v1", "preprocessing_fit_scope": "training_only",
                "autoresearch": {"objective": "accuracy", "search_budget": 2, "max_search_budget": 40},
            }, "ontology": snapshot, "analysis_contract": analysis_contract,
        }
        plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        frame = [{"schema": {"fields": [{"name": "age"}, {"name": "income"}]}, "data": {"values": [[30, 40], ["<=50K", ">50K"]]}}]
        frame_ref = bridge.ARTIFACTS.write_json(OWNER, source_run, "grafana-frame", frame)
        bridge.ARTIFACTS.write_json(OWNER, source_run, "query-plan", plan)
        code = "trusted retained code"
        code_sha256 = hashlib.sha256(code.encode()).hexdigest()
        code_ref = bridge.ARTIFACTS.write_json(OWNER, source_run, "sandbox-code", {"source": code, "sha256": code_sha256})
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        legacy_source = {
            "format": "ask-o11y-ml-presentation-v1", "purpose": "Compare model errors", "conclusion": "trusted",
            "results": {"selected": {"accuracy": 0.8}, "baseline": {"accuracy": 0.6}}, "artifacts": [{"name": "overview.png"}],
        }
        source_results = [
            {"display_name": "overview.png", "mime": {"image/png": png}},
            {"display_name": "ml-presentation.json", "mime": {"application/json": json.dumps(legacy_source)}},
        ]
        execution_ref = bridge.ARTIFACTS.write_json(OWNER, source_run, "sandbox-execution", {"results": source_results, "error": None})
        frame_sha256 = hashlib.sha256(json.dumps(frame[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        provenance_ref = bridge.ARTIFACTS.write_json(OWNER, source_run, "sandbox-provenance", {
            "executor_kind": "execute_ml_contract", "trusted_ml_contract": True, "input_frame_ref": frame_ref,
            "input_frame_sha256": frame_sha256, "plan_sha256": plan["plan_sha256"], "code_sha256": code_sha256,
            "code_ref": code_ref, "business_question": plan["business_question"], "analysis_contract": analysis_contract,
            "planned_objective": "accuracy", "report_manifest_ref": None, "computation_status": "succeeded", "report_status": "rejected",
        })
        original_execution = bridge.ARTIFACTS.read_json(OWNER, execution_ref)
        original_provenance = bridge.ARTIFACTS.read_json(OWNER, provenance_ref)
        reexported = sandbox.reexport_trusted_report({"execution_ref": execution_ref, "_server_context": OWNER})
        assert reexported["ok"], reexported
        prepared_reexport = call(bridge, "prepare_ml_report", {"report_manifest_ref": reexported["refs"]["report_manifest_ref"]})
        assert prepared_reexport["ok"], prepared_reexport
        reexport_coverage = prepared_reexport["report_context"]["analysis_coverage"]
        assert reexport_coverage["status"] == "evidence_available" and reexport_coverage["business_question_status"] == "assessed", reexport_coverage
        reexport_args = composition_args(bridge, prepared_reexport)
        reexport_evidence = [{"fact_ref": ref, "format": "percent_1"} for ref in reexport_coverage["evidence_fact_refs"]]
        def replace_evidence(node):
            if isinstance(node, dict):
                if isinstance(node.get("evidence"), list):
                    node["evidence"] = list(reexport_evidence)
                for child in node.values():
                    replace_evidence(child)
            elif isinstance(node, list):
                for child in node:
                    replace_evidence(child)
        reexport_args["synthesis"]["thesis_evidence"] = list(reexport_evidence)
        replace_evidence(reexport_args["synthesis"])
        for section in reexport_args["synthesis"]["sections"]:
            for panel in section.get("panels", []):
                panel["view_ids"] = ["image"]
                for narrative in panel.get("view_narratives", []):
                    narrative["view_id"] = "image"
        composed_reexport = call(bridge, "compose_ml_dashboard", reexport_args)
        assert composed_reexport["ok"] and composed_reexport["evidence"]["analysis_coverage"]["status"] == "evidence_available", composed_reexport
        assert bridge.ARTIFACTS.read_json(OWNER, execution_ref) == original_execution
        assert bridge.ARTIFACTS.read_json(OWNER, provenance_ref) == original_provenance
        assert reexported["refs"]["execution_ref"] != execution_ref and reexported["refs"]["provenance_ref"] != provenance_ref
        print("PASS: trusted re-export → bridge prepare/compose preserves immutable source and verifies retained comparison facts")

        # Deliberate corruption of disposable store files only; never overwrite
        # immutable artifacts through the production API or touch the live store.
        fresh_execution_ref = reexported["refs"]["execution_ref"]
        fresh_provenance_ref = reexported["refs"]["provenance_ref"]
        foreign_owner = {**OWNER, "user_id": "foreign-lineage-owner"}
        foreign_run = bridge.ARTIFACTS.create_run(foreign_owner)
        foreign_execution = bridge.ARTIFACTS.write_json(foreign_owner, foreign_run, "sandbox-execution", original_execution)
        foreign_provenance = bridge.ARTIFACTS.write_json(foreign_owner, foreign_run, "sandbox-provenance", original_provenance)
        fresh_results = bridge.ARTIFACTS.read_json(OWNER, fresh_execution_ref)["results"]
        fresh_source_index = next(index for index, item in enumerate(fresh_results) if item.get("display_name") == "report-source.json")
        try:
            fresh_source = json.loads(fresh_results[fresh_source_index]["mime"]["application/json"])
        except json.JSONDecodeError as exc:
            raise AssertionError("positive re-export fixture must contain valid report-source JSON") from exc
        fresh_source["facts"] = {"forged": {"kind": "number", "label": "Forged fact", "value": 0.99}}
        missing = object()
        cases = {
            "missing-parent": [(fresh_execution_ref, ("reexport_of",), missing)],
            "mispaired-parent": [(fresh_provenance_ref, ("source_provenance_ref",), fresh_provenance_ref)],
            "foreign-parent": [
                (ref, (key,), value)
                for ref in (fresh_execution_ref, fresh_provenance_ref)
                for key, value in (("reexport_of", foreign_execution), ("source_provenance_ref", foreign_provenance))
            ],
            "source-identity": [(provenance_ref, ("code_sha256",), "0" * 64)],
            "fresh-identity": [(fresh_provenance_ref, ("input_frame_sha256",), "0" * 64)],
            "source-facts": [(execution_ref, ("results", 1, "mime", "application/json"), json.dumps({**legacy_source, "results": {"selected": {"accuracy": 0.99}, "baseline": {"accuracy": 0.01}}}))],
            "fresh-facts": [(fresh_execution_ref, ("results", fresh_source_index, "mime", "application/json"), json.dumps(fresh_source))],
        }
        context_run, context_parts = bridge.parse_artifact_ref(reexport_args["report_context_ref"])
        context_path = Path(tmp) / context_run / (context_parts[0] + ".json")
        for case, changes in cases.items():
            # Preparation refreshes the mutable report context. Test the
            # previously verified context before refreshing it, then restore it.
            saved = {context_path: context_path.read_bytes()}
            try:
                for ref, keys, value in changes:
                    artifact_run, parts = bridge.parse_artifact_ref(ref)
                    path = Path(tmp) / artifact_run / (parts[0] + ".json")
                    saved.setdefault(path, path.read_bytes())
                    document = json.loads(path.read_text())
                    node = document
                    for key in keys[:-1]:
                        node = node[key]
                    if value is missing:
                        del node[keys[-1]]
                    else:
                        node[keys[-1]] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                assert not call(bridge, "compose_ml_dashboard", reexport_args)["ok"], case
                damaged = call(bridge, "prepare_ml_report", {"report_manifest_ref": reexported["refs"]["report_manifest_ref"]})
                assert not damaged["ok"] or damaged["report_context"]["analysis_coverage"]["status"] != "evidence_available", f"{case}: {damaged}"
                print(f"PASS: re-export tamper {case} cannot promote coverage or compose")
            finally:
                for path, content in saved.items():
                    path.write_bytes(content)
        assert call(bridge, "compose_ml_dashboard", reexport_args)["ok"], "restored control must still compose"


if __name__ == "__main__":
    main()
