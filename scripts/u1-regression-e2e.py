#!/usr/bin/env python3
"""Live U1 upload → Planner → Grafana Query → Sandbox → dynamic dashboard E2E."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/u1_by_date.csv"
OUTPUT = ROOT / ".scratch/u1-regression-e2e"
GRAFANA = "http://127.0.0.1:3000"
PORTS = {"query": 8772, "planner": 8768, "sandbox": 8777, "bridge": 8773}
UID = "u1-regression-candidates-e2e"

CONTROLLABLE = ["火上風門開度_FCP", "火上風門開度_FCS", "火上風門開度_FCT", "火上風門開度_FCF"]
SUPPORT_GROUPS = ["煤源_保證_A", "煤源_保證_B", "煤源_保證_C", "煤源_保證_D"]
CONTEXT = [*SUPPORT_GROUPS, "保證時段發電量_avg_MW", "主蒸汽溫度_C", "再熱蒸汽溫度_C", "冷凝器出口水溫_C"]
FORBIDDEN = ["原煤耗_g", "用煤量合計_保證", "發電量_保證_MWh", "平均熱值_保證_kcalkg", "燃燒風門開度_A_pct", "燃燒風門開度_B_pct", "燃燒風門開度_C_pct", "燃燒風門開度_D_pct"]


def headers() -> dict[str, str]:
    token = os.environ.get("MCP_SHARED_TOKEN", "")
    org = os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1")
    user = os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y")
    if len(token) < 32:
        raise RuntimeError("MCP_SHARED_TOKEN is required")
    return {"Authorization": f"Bearer {token}", "X-Grafana-Org-Id": org, "X-Grafana-User": user, "X-Grafana-Session-Id": "u1-regression-e2e", "Content-Type": "application/json"}


def request_json(url: str, *, method: str = "GET", body: bytes | None = None, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers={**headers(), **(extra_headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed: {method} {url}: {exc}") from exc


def rpc(server: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, ensure_ascii=False).encode()
    response = request_json(f"http://127.0.0.1:{PORTS[server]}/mcp", method="POST", body=payload)
    try:
        result = json.loads(response["result"]["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid {server}.{name} response: {response}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"{server}.{name} failed: {result}")
    return result


def observed_bounds() -> dict[str, list[float]]:
    try:
        with SOURCE.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        return {name: [min(float(row[name]) for row in rows if row[name]), max(float(row[name]) for row in rows if row[name])] for name in CONTROLLABLE}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("U1 controllable bounds are unavailable") from exc


def upload() -> str:
    raw = SOURCE.read_bytes()
    result = request_json(
        f"http://127.0.0.1:{PORTS['query']}/uploads",
        method="PUT",
        body=raw,
        extra_headers={"Content-Type": "text/csv", "Content-Length": str(len(raw)), "X-Upload-Filename": SOURCE.name, "X-Upload-Session-Id": "u1-regression-e2e"},
    )
    return str(result["dataset_id"])


def execute_analysis() -> tuple[dict[str, Any], str]:
    dataset_id = upload()
    inspected = rpc("query", "inspect_dataset", {"dataset_id": dataset_id})
    contract = {
        "task_kind": "regression",
        "algorithms": ["ridge", "extra_trees", "hist_gradient_boosting"],
        "dataset_id": dataset_id,
        "target": "熱耗率",
        "target_direction": "minimize",
        "features": [*CONTROLLABLE, *CONTEXT],
        "controllable_fields": CONTROLLABLE,
        "context_fields": CONTEXT,
        "forbidden_fields": FORBIDDEN,
        "split": {"kind": "chronological_holdout", "time_field": "日期", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"},
        "seed": 42,
        "ontology_snapshot_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "autotune": True,
        "objective": "mae",
        "search_budget": 6,
        "purpose": "比較熱耗率迴歸模型並提出有資料支持的候選設定。",
        "conclusion": "候選設定只供受控試驗，不代表因果最佳或自動控制指令。",
        "constrained_search": {"enabled": True, "minimum_support": 3, "top_k": 5, "support_group_fields": SUPPORT_GROUPS, "fixed_context": {}, "bounds": observed_bounds()},
    }
    selected = ["日期", "熱耗率", *contract["features"]]
    planned = rpc("planner", "plan_query", {"dataset_metadata_ref": inspected["refs"]["dataset_metadata_ref"], "selected_fields": selected, "minimum_rows": 100, "maximum_rows": 1000, "analysis_contract": contract})
    queried = rpc("query", "execute_planned_query", {"plan_ref": planned["refs"]["plan_ref"]})
    executed = rpc("sandbox", "execute_ml_contract", {"frame_ref": queried["refs"]["frame_ref"], "contract_ref": planned["refs"]["plan_ref"], "seed": 42})
    inline = executed.get("output_summary", {}).get("inline_results", [])
    manifest = next((item for item in inline if item.get("display_name") == "ml-regression.json"), None)
    if manifest is None:
        raise RuntimeError(f"regression manifest missing: {executed.get('output_summary')}")
    report_manifest_ref = executed.get("refs", {}).get("report_manifest_ref")
    if not isinstance(report_manifest_ref, str):
        raise RuntimeError("regression execution did not return a canonical report_manifest_ref")
    return executed, report_manifest_ref


def grafana_json(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    auth = base64.b64encode(b"admin:admin").decode()
    request = urllib.request.Request(GRAFANA + path, data=payload, method=method, headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthesis", type=Path)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    executed, report_manifest_ref = execute_analysis()
    execution_ref = executed["refs"]["execution_ref"]
    prepared = rpc("bridge", "prepare_ml_report", {"report_manifest_ref": report_manifest_ref})
    artifact_ids = [item["artifact_id"] for item in prepared["report_context"]["artifacts"]]
    inspection_refs = []
    inspections = []
    for start in range(0, len(artifact_ids), 8):
        inspected = rpc("bridge", "inspect_report_artifacts", {"report_context_ref": prepared["refs"]["report_context_ref"], "artifact_ids": artifact_ids[start:start + 8], "mode": "spec"})
        inspection_refs.append(inspected["refs"]["inspection_ref"])
        inspections.append(inspected)
    runtime = {"executed": executed, "prepared": prepared, "inspections": inspections, "inspection_refs": inspection_refs}
    (OUTPUT / "runtime.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / "report-context.json").write_text(json.dumps(prepared["report_context"], ensure_ascii=False, indent=2), encoding="utf-8")
    if args.synthesis is None:
        print(json.dumps({"ok": True, "phase": "prepared", "runtime": str(OUTPUT / "runtime.json"), "report_context": str(OUTPUT / "report-context.json"), "artifact_count": len(artifact_ids)}, ensure_ascii=False))
        return 0

    try:
        synthesis = json.loads(args.synthesis.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("LLM regression synthesis is unavailable or invalid") from exc
    composed = rpc("bridge", "compose_ml_dashboard", {"report_context_ref": prepared["refs"]["report_context_ref"], "inspection_refs": inspection_refs, "synthesis": synthesis, "uid": UID, "title": synthesis["report_title"]})
    resolved = rpc("bridge", "resolve_dashboard_refs", {"dashboard": composed["dashboard"]})
    written = grafana_json("/api/dashboards/db", method="POST", body={"dashboard": resolved["dashboard"], "overwrite": True, "message": "U1 regression constrained-search E2E"})
    fetched = grafana_json(f"/api/dashboards/uid/{UID}")
    serialized = json.dumps(fetched["dashboard"], ensure_ascii=False)
    if "$asset_url_" in serialized or "$plotly_" in serialized or "因果最佳" not in serialized:
        raise RuntimeError("stored regression dashboard failed resolution or causal-language check")
    (OUTPUT / "dashboard-readback.json").write_text(json.dumps(fetched, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "phase": "dashboard", "uid": UID, "url": written.get("url"), "artifact_count": len(artifact_ids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
