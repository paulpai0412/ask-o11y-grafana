#!/usr/bin/env python3
"""Authenticated MCP boundary for ephemeral OpenSandbox Python analysis."""
from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import io
import importlib.util
import json
import os
import sys
import tempfile
import time
import tomllib
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workflow_node = load_module("workflow_node", ROOT / "workflow_node.py")
artifact_store = load_module("artifact_store", ROOT / "artifact_store.py")
mcp_security = load_module("mcp_security", ROOT / "mcp_security.py")
artifact_assets = load_module("artifact_assets", ROOT / "artifact_assets.py")
ontology_contract = load_module("ontology_contract", ROOT / "ontology_contract.py")
ArtifactStore = artifact_store.ArtifactStore
WorkflowContractError = workflow_node.WorkflowContractError
authenticate_headers = mcp_security.authenticate_headers
error_response = workflow_node.error_response
parse_artifact_ref = workflow_node.parse_artifact_ref
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host
success_response = workflow_node.success_response

try:
    PORT = int(os.environ.get("SANDBOX_ANALYSIS_MCP_PORT", "8777"))
except ValueError:
    PORT = 8777

SERVER_INFO = {"name": "sandbox-analysis-mcp", "version": "0.4.0"}
PROTOCOL = "2025-03-26"
MAX_CODE_BYTES = 32 * 1024
MAX_RPC_BODY_BYTES = 128 * 1024
MAX_INPUT_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024
MAX_INLINE_RESULT_BYTES = 32 * 1024
MAX_OUTPUT_FIELDS = 200
DEFAULT_SEED = 42
ARTIFACT_PUBLIC_BASE = os.environ.get("ARTIFACT_PUBLIC_BASE", "http://127.0.0.1:8777").rstrip("/")
ARTIFACTS = ArtifactStore(os.environ.get("ANALYSIS_ARTIFACT_ROOT", ROOT / ".analysis-artifacts" / "runs"))
ARTIFACTS.cleanup_expired()

TOOLS = [
    {
        "name": "execute_python_analysis",
        "description": "Execute generated Python in a fresh network-denied OpenSandbox over one authorized Grafana frame after preview confirmation. The sandbox receives df, pd, np, display(value), and emit(value, name=None). Name JSON results *.json for bounded inline return and DataFrame/string downloads *.csv for a signed URL. The offline image includes SciPy, Matplotlib, Seaborn, Plotly, scikit-learn, statsmodels, SHAP, CPU-only XGBoost, LightGBM, imbalanced-learn, and Optuna; use Matplotlib PNG when a plot may become a Grafana panel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_ref": {"type": "string", "description": "Opaque authorized grafana-frame artifact ref."},
                "python_code": {"type": "string", "maxLength": MAX_CODE_BYTES, "description": "Python source executed only inside the isolated sandbox."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED},
            },
            "required": ["frame_ref", "python_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_python_analyses",
        "description": "List the authenticated user's recent Sandbox Analysis revisions so a later conversation can rediscover opaque refs without raw data.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "inspect_python_analysis",
        "description": "Read one authorized Sandbox Analysis revision's generated Python, fields, output summary, and provenance for revision; never returns frame rows.",
        "inputSchema": {
            "type": "object",
            "properties": {"provenance_ref": {"type": "string", "description": "Opaque sandbox-provenance ref returned by execute/list/revise."}},
            "required": ["provenance_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "revise_python_analysis",
        "description": "Execute replacement Python against the same authorized persisted Grafana frame as an earlier Sandbox Analysis revision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provenance_ref": {"type": "string", "description": "Opaque prior sandbox-provenance ref."},
                "python_code": {"type": "string", "maxLength": MAX_CODE_BYTES, "description": "Complete replacement Python source."},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": DEFAULT_SEED},
            },
            "required": ["provenance_ref", "python_code"],
            "additionalProperties": False,
        },
    },
]


def context_from_headers(headers) -> dict[str, str] | None:
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-Actor-User-Id") or headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    if org and user:
        return {"org_id": str(org), "user_id": str(user)}
    return None


def inject_header_context(msg: dict[str, Any], headers) -> dict[str, Any]:
    if msg.get("method") != "tools/call":
        return msg
    params = msg.setdefault("params", {})
    if not isinstance(params, dict):
        return msg
    args = params.setdefault("arguments", {})
    if not isinstance(args, dict):
        return msg
    args.pop("context", None)
    args.pop("_server_context", None)
    context = context_from_headers(headers)
    if context is not None:
        args["_server_context"] = context
    return msg


def context_from_args(args: dict[str, Any]) -> dict[str, str]:
    raw = args.get("_server_context")
    if isinstance(raw, dict) and raw.get("org_id") and raw.get("user_id"):
        return {"org_id": str(raw["org_id"]), "user_id": str(raw["user_id"])}
    raise WorkflowContractError("verified artifact context is required")


def sandbox_policy() -> dict[str, Any]:
    return {
        "timeout_seconds": 600,
        "ready_timeout_seconds": 20,
        "resource": {"cpu": "1", "memory": "1Gi"},
        "network_default_action": "deny",
        "env": {},
        "volumes": [],
    }


