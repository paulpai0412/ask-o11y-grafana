#!/usr/bin/env python3
"""Fresh Ask O11y E2E for three ontology-derived U1 regression feature sets."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch/u1-regression-e2e/ask-o11y-feature-sets.json"
GRAFANA = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def grafana_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    auth = base64.b64encode(b"admin:admin").decode()
    request = urllib.request.Request(
        GRAFANA + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
        method=method,
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json", "X-Grafana-Org-Id": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}") from exc


def start_run(message: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "type": "chat"}
    if session_id:
        body["sessionId"] = session_id
    return grafana_json(
        "/api/plugins/consensys-asko11y-app/resources/api/agent/run?" + urllib.parse.urlencode({"model": "large"}),
        "POST",
        body,
    )


def poll_run(run_id: str, *, approve: bool) -> tuple[dict[str, Any], list[str]]:
    approvals: list[str] = []
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        status = grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}")
        for event in status.get("events", []):
            if event.get("type") != "approval_request":
                continue
            approval_id = str((event.get("data") or {}).get("approvalId") or "")
            if not approval_id or approval_id in approvals:
                continue
            if not approve:
                raise RuntimeError("Analysis Preview requested execution approval")
            grafana_json(
                f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}",
                "POST",
                {"decision": "approved", "comment": "Approve the three exact regression contracts once.", "approvalScope": "once"},
            )
            approvals.append(approval_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status, approvals
        time.sleep(3)
    raise RuntimeError(f"Ask O11y run timed out: {run_id}")


def tool_names(status: dict[str, Any]) -> list[str]:
    return [str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_start"]


def tool_arguments(status: dict[str, Any], name: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") == "tool_call_start" and data.get("name") == name:
            try:
                arguments = json.loads(data.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid {name} arguments") from exc
            if isinstance(arguments, dict):
                output.append(arguments)
    return output


def tool_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("isError")]


def tool_result_text(status: dict[str, Any], name: str) -> list[str]:
    return [json.dumps(event.get("data") or {}, ensure_ascii=False) for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("name") == name]


def successful_tool_results(status: dict[str, Any], name: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") != "tool_call_result" or data.get("name") != name:
            continue
        try:
            result = json.loads(str(data.get("content") or "{}"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid {name} result") from exc
        if isinstance(result, dict) and result.get("ok"):
            output.append(result)
    return output


def visible_text(status: dict[str, Any]) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report"}], ensure_ascii=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def expected_feature_sets() -> dict[str, dict[str, Any]]:
    ontology = load("feature_set_ontology", ROOT / "ontology_contract.py")
    snapshot = ontology.load_snapshot(dataset_id="u1-operating-daily")
    dataset = ontology.find_dataset(snapshot, "u1-operating-daily")
    if dataset is None:
        raise RuntimeError("approved U1 ontology dataset is unavailable")
    approved = list(dataset["approved_features"])
    treatments = [field["physical_name"] for field in dataset["fields"] if field.get("analysis_role") == "treatment_candidate"]
    return {
        "A": {"features": approved, "controllable_fields": [], "context_fields": approved, "include_treatment_candidates": False},
        "B": {"features": [*approved, *treatments], "controllable_fields": treatments, "context_fields": approved, "include_treatment_candidates": True},
        "C": {"features": treatments, "controllable_fields": treatments, "context_fields": [], "include_treatment_candidates": True},
    }


def main() -> int:
    expected = expected_feature_sets()
    preview = start_run(
        "請使用已註冊的 `u1-operating-daily`，做一次 ontology-first regression feature sensitivity analysis。建立三個獨立且可比較的 nested feature-set contracts：Model A 只用 ontology approved_features；Model B 用 approved_features 加上 ontology 的 treatment_candidate fields；Model C 只用 treatment_candidate fields。這三個集合必須從 ontology 回傳的 exact canonical field names 產生，不能自行翻譯、命名或補欄位。三者都使用 task_kind regression、retrospective_association、heat_rate、minimize、相同 chronological holdout、training_only preprocessing、相同 algorithms、seed 與 search budget。treatment_candidate 可作為本次研究性模型 feature，但不代表操作核准；不要做 constrained action recommendation。先完成 metadata/ontology validation 與 Analysis Preview，確認前不要執行 Grafana Query、Sandbox 或 Dashboard。",
    )
    preview_status, _ = poll_run(preview["runId"], approve=False)
    preview_names = tool_names(preview_status)
    preview_errors = tool_errors(preview_status)
    require(preview_status.get("status") == "completed" and not preview_errors, f"feature-set preview failed: {preview_errors}")
    require("grafana-query_inspect_dataset" in preview_names and "ontology_get_semantic_context" in preview_names, f"preview omitted metadata/ontology: {preview_names}")
    require(not {"grafana-query_execute_planned_query", "sandbox-analysis_execute_ml_contract", "sandbox-analysis_execute_python_analysis", "mcp-grafana_update_dashboard"}.intersection(preview_names), f"preview executed protected work: {preview_names}")
    session_id = str(preview.get("sessionId") or preview_status.get("sessionId") or "")
    require(bool(session_id), "Ask O11y session missing")

    execution = start_run(
        "確認執行剛才的三個 feature-set contracts，依序完成 Model A、Model B、Model C。三者必須使用完全相同的 split、preprocessing、algorithms、seed、CV/holdout 與 baseline gate；只允許 feature set 不同。每個 contract 都要經 Planner 與 execute_ml_contract，禁止 execute_python_analysis。完成後只回覆一個 Result Preview，比較三組的 CV/holdout MAE、RMSE、R²、baseline gate 與 treatment feature contribution；不要建立 Grafana Dashboard，不要提出因果最佳或現場控制指令。",
        session_id,
    )
    execution_status, approvals = poll_run(execution["runId"], approve=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.with_name("ask-o11y-feature-sets-debug.json").write_text(json.dumps({"preview": preview_status, "execution": execution_status}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    names = tool_names(execution_status)
    errors = tool_errors(execution_status)
    require(execution_status.get("status") == "completed" and not errors, f"feature-set execution failed: {errors}")
    require(names.count("data-query-planner_plan_query") >= 3, f"expected three independent plans: {names}")
    require(names.count("grafana-query_execute_planned_query") == 3, f"expected three Grafana queries: {names}")
    require(names.count("sandbox-analysis_execute_ml_contract") == 3, f"expected three structured ML executions: {names}")
    require("sandbox-analysis_execute_python_analysis" not in names, "Ask O11y bypassed structured executor")
    require("mcp-grafana_update_dashboard" not in names, "Result Preview unexpectedly wrote a dashboard")

    plans = tool_arguments(execution_status, "data-query-planner_plan_query")
    observed: dict[str, dict[str, Any]] = {}
    for arguments in plans:
        contract = arguments.get("analysis_contract")
        if not isinstance(contract, dict):
            raise RuntimeError("plan missing analysis_contract")
        feature_names = set(contract.get("features") or [])
        matched = [name for name, shape in expected.items() if feature_names == set(shape["features"])]
        require(len(matched) == 1, f"plan feature set did not match A/B/C: {contract}")
        label = matched[0]
        require(contract.get("task_kind") == "regression" and contract.get("analysis_mode") == "retrospective_association", f"{label} contract mode invalid")
        require(contract.get("target") == "heat_rate" and contract.get("target_direction") == "minimize", f"{label} target invalid")
        require(contract.get("controllable_fields") == expected[label]["controllable_fields"], f"{label} controllable fields drifted")
        require(contract.get("context_fields") == expected[label]["context_fields"], f"{label} context fields drifted")
        require(bool(contract.get("include_treatment_candidates")) == expected[label]["include_treatment_candidates"], f"{label} treatment opt-in drifted")
        observed.setdefault(label, contract)
    require(set(observed) == set(expected), f"missing feature-set plans: {set(observed)}")

    results = successful_tool_results(execution_status, "sandbox-analysis_execute_ml_contract")
    require(len(results) == 3, "not all structured regression executions succeeded")
    model_results: list[dict[str, Any]] = []
    for result in results:
        inline = result.get("output_summary", {}).get("inline_results", [])
        manifest = next((item.get("value") for item in inline if isinstance(item, dict) and item.get("display_name") == "ml-regression.json"), None)
        if not isinstance(manifest, dict):
            raise RuntimeError("structured execution omitted regression manifest")
        feature_count = manifest.get("data", {}).get("features")
        label = next((name for name, shape in expected.items() if len(shape["features"]) == feature_count), None)
        if label is None:
            raise RuntimeError("regression manifest feature count did not match A/B/C")
        model_results.append({
            "model": label,
            "features": feature_count,
            "selected_model": manifest.get("selected_model"),
            "baseline_metrics": manifest.get("baseline_metrics"),
            "selected_metrics": manifest.get("selected_metrics"),
            "guards": manifest.get("guards"),
        })
    report_text = visible_text(execution_status)
    require("Model A" in report_text and "Model B" in report_text and "Model C" in report_text, "Result Preview omitted feature-set comparison")
    require("因果" not in report_text or "不" in report_text, "Result Preview used an unqualified causal claim")

    evidence = {
        "ok": True,
        "session_id": session_id,
        "preview_tools": preview_names,
        "execution_tools": names,
        "approval_count": len(approvals),
        "feature_sets": expected,
        "model_results": model_results,
        "validation": {
            "ontology_derived_feature_sets": True,
            "three_independent_contracts": True,
            "same_split_and_budget": True,
            "treatment_features_explicitly_opted_in": True,
            "no_generated_training_code": True,
            "result_preview_only": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "execution_tools": names, "approval_count": len(approvals), "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
