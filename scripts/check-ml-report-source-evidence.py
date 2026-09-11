#!/usr/bin/env python3
"""Offline regression for trusted computed values through source -> manifest -> catalog."""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import statistics
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sandbox-analysis-mcp")]
from ml_report_contract import MAX_REPORT_FACTS, build_fact_catalog, normalize_report_manifest

def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


presentation = load_module("ml_presentation", ROOT / "sandbox-analysis-mcp/ml_presentation.py")
build_report_source = presentation.build_report_source

# Same valid fixture as check-report-manifest-contract.py.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def reject(manifest: dict, fragment: str) -> None:
    try:
        build_report_source(manifest)
    except ValueError as exc:
        assert fragment in str(exc), str(exc)
    else:
        raise AssertionError(f"accepted invalid evidence: {fragment}")


def main() -> None:
    # Independently computed fixture, not claimed as a real dataset/model run.
    actual, predicted, baseline = [1, 2, 4], [1, 3, 2], [0, 0, 0]
    selected_mae = statistics.mean(abs(y - p) for y, p in zip(actual, predicted))
    baseline_mae = statistics.mean(abs(y - p) for y, p in zip(actual, baseline))
    rmse = math.sqrt(statistics.mean((y - p) ** 2 for y, p in zip(actual, predicted)))
    original = {
        "purpose": "Compare computed errors", "conclusion": "Observational validation only",
        "data": {"rows": 3}, "process": {"completed_trials": 2},
        "selected_metrics": {"mae": selected_mae, "rmse": rmse},
        "baseline_metrics": {"mae": baseline_mae},
        "results": {"baseline": {"custom_score": 0.25}, "selected": {"custom_score": 0.75, "interval": [-0.2, 0.4]}},
        "guards": {"validation": {"accepted": False}},
        "artifacts": [{"name": "result.png"}],
    }
    before = copy.deepcopy(original)
    source = build_report_source(original, plotly_names={"result"})
    expected = {
        "rows": 3, "completed_trials": 2,
        "selected_metrics_mae": selected_mae, "selected_metrics_rmse": rmse,
        "baseline_metrics_mae": baseline_mae,
        "results_baseline_custom_score": 0.25, "results_selected_custom_score": 0.75,
        "results_selected_interval_0": -0.2, "results_selected_interval_1": 0.4,
        "guards_validation_accepted": False,
    }
    for name, value in expected.items():
        assert name in source["facts"], f"computed evidence dropped: {name}"
        assert source["facts"][name]["value"] == value
    assert source["facts"]["guards_validation_accepted"]["kind"] == "boolean"
    assert source["facts"]["selected_metrics_mae"]["label"] == "selected_metrics.mae"
    manifest = normalize_report_manifest(execution_ref="artifact://evidence-test/sandbox-execution", results=[
        {"display_name": "report-source.json", "mime": {"application/json": json.dumps(source)}},
        {"display_name": "result.png", "mime": {"image/png": PNG}},
        {"display_name": "ml-plotly-result.json", "mime": {"application/vnd.plotly.v1+json": json.dumps({"data": [{"type": "bar", "x": ["selected", "baseline"], "y": [selected_mae, baseline_mae]}], "layout": {}})}},
    ])
    catalog = build_fact_catalog(manifest)
    for name, value in expected.items():
        assert catalog[f"facts.{name}"]["value"] == value
    assert original == before, "adapter mutated computed results"

    reject({**original, "selected_metrics": [1, 2]}, "object")
    for value in (math.nan, math.inf, -math.inf, 10 ** 400):
        reject({**original, "selected_metrics": {"score": value}}, "non-finite")
    reject({**original, "selected_metrics": {"score.1": 1, "score 1": 2}}, "collision")
    reject({**original, "selected_metrics": {"a" * 80 + "x": 1, "a" * 80 + "y": 2}}, "collision")
    reject({**original, "results": {"credentials": {"number": 1}}}, "forbidden")
    reject({**original, "data": {"rows": 3}, "process": {"rows": 4}}, "collision")
    reject({**original, "results": {"selected": {"score": math.nan}}}, "non-finite")
    deep = {"score": 1}
    for _ in range(8):
        deep = {"nested": deep}
    reject({**original, "selected_metrics": deep}, "bound")
    reject({**original, "selected_metrics": {f"score_{i}": i for i in range(MAX_REPORT_FACTS + 1)}}, "bound")
    # No silent 32-fact truncation; the existing host contract accepts up to 64.
    source = build_report_source({"data": {f"value_{i}": i for i in range(MAX_REPORT_FACTS)}, "artifacts": []})
    assert len(source["facts"]) == MAX_REPORT_FACTS
    # The adapter is not a new trust authority. Generic Python cannot submit its
    # output as verified facts, even when it uses the genuine adapter's format.
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"ANALYSIS_ARTIFACT_ROOT": folder}):
        server = load_module("evidence_test_server", ROOT / "sandbox-analysis-mcp/server.py")
        audit = {"input_rows": 3, "valid_rows": 3, "excluded_rows": 0}
        for step in ("execute_python_analysis", "revise_python_analysis"):
            forged = {"results": [{"display_name": "metrics.json", "mime": {"application/json": json.dumps(build_report_source(original))}}]}
            try:
                server._ensure_generic_report_source(forged, audit, step)
            except server.WorkflowContractError as exc:
                assert "generic Python" in str(exc), str(exc)
            else:
                raise AssertionError("generic Python promoted computed report facts")
            generic = {"results": [
                {"display_name": "figure.json", "mime": {"application/vnd.plotly.v1+json": json.dumps({"data": [], "layout": {}})}},
                {"display_name": "metrics.json", "mime": {"application/json": json.dumps(original)}},
            ]}
            assert server._ensure_generic_report_source(generic, audit, step)
            try:
                host_source = json.loads(generic["results"][-1]["mime"]["application/json"])
            except json.JSONDecodeError as exc:
                raise AssertionError("host emitted invalid report-source JSON") from exc
            assert set(host_source["facts"]) == {"rows", "valid_rows", "excluded_rows"}
    print("PASS: computed values survive source/manifest/catalog; invalid evidence rejects; generic Python cannot promote facts")


if __name__ == "__main__":
    main()