def runtime_settings() -> dict[str, str]:
    image = os.environ.get("SANDBOX_IMAGE", "").strip()
    if not image:
        raise RuntimeError("SANDBOX_IMAGE is required")
    allow_unpinned = os.environ.get("SANDBOX_ALLOW_UNPINNED_IMAGE") == "1"
    if "@sha256:" not in image and not allow_unpinned:
        raise RuntimeError("SANDBOX_IMAGE must be digest-pinned; set SANDBOX_ALLOW_UNPINNED_IMAGE=1 only for local development")
    runtime_class = os.environ.get("SANDBOX_RUNTIME_CLASS", "").strip().lower()
    if runtime_class not in {"gvisor", "kata", "firecracker"}:
        if not (runtime_class == "runc" and os.environ.get("SANDBOX_ALLOW_RUNC") == "1"):
            raise RuntimeError("SANDBOX_RUNTIME_CLASS must be gvisor, kata, or firecracker; runc requires SANDBOX_ALLOW_RUNC=1 for local development")
    config_path = Path(os.environ.get("SANDBOX_SERVER_CONFIG", ""))
    if not config_path.is_file():
        raise RuntimeError("SANDBOX_SERVER_CONFIG must reference the OpenSandbox server TOML used by the control plane")
    try:
        config_bytes = config_path.read_bytes()
        server_config = tomllib.loads(config_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"cannot validate SANDBOX_SERVER_CONFIG: {exc}") from exc
    configured_type = str((server_config.get("secure_runtime") or {}).get("type") or "runc").lower()
    if configured_type != runtime_class:
        raise RuntimeError(f"SANDBOX_RUNTIME_CLASS {runtime_class!r} does not match control-plane secure_runtime {configured_type!r}")
    egress = server_config.get("egress") or {}
    if egress.get("mode") != "dns+nft" or not bool(egress.get("disable_ipv6")):
        raise RuntimeError("OpenSandbox control-plane config must enforce dns+nft egress with IPv6 disabled")
    protocol = os.environ.get("SANDBOX_PROTOCOL", "http").strip().lower()
    if protocol not in {"http", "https"}:
        raise RuntimeError("SANDBOX_PROTOCOL must be http or https")
    return {
        "image": image,
        "domain": os.environ.get("SANDBOX_DOMAIN", "localhost:8080").strip(),
        "protocol": protocol,
        "runtime_class": configured_type,
        "server_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
    }


def read_authorized_frame(context: dict[str, str], frame_ref: str) -> tuple[str, dict[str, Any]]:
    run_id, parts = parse_artifact_ref(frame_ref)
    if parts != ("grafana-frame",):
        raise WorkflowContractError("frame_ref must reference a grafana-frame artifact")
    frames = ARTIFACTS.read_json(context, frame_ref)
    if not isinstance(frames, list) or len(frames) != 1 or not isinstance(frames[0], dict):
        raise WorkflowContractError("grafana-frame artifact must contain exactly one frame")
    return run_id, frames[0]


def validate_frame(frame: dict[str, Any]) -> tuple[list[str], int]:
    fields = frame.get("schema", {}).get("fields")
    values = frame.get("data", {}).get("values")
    if not isinstance(fields, list) or not isinstance(values, list) or len(fields) != len(values):
        raise WorkflowContractError("columnar frame fields/values must be equal-length arrays")
    names = [field.get("name") if isinstance(field, dict) else None for field in fields]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise WorkflowContractError("columnar frame field names must be unique non-empty strings")
    if any(not isinstance(column, list) for column in values):
        raise WorkflowContractError("columnar frame values must be arrays")
    lengths = {len(column) for column in values}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise WorkflowContractError("columnar frame columns must have one shared non-zero row count")
    return [str(name) for name in names], next(iter(lengths))


