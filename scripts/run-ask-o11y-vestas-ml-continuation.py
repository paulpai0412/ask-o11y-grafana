#!/usr/bin/env python3
"""Run the explicit Power regression half of an existing real Vestas session."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vestas_e2e_helpers", ROOT / "scripts/run-ask-o11y-vestas-e2e.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Vestas E2E helper is unavailable")
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)
poll_run = helper.poll_run
start_run = helper.start_run
tool_arguments = helper.tool_arguments
tool_errors = helper.tool_errors
tool_names = helper.tool_names
unrecovered_tool_errors = helper.unrecovered_tool_errors
visible_text = helper.visible_text
load_env = helper.load_env
grafana_json = helper.grafana_json
require = helper.require


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--parent-run-id", required=True)
    args = parser.parse_args()
    load_env()
    session_id = args.session_id
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{16,128}", args.parent_run_id)), "invalid parent run identity")
    parent = grafana_json(f"/api/plugins/consensys-asko11y-app/resources/api/agent/runs/{args.parent_run_id}")
    require(parent.get("sessionId") == session_id and parent.get("status") == "completed", "parent must be a completed run in this same session")
    parent_receipt = helper.record_run(parent)
    preview = start_run(
        "現在才要進行明確要求的 supervised regression。請針對這個實際 upload dataset 以物理欄位 `Power` 為連續 target；`Timestamp` 只作 chronological split，不得進 features。請依 observed ontology/data evidence 動態判斷 features，排除 MaxPower、MinPower、StdDevPower、AvgRPow、GenRPM、Scenario 及其他 leakage/proxy 欄位；不可抽樣、截斷、建立 derived dataset，不使用 sample weights。先重新提出 ontology-pinned ML Analysis Preview，確認前不要執行 query、Sandbox 或 Dashboard 寫入。",
        session_id,
    )
    preview_status, _ = poll_run(preview["runId"], approve=False)
    preview_names = tool_names(preview_status)
    require(preview_status.get("status") == "completed" and not tool_errors(preview_status), "ML preview failed")
    require("Analysis Preview" in visible_text(preview_status), "ML preview was not visible")
    require("grafana-query_execute_planned_query" not in preview_names and "sandbox-analysis_execute_ml_contract" not in preview_names, f"ML preview crossed execution boundary: {preview_names}")

    execution = start_run(
        "確認剛才的 Power regression Analysis Preview。請執行完整實際資料，不改寫欄位名稱或 refs；維持 Timestamp chronological split、training-only preprocessing、Dummy baseline gate、holdout only once、低搜尋預算與 no sample weights。完成後讀取完整 facts/artifacts/views，做一次 evidence-bound whole-report synthesis，建立可檢視但不要正式發佈的 Grafana Preview；compose 成功後把 refs.dashboard_ref 以 {dashboard: {$dashboard_ref: refs.dashboard_ref}} 交給 writer，不要複製完整 dashboard JSON；不得用 model-authored arbitrary Python 取代 trusted execute_ml_contract。",
        session_id,
    )
    status, approvals = poll_run(execution["runId"], approve=True)
    names = tool_names(status)
    require(status.get("status") == "completed" and not unrecovered_tool_errors(status), "ML execution failed")
    for required_name in ("grafana-query_execute_planned_query", "sandbox-analysis_execute_ml_contract", "mcp-grafana_update_dashboard"):
        require(required_name in names, f"ML chain omitted {required_name}: {names}")
    require("sandbox-analysis_execute_python_analysis" not in names, "ML path bypassed trusted contract executor")
    plans = tool_arguments(status, "plan_query")
    require(bool(plans), "ML execution did not create a query plan")
    contract: dict[str, Any] = plans[-1].get("analysis_contract") or {}
    features = [str(name) for name in contract.get("features") or []]
    require(contract.get("task_kind") == "regression" and contract.get("target") == "Power", f"unexpected regression contract: {contract}")
    require("Timestamp" not in features and "Power" not in features, f"target/split leaked into features: {features}")
    require(contract.get("split", {}).get("kind") == "chronological_holdout", f"unsafe regression split: {contract.get('split')}")
    require(contract.get("split", {}).get("preprocessing_fit_scope") == "training_only", "preprocessing was not training-only")
    require(contract.get("sample_weight_fields") in (None, []), "sample weights were unexpectedly used")
    text = visible_text(status)
    require("/d/" in text and "Power" in text, "ML report omitted Dashboard URL or target")
    dashboard_uids = re.findall(r"/d/([A-Za-z0-9_-]{1,80})/", text)
    require(bool(dashboard_uids), "ML Preview URL was not returned")
    dashboard = grafana_json(f"/api/dashboards/uid/{dashboard_uids[-1]}").get("dashboard") or {}
    serialized = json.dumps(dashboard, ensure_ascii=False)
    require("ask-o11y-preview" in dashboard.get("tags", []) and "$asset_url_" not in serialized and "$execution_ref" not in serialized, "stored ML Preview contains unresolved bindings")
    evidence = {
        "ok": True,
        "session_id": session_id,
        "preview_tools": preview_names,
        "execution_tools": names,
        "approval_count": len(approvals),
        "tool_errors": tool_errors(status),
        "unrecovered_tool_errors": unrecovered_tool_errors(status),
        "regression_contract": contract,
        "dashboard_uid": dashboard_uids[-1],
        "acceptance_kind": "continuation_not_fresh_e2e",
        "parent_run_id": args.parent_run_id,
        "run_receipts": {"parent": parent_receipt, "ml_preview": helper.RUN_RECEIPTS[preview["runId"]], "ml_execution": helper.RUN_RECEIPTS[execution["runId"]]},
        "run_lineage": [{"run_id": preview["runId"], "parent_run_id": args.parent_run_id}, {"run_id": execution["runId"], "parent_run_id": preview["runId"]}],
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
