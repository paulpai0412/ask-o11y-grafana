"""Shared workflow-node response and artifact-ref helpers.

The contract is intentionally tiny: MCP tools return plain JSON dicts, and large
intermediate data moves by opaque artifact refs instead of file paths.
"""
from __future__ import annotations

import re
from typing import Any

RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FORBIDDEN_REF_PARTS = {"", ".", ".."}


class WorkflowContractError(ValueError):
    """Raised when a workflow-node response or artifact ref violates contract."""


def make_artifact_ref(run_id: str, *parts: str) -> str:
    """Return an opaque artifact URI like artifact://run_abc123/query-plan."""
    if not RUN_ID_RE.fullmatch(run_id):
        raise WorkflowContractError("run_id must match run_<opaque-id>")
    if len(parts) != 1:
        raise WorkflowContractError("artifact ref must contain exactly one opaque name")
    clean_parts = []
    for part in parts:
        if part in FORBIDDEN_REF_PARTS or "/" in part or "\\" in part:
            raise WorkflowContractError("artifact parts must not be paths")
        if not ARTIFACT_NAME_RE.fullmatch(part):
            raise WorkflowContractError("artifact parts must be opaque names")
        clean_parts.append(part)
    return f"artifact://{run_id}/{'/'.join(clean_parts)}"


def parse_artifact_ref(ref: str) -> tuple[str, tuple[str, ...]]:
    """Validate and split an artifact URI without resolving storage paths."""
    prefix = "artifact://"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise WorkflowContractError("artifact ref must start with artifact://")
    body = ref[len(prefix) :]
    if "?" in body or "#" in body or "@" in body or ":" in body:
        raise WorkflowContractError("artifact ref must be opaque and unqualified")
    pieces = tuple(body.split("/"))
    if len(pieces) < 2:
        raise WorkflowContractError("artifact ref must include run_id and name")
    run_id, parts = pieces[0], pieces[1:]
    # Reuse the stricter generator validation.
    make_artifact_ref(run_id, *parts)
    return run_id, parts


def success_response(
    *,
    step: str,
    run_id: str,
    instruction: str,
    evidence: dict[str, Any] | None = None,
    refs: dict[str, str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "step": step,
        "run_id": run_id,
        "instruction": instruction,
        "evidence": evidence or {},
    }
    if refs:
        for ref in refs.values():
            parse_artifact_ref(ref)
        out["refs"] = refs
    out.update(extra)
    validate_response(out)
    return out


def error_response(
    *,
    step: str,
    error: str,
    instruction: str,
    recoverable: bool = False,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "ok": False,
        "step": step,
        "error": error,
        "recoverable": recoverable,
        "instruction": instruction,
        "evidence": evidence or {},
    }
    validate_response(out)
    return out


def clarification_response(
    *,
    step: str,
    question: str,
    missing_fields: list[str],
    instruction: str,
    suggested_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "ok": False,
        "step": step,
        "status": "clarification_needed",
        "question": question,
        "missing_fields": missing_fields,
        "suggested_defaults": suggested_defaults or {},
        "instruction": instruction,
    }
    validate_response(out)
    return out


def validate_response(resp: dict[str, Any]) -> None:
    if not isinstance(resp, dict):
        raise WorkflowContractError("response must be an object")
    if not isinstance(resp.get("ok"), bool):
        raise WorkflowContractError("response.ok must be boolean")
    for key in ("step", "instruction"):
        if not isinstance(resp.get(key), str) or not resp[key]:
            raise WorkflowContractError(f"response.{key} is required")
    if resp["ok"]:
        if not RUN_ID_RE.fullmatch(str(resp.get("run_id", ""))):
            raise WorkflowContractError("successful responses require opaque run_id")
    elif resp.get("status") == "clarification_needed":
        if not resp.get("question") or not isinstance(resp.get("missing_fields"), list):
            raise WorkflowContractError("clarification requires question and missing_fields")
    else:
        if not isinstance(resp.get("error"), str) or not isinstance(resp.get("recoverable"), bool):
            raise WorkflowContractError("error responses require error and recoverable")
    refs = resp.get("refs", {})
    if refs:
        if not isinstance(refs, dict):
            raise WorkflowContractError("refs must be an object")
        for ref in refs.values():
            parse_artifact_ref(ref)