def read_plan_contract(context: dict[str, str], source_run_id: str, field_names: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        plan = ARTIFACTS.read_json(context, f"artifact://{source_run_id}/query-plan")
    except WorkflowContractError:
        plan = {}
    if not isinstance(plan, dict):
        raise WorkflowContractError("query plan must be an object")
    try:
        ontology_contract.verify_plan(plan)
    except ValueError as exc:
        raise WorkflowContractError(str(exc)) from exc
    contract = plan.get("analysis_input_contract") if isinstance(plan, dict) else {}
    rules = contract.get("validity_rules", []) if isinstance(contract, dict) else []
    if not isinstance(rules, list):
        raise WorkflowContractError("query plan validity_rules must be an array")
    for rule in rules:
        if not isinstance(rule, dict):
            raise WorkflowContractError("query plan validity rule must be an object")
        field = rule.get("field")
        accepted = rule.get("accepted_values")
        if not isinstance(field, str) or field not in field_names or not isinstance(accepted, list) or not accepted:
            raise WorkflowContractError("query plan validity rule is incomplete")
    semantic = {key: plan.get(key) for key in ("ontology", "analysis_contract", "plan_sha256") if plan.get(key) is not None}
    if semantic and set(semantic) != {"ontology", "analysis_contract", "plan_sha256"}:
        raise WorkflowContractError("ontology analysis plan contract is incomplete")
    return rules, semantic


def wrapped_code(python_code: str, seed: int) -> str:
    # The full traceback belongs in the authorized execution artifact; the
    # model-visible response is redacted separately in execute_python_analysis.
    return f"from capture import run\nrun({python_code!r}, '/tmp/input-frame.json', {seed})"


def serialize_execution(execution: Any) -> dict[str, Any]:
    results = []
    for result in execution.result:
        results.append({"text": result.text, "timestamp": result.timestamp, "mime": dict(result.extra_properties)})
    error = None
    if execution.error is not None:
        error = {
            "name": execution.error.name,
            "value": execution.error.value,
            "timestamp": execution.error.timestamp,
            "traceback": list(execution.error.traceback),
        }
    complete = None
    if execution.complete is not None:
        complete = {
            "timestamp": execution.complete.timestamp,
            "execution_time_in_millis": execution.complete.execution_time_in_millis,
        }
    return {
        "execution_id": execution.id,
        "execution_count": execution.execution_count,
        "exit_code": execution.exit_code,
        "results": results,
        "stdout": [{"text": item.text, "timestamp": item.timestamp} for item in execution.logs.stdout],
        "stderr": [{"text": item.text, "timestamp": item.timestamp} for item in execution.logs.stderr],
        "error": error,
        "complete": complete,
    }


def read_captured_outputs(filesystem: Any) -> list[dict[str, Any]]:
    manifest_bytes = filesystem.read_bytes("/tmp/sandbox-output/manifest.json", range_header=f"bytes=0-{MAX_OUTPUT_BYTES}")
    if len(manifest_bytes) > MAX_OUTPUT_BYTES:
        raise WorkflowContractError("sandbox output manifest exceeds limit")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("sandbox output manifest is invalid") from exc
    if not isinstance(manifest, list) or len(manifest) > 20:
        raise WorkflowContractError("sandbox output manifest must contain at most 20 items")
    allowed_mime = {"text/plain", "text/csv", "text/html", "image/png", "application/json", "application/vnd.plotly.v1+json"}
    outputs = []
    total = len(manifest_bytes)
    output_root = Path("/tmp/sandbox-output")
    for item in manifest:
        if not isinstance(item, dict) or set(item) != {"path", "mime_type", "display_name"}:
            raise WorkflowContractError("sandbox output manifest item is invalid")
        path = Path(str(item["path"]))
        mime_type = str(item["mime_type"])
        display_name = item["display_name"]
        if not isinstance(display_name, str) or not display_name or len(display_name) > 120:
            raise WorkflowContractError("sandbox output display name is invalid")
        if path.parent != output_root or mime_type not in allowed_mime:
            raise WorkflowContractError("sandbox output path or MIME type is not allowed")
        payload = filesystem.read_bytes(str(path), range_header=f"bytes=0-{MAX_OUTPUT_BYTES}")
        total += len(payload)
        if total > MAX_OUTPUT_BYTES:
            raise WorkflowContractError("sandbox captured outputs exceed limit")
        if mime_type == "image/png":
            outputs.append({"text": None, "timestamp": 0, "mime": {mime_type: base64.b64encode(payload).decode("ascii")}, "display_name": display_name})
        else:
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkflowContractError("sandbox text output is not UTF-8") from exc
            outputs.append({"text": text if mime_type == "text/plain" else None, "timestamp": 0, "mime": {} if mime_type == "text/plain" else {mime_type: text}, "display_name": display_name})
    return outputs


def read_captured_logs(filesystem: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    logs = []
    for name in ("stdout", "stderr"):
        payload = filesystem.read_bytes(f"/tmp/sandbox-output/{name}.txt", range_header=f"bytes=0-{MAX_LOG_BYTES}")
        if len(payload) > MAX_LOG_BYTES:
            raise WorkflowContractError(f"sandbox {name} exceeds limit")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowContractError(f"sandbox {name} is not UTF-8") from exc
        logs.append([] if not text else [{"text": text, "timestamp": 0}])
    return logs[0], logs[1]


def read_input_audit(filesystem: Any) -> dict[str, Any]:
    payload = filesystem.read_bytes("/tmp/sandbox-output/audit.json", range_header="bytes=0-65535")
    try:
        audit = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowContractError("sandbox input audit is invalid") from exc
    required = {"input_rows", "valid_rows", "excluded_rows", "rules"}
    if not isinstance(audit, dict) or set(audit) != required:
        raise WorkflowContractError("sandbox input audit is incomplete")
    return audit


def execute_opensandbox(frame_bundle_json: str, python_code: str, seed: int) -> dict[str, Any]:
    from code_interpreter.models.code import SupportedLanguage
    from code_interpreter.sync.code_interpreter import CodeInterpreterSync
    from opensandbox.config import ConnectionConfigSync
    from opensandbox.models.execd_sync import ExecutionHandlersSync
    from opensandbox.models.filesystem import WriteEntry
    from opensandbox.models.sandboxes import NetworkPolicy
    from opensandbox.sync.sandbox import SandboxSync

    settings = runtime_settings()
    policy = sandbox_policy()
    connection = ConnectionConfigSync(
        domain=settings["domain"],
        protocol=settings["protocol"],
        api_key=os.environ.get("SANDBOX_API_KEY") or None,
        request_timeout=timedelta(seconds=policy["timeout_seconds"] + 10),
    )
    sandbox = None
    try:
        sandbox = SandboxSync.create(
            settings["image"],
            timeout=timedelta(seconds=policy["timeout_seconds"]),
            ready_timeout=timedelta(seconds=policy["ready_timeout_seconds"]),
            env=policy["env"],
            metadata={"service": SERVER_INFO["name"]},
            resource=policy["resource"],
            network_policy=NetworkPolicy(defaultAction=policy["network_default_action"]),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            volumes=policy["volumes"],
            connection_config=connection,
        )
        sandbox.files.write_files([WriteEntry(path="/tmp/input-frame.json", data=frame_bundle_json, mode=600)])
        interpreter = CodeInterpreterSync.create(sandbox=sandbox)
        execution = interpreter.codes.run(
            wrapped_code(python_code, seed),
            language=SupportedLanguage.PYTHON,
            handlers=ExecutionHandlersSync(skip_accumulation=True),
        )
        serialized = serialize_execution(execution)
        try:
            serialized["input_audit"] = read_input_audit(sandbox.files)
            serialized["stdout"], serialized["stderr"] = read_captured_logs(sandbox.files)
            serialized["results"].extend(read_captured_outputs(sandbox.files))
        except Exception:
            if execution.error is None:
                raise
        return serialized
    finally:
        if sandbox is not None:
            with contextlib.suppress(Exception):
                sandbox.kill()
            sandbox.close()


def output_asset_summary(execution: dict[str, Any], execution_ref: str) -> list[dict[str, Any]]:
    assets = []
    for index, result in enumerate(execution.get("results", [])):
        mime = result.get("mime") if isinstance(result, dict) else None
        if not isinstance(mime, dict) or not isinstance(mime.get("image/png"), str):
            continue
        assets.append({
            "output_index": index,
            "display_name": result.get("display_name") or f"Output {index + 1}",
            "mime_type": "image/png",
            "$execution_ref": execution_ref,
        })
    return assets


def inline_result_summary(execution: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    inline_results = []
    total = 0
    omitted = False
    for index, result in enumerate(execution.get("results", [])):
        if not isinstance(result, dict):
            continue
        mime = result.get("mime")
        text = result.get("text")
        if isinstance(text, str):
            mime_type, raw = "text/plain", text
        else:
            json_data = mime.get("application/json") if isinstance(mime, dict) else None
            if not isinstance(json_data, str):
                continue
            mime_type, raw = "application/json", json_data
        size = len(raw.encode("utf-8"))
        if size > MAX_INLINE_RESULT_BYTES - total:
            omitted = True
            continue
        try:
            value = raw if mime_type == "text/plain" else json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            omitted = True
            continue
        inline_results.append({"output_index": index, "display_name": result.get("display_name") or f"Output {index + 1}", "mime_type": mime_type, "value": value})
        total += size
    return inline_results, omitted


def output_download_summary(execution: dict[str, Any], execution_ref: str, context: dict[str, str]) -> list[dict[str, Any]]:
    try:
        expires_at = int(time.time()) + int(ARTIFACTS.retention_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorkflowContractError("artifact retention is invalid") from exc
    downloads = []
    for index, result in enumerate(execution.get("results", [])):
        mime = result.get("mime") if isinstance(result, dict) else None
        csv_data = mime.get("text/csv") if isinstance(mime, dict) else None
        if not isinstance(csv_data, str) or len(csv_data.encode("utf-8")) > artifact_assets.MAX_ASSET_BYTES:
            continue
        downloads.append({
            "output_index": index,
            "display_name": result.get("display_name") or f"Output {index + 1}",
            "mime_type": "text/csv",
            "url": artifact_assets.sign_output_url(public_base=ARTIFACT_PUBLIC_BASE, secret=os.environ.get("MCP_SHARED_TOKEN", ""), context=context, execution_ref=execution_ref, output_index=index, expires_at=expires_at),
            "expires_at": expires_at,
        })
    return downloads


def output_summary(execution: dict[str, Any]) -> dict[str, Any]:
    mime_types = set()
    output_names = []
    tabular_outputs = []
    stdout_lines = 0
    for index, result in enumerate(execution["results"]):
        if result.get("text") is not None:
            mime_types.add("text/plain")
        mime = result.get("mime", {})
        mime_types.update(str(key) for key in mime)
        if isinstance(result.get("display_name"), str):
            output_names.append(result["display_name"])
        csv_data = mime.get("text/csv") if isinstance(mime, dict) else None
        if isinstance(csv_data, str):
            reader = csv.DictReader(io.StringIO(csv_data))
            rows = list(reader)
            fields = []
            for name in (reader.fieldnames or [])[:MAX_OUTPUT_FIELDS]:
                values = [row.get(name, "") for row in rows[:100] if row.get(name, "") != ""]
                logical_type = "string"
                if values:
                    try:
                        for value in values:
                            float(value)
                        logical_type = "number"
                    except ValueError:
                        try:
                            for value in values:
                                datetime.fromisoformat(value.replace("Z", "+00:00"))
                            logical_type = "time"
                        except ValueError:
                            logical_type = "string"
                fields.append({"name": name, "type": logical_type})
            tabular_outputs.append({"output_index": index, "display_name": result.get("display_name") or f"Output {index + 1}", "row_count": len(rows), "fields": fields})
    for item in execution["stdout"]:
        stdout_lines += len(str(item.get("text", "")).splitlines())
    inline_results, inline_results_truncated = inline_result_summary(execution)
    return {
        "result_count": len(execution["results"]),
        "inline_results": inline_results,
        "inline_results_truncated": inline_results_truncated,
        "mime_types": sorted(mime_types),
        "output_names": output_names,
        "tabular_outputs": tabular_outputs,
        "stdout_lines": stdout_lines,
        "stderr_lines": sum(len(str(item.get("text", "")).splitlines()) for item in execution["stderr"]),
    }


def execute_python_analysis(
    args: dict[str, Any],
    executor: Callable[[str, str, int], dict[str, Any]] = execute_opensandbox,
    *,
    step: str = "execute_python_analysis",
    parent_provenance_ref: str | None = None,
) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"frame_ref", "python_code", "seed", "context", "_server_context"})
    if unexpected:
        return error_response(step=step, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only the declared opaque frame ref, Python source, and seed.")
    frame_ref = args.get("frame_ref")
    python_code = args.get("python_code")
    seed = args.get("seed", DEFAULT_SEED)
    if not isinstance(frame_ref, str):
        return error_response(step=step, error="frame_ref is required", recoverable=False, instruction="Stop; Grafana Query must return a frame_ref first.")
    if not isinstance(python_code, str) or not python_code.strip():
        return error_response(step=step, error="python_code is required", recoverable=False, instruction="Stop; provide the confirmed Python analysis source.")
    code_bytes = python_code.encode("utf-8")
    if len(code_bytes) > MAX_CODE_BYTES:
        return error_response(step=step, error=f"python_code exceeds {MAX_CODE_BYTES} bytes", recoverable=False, instruction="Stop; reduce the code to the requested analysis only.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        return error_response(step=step, error="seed must be an integer from 0 to 4294967295", recoverable=False, instruction="Stop; provide a valid deterministic seed.")
    try:
        context = context_from_args(args)
        source_run_id, frame = read_authorized_frame(context, frame_ref)
        field_names, row_count = validate_frame(frame)
        validity_rules, semantic_contract = read_plan_contract(context, source_run_id, field_names)
        frame_bundle_json = json.dumps({"frame": frame, "validity_rules": validity_rules, "semantic_contract": semantic_contract}, ensure_ascii=False, separators=(",", ":"))
    except (PermissionError, WorkflowContractError, ValueError, TypeError) as exc:
        return error_response(step=step, error=str(exc), recoverable=False, instruction="Stop; the input frame is invalid or not authorized.")
    if len(frame_bundle_json.encode("utf-8")) > MAX_INPUT_BUNDLE_BYTES:
        return error_response(step=step, error=f"authorized input exceeds {MAX_INPUT_BUNDLE_BYTES} bytes", recoverable=False, instruction="Stop; request a smaller bounded Grafana query.")
    code_sha256 = hashlib.sha256(code_bytes).hexdigest()
    try:
        execution = executor(frame_bundle_json, python_code, seed)
    except Exception as exc:
        return error_response(step=step, error=f"sandbox execution unavailable: {type(exc).__name__}", recoverable=False, instruction="Stop; inspect service-side logs; never expose input data through exception text or execute this code on the MCP host.")
    encoded_execution = json.dumps(execution, ensure_ascii=False).encode("utf-8")
    if len(encoded_execution) > MAX_OUTPUT_BYTES:
        return error_response(step=step, error=f"sandbox output exceeds {MAX_OUTPUT_BYTES} bytes", recoverable=False, instruction="Stop; request smaller displayed outputs.")
    validity = execution.get("input_audit")
    audit_counts = [validity.get(key) for key in ("input_rows", "valid_rows", "excluded_rows")] if isinstance(validity, dict) else []
    if (
        not isinstance(validity, dict)
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in audit_counts)
        or validity.get("input_rows") != row_count
        or validity.get("valid_rows", 0) + validity.get("excluded_rows", 0) != row_count
        or validity.get("rules") != validity_rules
    ):
        return error_response(step=step, error="sandbox execution returned an invalid trusted input audit", recoverable=False, instruction="Stop; do not trust outputs without host-verified validity evidence.")
    settings = runtime_settings() if executor is execute_opensandbox else {"image": "self-check", "runtime_class": "fake"}
    summary = output_summary(execution)
    output_run_id = ARTIFACTS.create_run(context)
    code_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-code", {"sha256": code_sha256, "source": python_code})
    provenance = {
        "runtime": "opensandbox",
        "runtime_class": settings["runtime_class"],
        "server_config_sha256": settings.get("server_config_sha256"),
        "image": settings["image"],
        "code_sha256": code_sha256,
        "code_ref": code_ref,
        "input_frame_ref": frame_ref,
        "input_fields": field_names,
        "seed": seed,
        "network": "deny",
        "resource": sandbox_policy()["resource"],
        "limits": {
            "timeout_seconds": sandbox_policy()["timeout_seconds"],
            "source_bytes": MAX_CODE_BYTES,
            "rpc_body_bytes": MAX_RPC_BODY_BYTES,
            "input_bundle_bytes": MAX_INPUT_BUNDLE_BYTES,
            "captured_execution_bytes": MAX_OUTPUT_BYTES,
            "stdout_stderr_bytes_each": MAX_LOG_BYTES,
            "inline_result_bytes": MAX_INLINE_RESULT_BYTES,
            "output_fields": MAX_OUTPUT_FIELDS,
            "signed_download_bytes": artifact_assets.MAX_ASSET_BYTES,
        },
        "validity": validity,
        "ontology": semantic_contract.get("ontology"),
        "analysis_contract": semantic_contract.get("analysis_contract"),
        "plan_sha256": semantic_contract.get("plan_sha256"),
        "output_summary": summary,
        "parent_provenance_ref": parent_provenance_ref,
    }
    execution_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-execution", execution)
    summary["assets"] = output_asset_summary(execution, execution_ref)
    summary["downloads"] = output_download_summary(execution, execution_ref, context)
    provenance_ref = ARTIFACTS.write_json(context, output_run_id, "sandbox-provenance", provenance)
    refs = {"execution_ref": execution_ref, "provenance_ref": provenance_ref}
    execution_error = execution.get("error")
    if execution_error:
        error_name = execution_error.get("name") if isinstance(execution_error, dict) else None
        if error_name in {"SyntaxError", "IndentationError"}:
            return error_response(
                step=step,
                error=f"generated Python has a {error_name}; fix the syntax and resubmit complete corrected code",
                recoverable=True,
                instruction="Do not rerun the query; call execute_python_analysis once more with the same frame_ref and corrected python_code. Never expose exception values in prose.",
                evidence={"refs": refs, "code_sha256": code_sha256},
            )
        return error_response(
            step=step,
            error="sandbox Python failed; details retained only in the authorized execution artifact",
            recoverable=False,
            instruction="Stop and report the opaque execution_ref; do not expose exception values or silently execute replacement code.",
            evidence={"refs": refs, "code_sha256": code_sha256},
        )
    return success_response(
        step=step,
        run_id=output_run_id,
        refs=refs,
        instruction="Use the opaque execution_ref and output metadata as analysis evidence. A Grafana Dashboard exists only after the approved built-in Grafana writer returns a URL.",
        evidence={"validity": validity},
        output_summary=summary,
        provenance={key: value for key, value in provenance.items() if key != "code_ref"},
    )


def read_provenance(context: dict[str, str], provenance_ref: str) -> dict[str, Any]:
    _run_id, parts = parse_artifact_ref(provenance_ref)
    if parts != ("sandbox-provenance",):
        raise WorkflowContractError("provenance_ref must reference sandbox-provenance")
    provenance = ARTIFACTS.read_json(context, provenance_ref)
    if not isinstance(provenance, dict) or not isinstance(provenance.get("input_frame_ref"), str) or not isinstance(provenance.get("code_ref"), str):
        raise WorkflowContractError("sandbox provenance artifact is incomplete")
    return provenance


def list_python_analyses(args: dict[str, Any]) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"context", "_server_context"})
    if unexpected:
        return error_response(step="list_python_analyses", error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; this tool accepts no model-provided arguments.")
    try:
        context = context_from_args(args)
        refs = ARTIFACTS.list_refs(context, "sandbox-provenance", limit=20)
        analyses = []
        for ref in refs:
            provenance = read_provenance(context, ref)
            analyses.append({
                "provenance_ref": ref,
                "code_sha256": provenance.get("code_sha256"),
                "input_fields": provenance.get("input_fields", []),
                "output_summary": provenance.get("output_summary", {}),
                "parent_provenance_ref": provenance.get("parent_provenance_ref"),
            })
    except (PermissionError, WorkflowContractError, OSError) as exc:
        return error_response(step="list_python_analyses", error=str(exc), recoverable=False, instruction="Stop; recent analyses could not be listed securely.")
    return success_response(step="list_python_analyses", run_id="run_listing", instruction="Use inspect_python_analysis on the matching revision before generating replacement code.", analyses=analyses)


def inspect_python_analysis(args: dict[str, Any]) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"provenance_ref", "context", "_server_context"})
    if unexpected:
        return error_response(step="inspect_python_analysis", error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only provenance_ref.")
    provenance_ref = args.get("provenance_ref")
    if not isinstance(provenance_ref, str):
        return error_response(step="inspect_python_analysis", error="provenance_ref is required", recoverable=False, instruction="Stop; list recent Sandbox analyses first.")
    try:
        context = context_from_args(args)
        provenance = read_provenance(context, provenance_ref)
        code = ARTIFACTS.read_json(context, provenance["code_ref"])
        if not isinstance(code, dict) or not isinstance(code.get("source"), str):
            raise WorkflowContractError("sandbox code artifact is incomplete")
    except (PermissionError, WorkflowContractError) as exc:
        return error_response(step="inspect_python_analysis", error=str(exc), recoverable=False, instruction="Stop; the analysis revision is invalid or unauthorized.")
    return success_response(
        step="inspect_python_analysis",
        run_id=parse_artifact_ref(provenance_ref)[0],
        instruction="Revise the complete Python source with revise_python_analysis; raw frame rows are intentionally not returned.",
        python_code=code["source"],
        provenance_ref=provenance_ref,
        code_sha256=provenance.get("code_sha256"),
        input_fields=provenance.get("input_fields", []),
        output_summary=provenance.get("output_summary", {}),
        parent_provenance_ref=provenance.get("parent_provenance_ref"),
    )


def revise_python_analysis(args: dict[str, Any], executor: Callable[[str, str, int], dict[str, Any]] = execute_opensandbox) -> dict[str, Any]:
    unexpected = sorted(set(args) - {"provenance_ref", "python_code", "seed", "context", "_server_context"})
    if unexpected:
        return error_response(step="revise_python_analysis", error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only provenance_ref, replacement Python, and seed.")
    provenance_ref = args.get("provenance_ref")
    if not isinstance(provenance_ref, str):
        return error_response(step="revise_python_analysis", error="provenance_ref is required", recoverable=False, instruction="Stop; list and inspect a prior revision first.")
    try:
        context = context_from_args(args)
        provenance = read_provenance(context, provenance_ref)
    except (PermissionError, WorkflowContractError) as exc:
        return error_response(step="revise_python_analysis", error=str(exc), recoverable=False, instruction="Stop; the prior revision is invalid or unauthorized.")
    return execute_python_analysis(
        {"frame_ref": provenance["input_frame_ref"], "python_code": args.get("python_code"), "seed": args.get("seed", DEFAULT_SEED), "_server_context": context},
        executor=executor,
        step="revise_python_analysis",
        parent_provenance_ref=provenance_ref,
    )


def rpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_rpc(msg: dict[str, Any]):
    method, rid = msg.get("method", ""), msg.get("id")
    if method == "initialize":
        return rpc_result(rid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return rpc_result(rid, {})
    if method == "tools/list":
        return rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        tool = next((item for item in TOOLS if item["name"] == name), None)
        if tool is None:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        allowed = set(tool["inputSchema"]["properties"]) | {"context", "_server_context"}
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            out = error_response(step=name, error="unsupported tool arguments: " + ", ".join(unexpected), recoverable=False, instruction="Stop; pass only arguments declared by this tool schema.")
        else:
            handlers = {
                "execute_python_analysis": execute_python_analysis,
                "list_python_analyses": list_python_analyses,
                "inspect_python_analysis": inspect_python_analysis,
                "revise_python_analysis": revise_python_analysis,
            }
            out = handlers[name](arguments)
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": not out.get("ok", False)})
    if rid is None:
        return None
    return rpc_error(rid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: Any = None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/assets/"):
            token = self.path.removeprefix("/assets/").split("?", 1)[0]
            try:
                data, mime_type, _display_name = artifact_assets.read_signed_output(token, secret=os.environ.get("MCP_SHARED_TOKEN", ""), artifacts=ARTIFACTS)
            except (PermissionError, OSError, ValueError):
                return self._send(403, {"error": "artifact URL is invalid or expired"})
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            if mime_type == "text/csv":
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(_display_name, safe=""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", os.environ.get("GRAFANA_PUBLIC_ORIGIN", "http://localhost:3000"))
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(405 if self.path.rstrip("/") == "/mcp" else 404, {"error": "POST JSON-RPC to /mcp"})

    def do_DELETE(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            return self._send(404, {"error": "not found"})
        if authenticate_headers(self.headers) is None:
            return self._send(401, {"error": "authenticated MCP service identity is required"})
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return self._send(400, rpc_error(None, -32700, "invalid Content-Length"))
        if content_length < 0 or content_length > MAX_RPC_BODY_BYTES:
            return self._send(413, rpc_error(None, -32000, f"request body exceeds {MAX_RPC_BODY_BYTES} bytes"))
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, rpc_error(None, -32700, "parse error"))
        messages = payload if isinstance(payload, list) else [payload]
        replies = [reply for message in messages if (reply := handle_rpc(inject_header_context(message, self.headers))) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):  # noqa: A002
        sys.stderr.write("sandbox-analysis-mcp " + format % args + "\n")


def self_check() -> None:
    global ARTIFACTS
    original = ARTIFACTS
    with tempfile.TemporaryDirectory() as tmp:
        ARTIFACTS = ArtifactStore(Path(tmp) / "runs")
        context = {"org_id": "1", "user_id": "self-check"}
        source_run = ARTIFACTS.create_run(context)
        frame_ref = ARTIFACTS.write_json(
            context,
            source_run,
            "grafana-frame",
            [{"schema": {"fields": [{"name": "x"}, {"name": "heat_rate_valid"}]}, "data": {"values": [[1, 2, 3], [True, False, True]]}}],
        )
        ARTIFACTS.write_json(
            context,
            source_run,
            "query-plan",
            {"analysis_input_contract": {"validity_rules": [{"field": "heat_rate_valid", "accepted_values": [True], "applies_to": ["x"]}]}},
        )
        observed = {}

        def fake_executor(frame_bundle_json: str, code: str, seed: int) -> dict[str, Any]:
            try:
                bundle = json.loads(frame_bundle_json)
            except json.JSONDecodeError as exc:
                raise AssertionError("invalid test frame bundle") from exc
            observed.update({"bundle": bundle, "code": code, "seed": seed})
            return {
                "execution_id": "fake",
                "execution_count": 1,
                "exit_code": 0,
                "results": [
                    {"text": "mean=2", "timestamp": 1, "mime": {}, "display_name": "summary.txt"},
                    {"text": None, "timestamp": 1, "mime": {"application/json": "{\"mean\":2}"}, "display_name": "result.json"},
                    {"text": None, "timestamp": 1, "mime": {"text/csv": "x\n1\n2\n"}, "display_name": "result.csv"},
                    {"text": None, "timestamp": 1, "mime": {"text/html": "<table></table>", "image/png": "aW1hZ2U="}, "display_name": "plot.png"},
                ],
                "stdout": [{"text": "done\n", "timestamp": 1}],
                "stderr": [],
                "error": None,
                "complete": {"timestamp": 2, "execution_time_in_millis": 1},
                "input_audit": {"input_rows": 3, "valid_rows": 2, "excluded_rows": 1, "rules": bundle["validity_rules"]},
            }

        args = {"frame_ref": frame_ref, "python_code": "display(df)", "seed": 7, "_server_context": context}
        result = execute_python_analysis(args, executor=fake_executor)
        assert result["ok"] and result["output_summary"]["mime_types"] == ["application/json", "image/png", "text/csv", "text/html", "text/plain"]
        assert result["output_summary"]["inline_results"] == [
            {"output_index": 0, "display_name": "summary.txt", "mime_type": "text/plain", "value": "mean=2"},
            {"output_index": 1, "display_name": "result.json", "mime_type": "application/json", "value": {"mean": 2}},
        ]
        assert len(result["output_summary"]["downloads"]) == 1 and result["output_summary"]["downloads"][0]["display_name"] == "result.csv"
        download_token = result["output_summary"]["downloads"][0]["url"].rsplit("/", 1)[1]
        download, download_mime, download_name = artifact_assets.read_signed_output(download_token, secret=os.environ.get("MCP_SHARED_TOKEN", ""), artifacts=ARTIFACTS)
        assert download == b"x\n1\n2\n" and download_mime == "text/csv" and download_name == "result.csv"
        wide_names = [f"field_{index}" for index in range(MAX_OUTPUT_FIELDS + 1)]
        wide_csv = ",".join(wide_names) + "\n" + ",".join("1" for _ in wide_names) + "\n"
        wide_summary = output_summary({"results": [{"text": None, "mime": {"text/csv": wide_csv}, "display_name": "wide.csv"}], "stdout": [], "stderr": []})
        assert len(wide_summary["tabular_outputs"][0]["fields"]) == MAX_OUTPUT_FIELDS
        oversized_inline = output_summary({"results": [{"text": "x" * (MAX_INLINE_RESULT_BYTES + 1), "mime": {}, "display_name": "summary.txt"}], "stdout": [], "stderr": []})
        assert oversized_inline["inline_results"] == [] and oversized_inline["inline_results_truncated"]
        assert result["provenance"]["limits"] == {"timeout_seconds": 600, "source_bytes": MAX_CODE_BYTES, "rpc_body_bytes": MAX_RPC_BODY_BYTES, "input_bundle_bytes": MAX_INPUT_BUNDLE_BYTES, "captured_execution_bytes": MAX_OUTPUT_BYTES, "stdout_stderr_bytes_each": MAX_LOG_BYTES, "inline_result_bytes": MAX_INLINE_RESULT_BYTES, "output_fields": MAX_OUTPUT_FIELDS, "signed_download_bytes": artifact_assets.MAX_ASSET_BYTES}
        assert observed["seed"] == 7 and observed["bundle"]["frame"]["data"]["values"][0] == [1, 2, 3]
        assert observed["bundle"]["validity_rules"][0]["field"] == "heat_rate_valid"
        assert "display(df)" not in json.dumps(result) and '"values"' not in json.dumps(result)
        assert ARTIFACTS.read_json(context, result["refs"]["execution_ref"])["results"][3]["mime"]["image/png"] == "aW1hZ2U="
        listed = list_python_analyses({"_server_context": context})
        assert listed["ok"] and listed["analyses"][0]["provenance_ref"] == result["refs"]["provenance_ref"], listed
        inspected = inspect_python_analysis({"provenance_ref": result["refs"]["provenance_ref"], "_server_context": context})
        assert inspected["ok"] and inspected["python_code"] == "display(df)" and "values" not in inspected
        revised = revise_python_analysis({"provenance_ref": result["refs"]["provenance_ref"], "python_code": "display(df.head())", "seed": 8, "_server_context": context}, executor=fake_executor)
        assert revised["ok"] and revised["step"] == "revise_python_analysis" and revised["provenance"]["parent_provenance_ref"] == result["refs"]["provenance_ref"]
        foreign = execute_python_analysis({**args, "_server_context": {"org_id": "2", "user_id": "attacker"}}, executor=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
        assert not foreign["ok"] and "mismatch" in foreign["error"]
        oversized = execute_python_analysis({**args, "python_code": "x" * (MAX_CODE_BYTES + 1)}, executor=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
        assert not oversized["ok"] and "exceeds" in oversized["error"]
        policy = sandbox_policy()
        assert policy["network_default_action"] == "deny" and policy["env"] == {} and policy["volumes"] == [] and policy["resource"] == {"cpu": "1", "memory": "1Gi"}
        raw = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "execute_python_analysis", "arguments": {**args, "frame": []}}})
        assert raw is not None
        try:
            payload = json.loads(raw["result"]["content"][0]["text"])
        except json.JSONDecodeError as exc:
            raise AssertionError("invalid test tool response") from exc
        assert not payload["ok"] and "unsupported tool arguments" in payload["error"]
    ARTIFACTS = original
    print(json.dumps({"ok": True, "checks": ["authorized_frame_bundle", "trusted_validity_audit", "bounded_inline_results", "signed_csv_download", "200_field_output_summary", "opaque_mime_artifact", "cross_conversation_list_inspect_revise", "foreign_context_rejected", "oversized_code_rejected", "deny_all_policy", "raw_frame_rejected"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    require_runtime_token()
    require_service_identity()
    runtime_settings()
    bind_host = runtime_bind_host()
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} on {bind_host}:{PORT}", file=sys.stderr)
    ThreadingHTTPServer((bind_host, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
