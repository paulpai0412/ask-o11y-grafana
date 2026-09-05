"""Host-side raw run receipts for real Ask O11y acceptance, not model-authored flags."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = ROOT / ".scratch/ask-o11y-e2e-events"


def _run_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise ValueError("invalid run identity")
    return value


def record_run(status: dict[str, Any]) -> dict[str, str]:
    run_id = _run_id(status.get("runId"))
    payload = json.dumps(status, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(payload).hexdigest()
    directory = RECEIPTS / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (digest + ".json")
    try:
        with path.open("xb") as handle:
            path.chmod(0o600)
            handle.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("immutable run receipt mismatch")
    return {"run_id": run_id, "sha256": digest}


def read_run(receipt: dict[str, Any], session_id: str) -> dict[str, Any]:
    run_id = _run_id(receipt.get("run_id"))
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid receipt digest")
    try:
        payload = (RECEIPTS / run_id / (digest + ".json")).read_bytes()
        status = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("raw run receipt is missing or invalid") from exc
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("run receipt was modified")
    if not isinstance(status, dict):
        raise ValueError("raw run receipt must be an object")
    if status.get("runId") != run_id or status.get("sessionId") != session_id or status.get("status") != "completed":
        raise ValueError("receipt is not a completed run in this session")
    return status


def tool_calls(status: dict[str, Any]) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    calls = []
    for index, event in enumerate(status.get("events", [])):
        data = event.get("data") or {}
        call_id = data.get("id")
        if event.get("type") == "tool_call_start":
            if not isinstance(call_id, str) or call_id in starts:
                raise ValueError("missing or duplicate tool-call identity")
            arguments = data.get("arguments") or "{}"
            try:
                parsed_args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError as exc:
                raise ValueError("raw tool arguments are invalid") from exc
            if not isinstance(parsed_args, dict):
                raise ValueError("raw tool arguments must be an object")
            starts[call_id] = {"name": data.get("name"), "args": parsed_args}
        elif event.get("type") == "tool_call_result":
            if not isinstance(call_id, str):
                raise ValueError("tool result is missing call identity")
            start = starts.pop(call_id, None)
            if start is None or start["name"] != data.get("name"):
                raise ValueError("tool result has no matching preceding start")
            if not isinstance(data.get("isError"), bool):
                raise ValueError("tool result must explicitly report success or failure")
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("tool result has no content receipt")
            try:
                payload = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                payload = None
            calls.append({**start, "id": call_id, "position": index, "error": bool(data.get("isError")), "result": payload})
    if starts:
        raise ValueError("tool calls have no result receipts")
    return calls


def verify_recovery(evidence: dict[str, Any]) -> dict[str, Any]:
    if not re.fullmatch(r"upload_[a-f0-9]{32}", str(evidence.get("dataset_id", ""))):
        raise ValueError("invalid uploaded dataset identity")
    session_id = _run_id(evidence.get("session_id"))
    receipts = evidence.get("run_receipts")
    if not isinstance(receipts, dict) or "recovery" not in receipts:
        raise ValueError("raw run receipts are required; summaries are not acceptance evidence")
    runs = {phase: read_run(receipt, session_id) for phase, receipt in receipts.items()}
    run_ids = {run["runId"] for run in runs.values()}
    if len(run_ids) != len(runs):
        raise ValueError("phases cannot reuse the same run identity")
    lineage = evidence.get("run_lineage")
    if not isinstance(lineage, list) or len(lineage) != len(runs):
        raise ValueError("explicit continuation lineage is required")
    previous = None
    seen = set()
    for link in lineage:
        if not isinstance(link, dict) or link.get("run_id") not in run_ids or link.get("run_id") in seen or link.get("parent_run_id") != previous:
            raise ValueError("continuation lineage is incomplete or cross-run spliced")
        previous = link["run_id"]
        seen.add(previous)
    if previous != runs["recovery"]["runId"]:
        raise ValueError("recovery is not the final declared continuation")
    if "ml_execution" not in runs:
        raise ValueError("recovery must retain the originating ML run receipt")
    ml_calls = tool_calls(runs["ml_execution"])
    execution_refs = set()
    for call in ml_calls:
        result = call["result"]
        if call["name"] != "sandbox-analysis_execute_ml_contract" or call["error"] or not isinstance(result, dict) or not result.get("ok"):
            continue
        provenance = result.get("provenance") or {}
        if not isinstance(provenance.get("trusted_ml_contract"), bool) or not provenance["trusted_ml_contract"] or (provenance.get("analysis_contract") or {}).get("dataset_id") != evidence["dataset_id"]:
            raise ValueError("ML receipt does not bind the declared uploaded dataset")
        execution_ref = (result.get("refs") or {}).get("execution_ref")
        if isinstance(execution_ref, str):
            execution_refs.add(execution_ref)
    if not execution_refs:
        raise ValueError("missing successful originating ML execution receipt")
    calls = tool_calls(runs["recovery"])
    report_contexts = {(call["result"].get("refs") or {}).get("report_context_ref") for call in calls if call["name"] == "artifact-bridge_prepare_ml_report" and not call["error"] and isinstance(call["result"], dict) and call["result"].get("ok") and call["args"].get("execution_ref") in execution_refs}
    if any(call["name"] in {"grafana-query_execute_planned_query", "sandbox-analysis_profile_dataset", "sandbox-analysis_execute_ml_contract", "sandbox-analysis_execute_python_analysis"} for call in calls):
        raise ValueError("recovery reran successful query or analysis")
    failed = [call for call in calls if call["error"]]
    if not failed:
        raise ValueError("no real failed call to recover")
    for failure in failed:
        result = failure["result"]
        if failure["name"] != "artifact-bridge_compose_ml_dashboard" or not isinstance(result, dict) or not isinstance(result.get("recoverable"), bool) or not result["recoverable"]:
            raise ValueError("recovery includes an unexpected or non-recoverable failure")
        context_ref = failure["args"].get("report_context_ref")
        if not context_ref or context_ref not in report_contexts:
            raise ValueError("failed compose is not bound to the originating ML execution")
        repaired = [call for call in calls if call["position"] > failure["position"] and call["name"] == failure["name"] and not call["error"] and call["args"].get("report_context_ref") == context_ref and call["args"].get("synthesis") != failure["args"].get("synthesis") and isinstance(call["result"], dict) and isinstance(call["result"].get("ok"), bool) and call["result"]["ok"]]
        if not repaired:
            raise ValueError("no successful synthesis correction for the failed report context")
        repair = repaired[0]
        dashboard_ref = (repair["result"].get("refs") or {}).get("dashboard_ref")
        if not dashboard_ref or not any(call["position"] > repair["position"] and call["name"] == "mcp-grafana_update_dashboard" and not call["error"] and isinstance(call["result"], dict) and (call["args"].get("dashboard") or {}).get("$dashboard_ref") == dashboard_ref for call in calls):
            raise ValueError("repaired dashboard has no matching writer receipt")
    return {"ok": True, "native_llm_recovery": True, "session_id": session_id, "run_id": runs["recovery"]["runId"], "recovered_calls": [call["id"] for call in failed]}
