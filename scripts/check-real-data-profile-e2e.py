#!/usr/bin/env python3
"""Run the data-first profile chain against the real Vestas source and services."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".scratch/huggingface/vestas_high_wind_power_regulation.csv"
ARTIFACT_ROOT = ROOT / ".analysis-artifacts/runs"
OUT = ROOT / ".scratch/real-vestas-profile-e2e.json"


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


def request_json(url: str, *, headers: dict[str, str], body: Any, method: str = "POST", timeout: int = 1800) -> Any:
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), method=method, headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed: {method} {url}") from exc


def mcp(port: int, name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    response = request_json(
        f"http://127.0.0.1:{port}/mcp",
        headers=headers,
        body={"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    if "error" in response:
        raise RuntimeError(json.dumps(response, ensure_ascii=False))
    try:
        result = response["result"]
        payload = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid MCP response from {name}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"{name} failed: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def main() -> int:
    load_env()
    required = ["MCP_SHARED_TOKEN", "ANALYSIS_SERVICE_ORG_ID", "ANALYSIS_SERVICE_USER_ID"]
    missing_config = [key for key in required if not os.environ.get(key)]
    if missing_config:
        raise RuntimeError("missing service configuration: " + ", ".join(missing_config))
    if not SOURCE.is_file():
        raise RuntimeError(f"real source is unavailable: {SOURCE}")
    session_id = "real-profile-" + uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer " + os.environ["MCP_SHARED_TOKEN"],
        "X-Grafana-Org-Id": os.environ["ANALYSIS_SERVICE_ORG_ID"],
        "X-Grafana-User": os.environ["ANALYSIS_SERVICE_USER_ID"],
        "X-Grafana-Actor-User-Id": "real-profile-e2e",
        "X-Grafana-Session-Id": session_id,
    }
    raw = SOURCE.read_bytes()
    upload_request = urllib.request.Request(
        "http://127.0.0.1:8772/uploads",
        data=raw,
        method="PUT",
        headers={**headers, "Content-Type": "text/csv", "X-Upload-Session-Id": session_id, "X-Upload-Filename": SOURCE.name, "Content-Length": str(len(raw))},
    )
    try:
        with urllib.request.urlopen(upload_request, timeout=90) as response:
            upload = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("real Vestas upload failed") from exc
    dataset_id = str(upload["dataset_id"])
    try:
        expected_rows = int(upload["rows"])
        inspected = mcp(8772, "inspect_dataset", {"dataset_id": dataset_id}, headers)
        fields = [str(field["name"]) for field in inspected["metadata"]["fields"]]
        if not fields:
            raise RuntimeError("inspect_dataset returned no fields")
        planned = mcp(8768, "plan_query", {"dataset_metadata_ref": inspected["refs"]["dataset_metadata_ref"], "selected_fields": fields, "minimum_rows": expected_rows, "maximum_rows": expected_rows}, headers)
        queried = mcp(8772, "execute_planned_query", {"plan_ref": planned["refs"]["plan_ref"]}, headers)
        validation = queried["evidence"]["validation"]
        if validation["row_count"] != expected_rows or validation["field_names"] != sorted(fields):
            raise RuntimeError("Grafana Query did not preserve the complete inspected frame")
        profiled = mcp(8777, "profile_dataset", {"frame_ref": queried["refs"]["frame_ref"]}, headers)
    except (RuntimeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("real data profile chain failed") from exc
    execution_ref = profiled["refs"]["execution_ref"]
    run_id = execution_ref.split("/")[2]
    try:
        execution = json.loads((ARTIFACT_ROOT / run_id / "sandbox-execution.json").read_text(encoding="utf-8"))
        manifest_result = next(result for result in execution["results"] if result.get("display_name") == "data-profile.json")
        manifest = json.loads(manifest_result["mime"]["application/json"])
    except (OSError, StopIteration, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("profile manifest is unavailable") from exc
    profile_data = manifest["data"]
    if profile_data["rows"] != expected_rows or profile_data["profiled_rows"] != expected_rows:
        raise RuntimeError("profile did not cover every source row")
    if profile_data["columns"] != len(fields) or not profile_data["full_data"] or profile_data["sampling"] or profile_data["derived_dataset"]:
        raise RuntimeError("profile violated full-data invariants")
    png_outputs = [result for result in execution["results"] if "image/png" in (result.get("mime") or {})]
    if len(png_outputs) < 3 or not manifest["profile"].get("temporal_fields"):
        raise RuntimeError("profile did not render the expected bounded real-data views")
    try:
        manifest_output_index = next(index for index, result in enumerate(execution["results"]) if result.get("display_name") == "data-profile.json")
        report = mcp(8773, "prepare_ml_report", {"execution_ref": execution_ref, "manifest_output_index": manifest_output_index}, headers)
        artifact_ids = [item["artifact_id"] for item in report["report_context"]["artifacts"]]
        inspected_report = mcp(8773, "inspect_report_artifacts", {"report_context_ref": report["refs"]["report_context_ref"], "artifact_ids": artifact_ids, "mode": "vision"}, headers)
    except (RuntimeError, StopIteration, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("generic report preparation failed") from exc
    if len(inspected_report["inspection"]["artifacts"]) != len(artifact_ids):
        raise RuntimeError("report inspection did not cover every profile artifact")
    evidence = {
        "ok": True,
        "dataset_id": dataset_id,
        "rows": profile_data["rows"],
        "columns": profile_data["columns"],
        "fields": fields,
        "plan_ref": planned["refs"]["plan_ref"],
        "frame_ref": queried["refs"]["frame_ref"],
        "execution_ref": execution_ref,
        "report_context_ref": report["refs"]["report_context_ref"],
        "inspection_ref": inspected_report["refs"]["inspection_ref"],
        "artifact_count": len(artifact_ids),
        "fact_count": report["evidence"]["fact_count"],
        "full_data": profile_data["full_data"],
        "sampling": profile_data["sampling"],
        "derived_dataset": profile_data["derived_dataset"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "rows": evidence["rows"], "columns": evidence["columns"], "artifact_count": evidence["artifact_count"], "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
