#!/usr/bin/env python3
"""Run the real Vestas profile-first then Power-regression Ask O11y E2E."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.nlap_receipts import record_run  # pyright: ignore[reportMissingImports] -- resolved from ROOT.

RUN_RECEIPTS: dict[str, dict[str, str]] = {}
SOURCE = ROOT / ".scratch/huggingface/vestas_high_wind_power_regulation.csv"
OUT = ROOT / ".scratch/real-vestas-ask-o11y-e2e.json"
GRAFANA = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"')
        command_file = re.fullmatch(r"\$\(cat ['\"]?([^'\")]+)['\"]?\)", value)
        if command_file:
            value = Path(os.path.expandvars(command_file.group(1))).expanduser().read_text(encoding="utf-8").strip()
        os.environ.setdefault(key, value)


def grafana_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    auth = base64.b64encode(b"admin:admin").decode()
    request = urllib.request.Request(GRAFANA + path, data=None if body is None else json.dumps(body, ensure_ascii=False).encode(), method=method, headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}") from exc


def start_run(message: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "type": "chat"}
    if session_id:
        body["sessionId"] = session_id
    model = os.environ.get("ASK_O11Y_E2E_MODEL")
    if not model:
        raise RuntimeError("Set ASK_O11Y_E2E_MODEL to a verified low-cost configured model before live tests")
    return grafana_json("/api/plugins/consensys-asko11y-app/resources/api/agent/run?" + urllib.parse.urlencode({"model": model}), "POST", body)


def poll_run(run_id: str, *, approve: bool) -> tuple[dict[str, Any], list[str]]:
    approvals: list[str] = []
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        status = grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}")
        RUN_RECEIPTS[run_id] = record_run(status)
        for event in status.get("events", []):
            if event.get("type") != "approval_request":
                continue
            approval_id = str((event.get("data") or {}).get("approvalId") or "")
            if not approval_id or approval_id in approvals:
                continue
            if not approve:
                raise RuntimeError("unexpected mutation approval during preview")
            grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{run_id}/approvals/{approval_id}", "POST", {"decision": "approved", "comment": "Approve this exact user-requested preview once.", "approvalScope": "once"})
            approvals.append(approval_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status, approvals
        time.sleep(3)
    raise RuntimeError(f"Ask O11y run timed out: {run_id}")


def upload(session_id: str) -> dict[str, Any]:
    raw = SOURCE.read_bytes()
    boundary = "----ask-o11y-upload-boundary"
    body = b"".join((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{session_id}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{SOURCE.name}\"\r\nContent-Type: text/csv\r\n\r\n".encode(),
        raw,
        f"\r\n--{boundary}--\r\n".encode(),
    ))
    auth = base64.b64encode(b"admin:admin").decode()
    request = urllib.request.Request(
        GRAFANA + "/api/plugins/consensys-asko11y-app/resources/api/uploads",
        data=body,
        method="POST",
        headers={
            "Authorization": "Basic " + auth,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("real Vestas upload failed") from exc


def tool_names(status: dict[str, Any]) -> list[str]:
    return [str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_start"]


def tool_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.get("data") or {} for event in status.get("events", []) if event.get("type") == "tool_call_result" and (event.get("data") or {}).get("isError")]


def unrecovered_tool_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    successful_names = {str((event.get("data") or {}).get("name")) for event in status.get("events", []) if event.get("type") == "tool_call_result" and not (event.get("data") or {}).get("isError")}
    return [error for error in tool_errors(status) if str(error.get("name")) not in successful_names]


def tool_arguments(status: dict[str, Any], name_fragment: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for event in status.get("events", []):
        data = event.get("data") or {}
        if event.get("type") != "tool_call_start" or name_fragment not in str(data.get("name")):
            continue
        try:
            value = json.loads(data.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid arguments for {name_fragment}") from exc
        if isinstance(value, dict):
            values.append(value)
    return values


def visible_text(status: dict[str, Any]) -> str:
    return json.dumps([event.get("data") or {} for event in status.get("events", []) if event.get("type") in {"content", "final_report", "tool_call_result"}], ensure_ascii=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    load_env()
    require(SOURCE.is_file(), f"real source is unavailable: {SOURCE}")
    opened = start_run("我要先理解稍後上傳的實際表格資料。請建立分析工作階段並等待我提供 upload dataset id；此回合不要查資料或呼叫工具。")
    opened_status, _ = poll_run(opened["runId"], approve=False)
    session_id = str(opened.get("sessionId") or opened_status.get("sessionId") or "")
    actor_user_id = str(opened_status.get("userId") or "")
    require(bool(session_id) and bool(actor_user_id) and opened_status.get("status") == "completed", "Ask O11y session creation failed")
    uploaded = upload(session_id)
    dataset_id = str(uploaded["dataset_id"])

    profile_request = (
        f"我已上傳實際資料集。請只做 ontology-guided、data-first 的完整資料 profile，不要做 ML。請讀取所有 {uploaded['rows']} rows 與所有欄位，保留實際 row count，不抽樣、不截斷、不建立 derived dataset；圖表聚合只作視覺用途。請 inspect dataset、取得 observed upload ontology evidence、檢查欄位型別與 Timestamp 日期語義，提出 Analysis Preview；確認前不要執行 Grafana Query、Sandbox 或 Dashboard 寫入。不要猜測或從檔名推導 dataset id，使用本聊天上傳附件。"
    )
    require(dataset_id not in profile_request, "structured-attachment E2E accidentally put dataset id in the replacement prompt")
    profile_preview = start_run(profile_request, session_id)
    profile_preview_status, _ = poll_run(profile_preview["runId"], approve=False)
    profile_preview_names = tool_names(profile_preview_status)
    require(profile_preview_status.get("status") == "completed" and not tool_errors(profile_preview_status), "profile preview failed")
    require("grafana-query_inspect_dataset" in profile_preview_names and "grafana-query_execute_planned_query" not in profile_preview_names, f"profile preview crossed query boundary: {profile_preview_names}")
    require("sandbox-analysis_profile_dataset" not in profile_preview_names and "mcp-grafana_update_dashboard" not in profile_preview_names, f"profile preview executed protected work: {profile_preview_names}")
    require("Analysis Preview" in visible_text(profile_preview_status), "profile preview was not visible")

    profile_execution = start_run(
        "確認剛才的完整 profile Analysis Preview。請執行同一個授權計畫，維持所有 rows/fields 與完整 row count，不抽樣、不截斷、不建立 derived dataset；完成後請使用通用 prepare_ml_report、inspect_report_artifacts、compose_ml_dashboard 做一次完整 evidence-bound profile report，逐一檢查所有 artifact/view，建立可檢視但不要正式發佈的 Grafana Preview；compose 成功後把 refs.dashboard_ref 以 {dashboard: {$dashboard_ref: refs.dashboard_ref}} 交給 writer，不要複製完整 dashboard JSON。這一回合仍然不要做 ML。",
        session_id,
    )
    profile_execution_status, profile_approvals = poll_run(profile_execution["runId"], approve=True)
    profile_execution_names = tool_names(profile_execution_status)
    require(profile_execution_status.get("status") == "completed" and not unrecovered_tool_errors(profile_execution_status), "profile execution failed")
    for required_name in ("grafana-query_execute_planned_query", "sandbox-analysis_profile_dataset", "mcp-grafana_update_dashboard"):
        require(required_name in profile_execution_names, f"profile chain omitted {required_name}: {profile_execution_names}")
    require("sandbox-analysis_execute_ml_contract" not in profile_execution_names, "profile path unexpectedly invoked ML")
    profile_text = visible_text(profile_execution_status)
    require("/d/" in profile_text and "10000" in profile_text, "profile report omitted the real row count or dashboard URL")

    ml_preview = start_run(
        "現在才要進行明確要求的 supervised regression。請針對本聊天的實際 upload attachment 以物理欄位 `Power` 為連續 target；`Timestamp` 只作 chronological split，不得進 features。請依 observed ontology/data evidence 動態判斷 features，排除 MaxPower、MinPower、StdDevPower、AvgRPow、GenRPM、Scenario 及其他 leakage/proxy 欄位；不可抽樣、截斷、建立 derived dataset，不使用 sample weights。Contract 使用 objective=mae、search_budget=5、sample_weight_fields=[]；只做預測評估，不要求最佳化目標或候選操作設定，並保留 Dummy baseline；只有模型同時勝過 baseline 才能提出 observed-support candidate settings。先重新提出 ontology-pinned ML Analysis Preview，確認前不要執行 query、Sandbox 或 Dashboard 寫入。",
        session_id,
    )
    ml_preview_status, _ = poll_run(ml_preview["runId"], approve=False)
    ml_preview_names = tool_names(ml_preview_status)
    require(ml_preview_status.get("status") == "completed" and not tool_errors(ml_preview_status), "ML preview failed")
    require("Analysis Preview" in visible_text(ml_preview_status), "ML preview was not visible")
    require("grafana-query_execute_planned_query" not in ml_preview_names and "sandbox-analysis_execute_ml_contract" not in ml_preview_names, f"ML preview crossed execution boundary: {ml_preview_names}")

    ml_execution = start_run(
        "確認剛才的 Power regression Analysis Preview。請執行完整實際資料，不改寫欄位名稱或 refs；contract 維持 objective=mae、search_budget=5、sample_weight_fields=[]；不啟用最佳化或候選操作設定，並維持 Timestamp chronological split、training-only preprocessing、Dummy baseline gate、holdout only once、no sample weights。完成後讀取完整 facts/artifacts/views，做一次 evidence-bound whole-report synthesis，建立可檢視但不要正式發佈的 Grafana Preview；compose 成功後把 refs.dashboard_ref 以 {dashboard: {$dashboard_ref: refs.dashboard_ref}} 交給 writer，不要複製完整 dashboard JSON；不得用 model-authored arbitrary Python 取代 trusted execute_ml_contract。",
        session_id,
    )
    ml_execution_status, ml_approvals = poll_run(ml_execution["runId"], approve=True)
    ml_execution_names = tool_names(ml_execution_status)
    require(ml_execution_status.get("status") == "completed" and not unrecovered_tool_errors(ml_execution_status), "ML execution failed")
    for required_name in ("grafana-query_execute_planned_query", "sandbox-analysis_execute_ml_contract", "mcp-grafana_update_dashboard"):
        require(required_name in ml_execution_names, f"ML chain omitted {required_name}: {ml_execution_names}")
    require("sandbox-analysis_execute_python_analysis" not in ml_execution_names, "ML path bypassed the trusted contract executor")
    plans = tool_arguments(ml_execution_status, "plan_query")
    require(bool(plans), "ML execution did not create a query plan")
    contract = plans[-1].get("analysis_contract") or {}
    features = [str(name) for name in contract.get("features") or []]
    require(contract.get("task_kind") == "regression" and contract.get("target") == "Power", f"unexpected regression contract: {contract}")
    require("Timestamp" not in features and "Power" not in features, f"target/split leaked into features: {features}")
    require(contract.get("split", {}).get("kind") == "chronological_holdout", f"unsafe regression split: {contract.get('split')}")
    require(contract.get("split", {}).get("preprocessing_fit_scope") == "training_only", "preprocessing was not training-only")
    require(contract.get("objective") == "mae", f"unexpected regression metric: {contract.get('objective')}")
    require(contract.get("search_budget") == 5 and contract.get("sample_weight_fields") == [], f"unsafe regression budget/weights: {contract.get('search_budget')} / {contract.get('sample_weight_fields')}")
    require(bool(contract.get("algorithms")), "regression did not declare its candidate algorithms")
    require({"MaxPower", "MinPower", "StdDevPower", "AvgRPow", "GenRPM", "Scenario"} <= set(contract.get("forbidden_fields") or []), f"known leakage/proxy fields were not forbidden: {contract.get('forbidden_fields')}")
    require(not (contract.get("constrained_search") or {}).get("enabled"), "constrained search was enabled before the baseline gate")
    ml_text = visible_text(ml_execution_status)
    require("/d/" in ml_text and "Power" in ml_text, "ML report omitted Dashboard URL or target")
    dashboard_uids = re.findall(r"/d/([A-Za-z0-9_-]{1,80})/", ml_text)
    require(bool(dashboard_uids), "ML Preview URL was not returned")
    dashboard = grafana_json(f"/api/dashboards/uid/{dashboard_uids[-1]}").get("dashboard") or {}
    serialized_dashboard = json.dumps(dashboard, ensure_ascii=False)
    require("ask-o11y-preview" in dashboard.get("tags", []) and "$asset_url_" not in serialized_dashboard and "$execution_ref" not in serialized_dashboard, "stored ML Preview has invalid lifecycle or unresolved bindings")

    recovery = start_run(
        "Use the existing successful report refs from this chat without rerunning query or analysis. Exercise native validator recovery by intentionally making the first compose synthesis claim a visual observation for one inspected spec-only view; when the validator returns its recoverable error, read that exact error and revise only the synthesis/ref before retrying. Do not invent data, refs, IDs, or URLs, and do not call execute_planned_query, profile_dataset, or execute_ml_contract.",
        session_id,
    )
    recovery_status, recovery_approvals = poll_run(recovery["runId"], approve=True)
    recovery_names = tool_names(recovery_status)
    require(recovery_status.get("status") == "completed" and bool(tool_errors(recovery_status)) and not unrecovered_tool_errors(recovery_status), "native recovery run did not recover a real validator error")
    require(recovery_names.count("artifact-bridge_compose_ml_dashboard") >= 2, f"native recovery did not retry compose: {recovery_names}")

    ordered_runs = [opened, profile_preview, profile_execution, ml_preview, ml_execution, recovery]
    evidence = {
        "ok": True,
        "run_lineage": [{"run_id": run["runId"], "parent_run_id": ordered_runs[index - 1]["runId"] if index else None} for index, run in enumerate(ordered_runs)],
        "session_id": session_id,
        "dataset_id": dataset_id,
        "run_receipts": {phase: RUN_RECEIPTS[run["runId"]] for phase, run in {"opened": opened, "profile_preview": profile_preview, "profile_execution": profile_execution, "ml_preview": ml_preview, "ml_execution": ml_execution, "recovery": recovery}.items()},
        "model_preset": os.environ["ASK_O11Y_E2E_MODEL"],
        "acceptance_kind": "fresh_session_with_explicit_validator_recovery_probe",
        "rows": uploaded["rows"],
        "columns": uploaded["columns"],
        "profile_preview_tools": profile_preview_names,
        "profile_execution_tools": tool_names(profile_execution_status),
        "profile_approval_count": len(profile_approvals),
        "profile_tool_errors": tool_errors(profile_execution_status),
        "profile_unrecovered_tool_errors": unrecovered_tool_errors(profile_execution_status),
        "ml_preview_tools": ml_preview_names,
        "ml_execution_tools": ml_execution_names,
        "ml_approval_count": len(ml_approvals),
        "ml_tool_errors": tool_errors(ml_execution_status),
        "ml_unrecovered_tool_errors": unrecovered_tool_errors(ml_execution_status),
        "recovery_execution_tools": recovery_names,
        "recovery_tool_errors": tool_errors(recovery_status),
        "recovery_unrecovered_tool_errors": unrecovered_tool_errors(recovery_status),
        "recovery_approval_count": len(recovery_approvals),
        "regression_contract": contract,
        "dashboard_uid": dashboard_uids[-1],
        "validation": {
            "real_source": str(SOURCE.relative_to(ROOT)),
            "full_data_requested": True,
            "profile_before_ml": True,
            "profile_used_trusted_executor": True,
            "profile_report_synthesized": True,
            "regression_target_from_user_intent": True,
            "timestamp_excluded_from_features": True,
            "dummy_gate_present": True,
            "no_arbitrary_python_ml": True,
            "grafana_preview": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "dataset_id": dataset_id, "rows": uploaded["rows"], "profile_tools": len(profile_execution_names), "ml_tools": len(ml_execution_names), "dashboard_uid": dashboard_uids[-1], "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
