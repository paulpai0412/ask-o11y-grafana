#!/usr/bin/env python3
"""Fresh preview-confirm real Ask O11y Engineering profile/EDA E2E."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "ask-o11y-profile-e2e.json"
PROMPT = "請對一號機的熱耗率、原煤耗與平均發電量做 EDA/profile，說明型別、missingness、cardinality 與描述統計，並用 table 呈現；先 preview，未確認前不要查詢或建立 dashboard。"
CONFIRMATION = "確認執行剛才的 EDA/profile preview。使用已授權的真實 Grafana 資料，只執行 profile，不要執行 correlation、predictive、patterns 或 timeseries，並建立預覽中的 table dashboard。"


def load_base() -> Any:
    path = ROOT / "scripts" / "run-ask-o11y-correlation-preview-e2e.py"
    spec = importlib.util.spec_from_file_location("profile_e2e_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Ask O11y E2E helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules["profile_e2e_base"] = module
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def artifact_json(ref: str, name: str) -> dict[str, Any]:
    parts = ref.split("/")
    if len(parts) < 4 or not parts[2].startswith("run_"):
        raise RuntimeError(f"invalid artifact ref: {ref}")
    try:
        value = json.loads((ROOT / ".analysis-artifacts" / "runs" / parts[2] / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read profile artifact: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("profile artifact must be an object")
    return value


def main() -> int:
    base = load_base()
    preview_start = base.start_run(PROMPT)
    preview, preview_approvals = base.poll_run(preview_start["runId"], approve_renderer=False)
    preview_tools = base.starts(preview)
    require(preview.get("status") == "completed" and not [result for result in base.results(preview) if result.get("isError")], "profile preview failed")
    require(set(preview_tools).issubset({"grafana-query_discover_datasets", "grafana-query_inspect_dataset", "data-query-planner_plan_query"}), f"profile preview executed work: {preview_tools}")
    require(not preview_approvals and "profile" in base.visible_text(preview).lower(), "profile preview/confirmation missing")
    session_id = str(preview_start.get("sessionId") or preview.get("sessionId") or "")
    require(bool(session_id), "profile preview session missing")

    execution_start = base.start_run(CONFIRMATION, session_id=session_id)
    execution, approvals = base.poll_run(execution_start["runId"], approve_renderer=True)
    tools = base.starts(execution)
    errors = [result for result in base.results(execution) if result.get("isError")]
    require(execution.get("status") == "completed" and not errors, f"profile execution failed: {errors}")
    for required in ["grafana-query_execute_planned_query", "engineering-analysis_analyze_profile", "grafana-renderer_prepare_dashboard_write", "grafana-renderer_create_dashboard_from_analysis"]:
        require(required in tools, f"profile execution missing {required}: {tools}")
    require(not any(name in tools for name in ["engineering-analysis_analyze_correlation", "engineering-analysis_analyze_predictive", "engineering-analysis_analyze_patterns", "engineering-analysis_analyze_timeseries"]), f"profile invoked unselected method: {tools}")
    arguments = base.tool_arguments(execution, "engineering-analysis_analyze_profile")
    require(len(arguments) == 1 and set(arguments[0].get("fields") or []) == {"heat_rate", "raw_coal_consumption_g", "avg_generation_mw"}, f"profile fields mismatch: {arguments}")
    prepare_args = base.tool_arguments(execution, "grafana-renderer_prepare_dashboard_write")
    create_args = base.tool_arguments(execution, "grafana-renderer_create_dashboard_from_analysis")
    require(len(prepare_args) == 1 and len(create_args) == 1 and create_args[0].get("analysis_result_ref") == prepare_args[0].get("analysis_result_ref") and isinstance(create_args[0].get("approval_ref"), str) and "approval_confirmed" not in create_args[0], "profile renderer did not use a server-issued approval capability")
    method = artifact_json(arguments[0]["frame_ref"], "method-engineering-profile")
    validity = method.get("validity") or {}
    require(method.get("profile", {}).get("rows") == 172 and method.get("profile", {}).get("columns") == 3, f"profile semantics mismatch: {method.get('profile')}")
    require(validity.get("input_rows") == 365 and validity.get("valid_rows") == 172 and validity.get("excluded_rows") == 193, f"profile validity mismatch: {validity}")
    require(len(approvals) >= 1 and "http://" in base.visible_text(execution), "profile Renderer approval/dashboard missing")

    evidence = {"ok": True, "prompt": PROMPT, "preview": {"runId": preview_start["runId"], "sessionId": session_id, "tools": preview_tools, "text": base.visible_text(preview), "errors": []}, "confirmation": CONFIRMATION, "execution": {"runId": execution_start["runId"], "tools": tools, "arguments": arguments[0], "validity": validity, "profile": method["profile"], "approval_count": len(approvals), "text": base.visible_text(execution), "errors": []}, "finance_real_e2e": False, "raw": {"preview_status": base.sanitize(preview), "execution_status": base.sanitize(execution)}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "preview_run": preview_start["runId"], "execution_run": execution_start["runId"], "tools": tools, "validity": validity, "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
