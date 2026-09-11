#!/usr/bin/env python3
"""Exercise the real WFERP query -> profile -> generic report chain."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / ".analysis-artifacts/runs"
SESSION_ID = ""


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


def request_json(port: int, body: dict[str, Any]) -> dict[str, Any]:
    token = os.environ["MCP_SHARED_TOKEN"]
    headers = {
        "Authorization": "Bearer " + token,
        "X-Grafana-Org-Id": os.environ["ANALYSIS_SERVICE_ORG_ID"],
        "X-Grafana-User": os.environ["ANALYSIS_SERVICE_USER_ID"],
        "X-Grafana-Actor-User-Id": "wferp-profile-e2e",
        "X-Grafana-Session-Id": SESSION_ID,
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            payload = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MCP request failed on port {port}") from exc
    if "error" in payload:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    result = payload.get("result") or {}
    content = result.get("content") or []
    try:
        value = json.loads(content[0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid MCP response on port {port}") from exc
    if not value.get("ok"):
        raise RuntimeError(json.dumps(value, ensure_ascii=False))
    return value


def mcp(port: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return request_json(port, {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call", "params": {"name": name, "arguments": arguments}})


def quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise RuntimeError(f"unexpected identifier from WFERP schema evidence: {value}")
    return f"[{value}]"


def read_profile_manifest(execution_ref: str) -> tuple[dict[str, Any], int]:
    run_id = execution_ref.split("/")[2] if len(execution_ref.split("/")) > 2 else ""
    try:
        execution = json.loads((ARTIFACT_ROOT / run_id / "sandbox-execution.json").read_text(encoding="utf-8"))
        manifest_index = next(index for index, result in enumerate(execution["results"]) if result.get("display_name") == "data-profile.json")
        manifest = json.loads(execution["results"][manifest_index]["mime"]["application/json"])
    except (OSError, UnicodeDecodeError, StopIteration, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("WFERP profile manifest is unavailable") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("WFERP profile manifest is invalid")
    return manifest, manifest_index


def make_synthesis(report_context: dict[str, Any]) -> dict[str, Any]:
    facts = report_context["facts"]
    numeric_fact = next(key for key, value in facts.items() if value.get("kind") == "number")
    text_fact = next(key for key, value in facts.items() if value.get("kind") == "text")
    panels = []
    for artifact in report_context["artifacts"]:
        views = artifact["figure_spec"]["views"]
        view_narratives = []
        for view in views:
            view_id = view["view_id"]
            view_narratives.append({
                "view_id": view_id,
                "headline": "此視圖呈現授權資料的描述性證據",
                "data_observation": "資料事實由受信任 profile 計算並保留原始粒度。",
                "visual_observation": None,
                "interpretation": "此視圖可協助理解資料結構，但不支持因果結論。",
                "limitation": "目前語義與分析範圍仍受本次授權查詢限制。",
                "next_step": "如需更深入判斷，先確認業務語義與要支持的決策。",
                "evidence": [{"fact_ref": numeric_fact, "format": "auto"}],
            })
        panels.append({
            "artifact_id": artifact["artifact_id"],
            "view_ids": [view["view_id"] for view in views],
            "view_narratives": view_narratives,
            "headline": "資料概況視圖",
            "observation": "這是完整授權資料的描述性呈現。",
            "interpretation": "跨視圖閱讀可協助辨識資料品質與結構線索。",
            "cross_chart_context": "各圖應與完整資料事實和限制一起閱讀。",
            "limitation": "相關或趨勢不等於因果，也不代表預測能力。",
            "next_step": "在採取行動前確認欄位角色、日期語義與業務問題。",
            "evidence": [{"fact_ref": text_fact, "format": "auto"}],
            "priority": "primary",
            "preferred_width": "full",
        })
    return {
        "format": "ask-o11y-report-synthesis-v1",
        "report_title": "WFERP 資料理解報告",
        "thesis": "完整授權資料已完成描述性 profile，後續判斷仍需遵守語義與因果限制。",
        "thesis_evidence": [{"fact_ref": numeric_fact, "format": "integer"}],
        "sections": [{
            "section_id": "data-understanding",
            "title": "資料理解",
            "purpose": "呈現資料形狀、品質與結構證據。",
            "collapsed": False,
            "narrative_blocks": [],
            "panels": panels,
        }],
    }


def main() -> int:
    global SESSION_ID
    load_env()
    SESSION_ID = "wferp-profile-" + uuid.uuid4().hex
    inspected = mcp(8772, "inspect_dataset", {"dataset_id": "wferp"})
    metadata_ref = inspected["refs"]["dataset_metadata_ref"]
    searched = mcp(8768, "search_wferp_schema", {"dataset_metadata_ref": metadata_ref, "prompt": "查詢 2026 年工程預算各期預算與已耗預算", "top_k": 8})
    tables = searched["schema_context"]["tables"]
    table = next((item for item in tables if any(column.get("requested") and column.get("type") == "N" for column in item.get("columns", []))), None)
    if table is None:
        raise RuntimeError("schema search did not return a requested numeric WFERP table")
    requested = [column for column in table["columns"] if column.get("requested")]
    numeric_requested = [column for column in requested if column.get("type") == "N"]
    if len(numeric_requested) < 2:
        raise RuntimeError("schema search did not return enough requested numeric fields")
    year = next((column for column in table["columns"] if "年度" in str(column.get("name"))), None)
    if year is None:
        raise RuntimeError("schema evidence did not expose a fiscal-year field")
    selected = [str(column["id"]) for column in requested]
    output_fields = selected
    table_ref = ".".join(quote(str(table[key])) for key in ("database", "schema", "id"))
    projection = ", ".join(f"[T].{quote(field)}" for field in selected)
    sql = f"SELECT {projection} FROM {table_ref} AS [T] WHERE [T].{quote(str(year['id']))} = '2026' ORDER BY [T].{quote(selected[0])}"
    planned = mcp(8768, "plan_wferp_query", {"dataset_metadata_ref": metadata_ref, "prompt": "查詢 2026 年工程預算各期預算與已耗預算", "sql": sql, "output_fields": output_fields, "minimum_rows": 0, "maximum_rows": 100000})
    queried = mcp(8772, "execute_planned_query", {"plan_ref": planned["refs"]["plan_ref"]})
    frame_ref = queried["refs"]["frame_ref"]
    profiled = mcp(8777, "profile_dataset", {"frame_ref": frame_ref})
    execution_ref = profiled["refs"]["execution_ref"]
    manifest, manifest_index = read_profile_manifest(execution_ref)
    if manifest["data"]["rows"] != queried["validation"]["row_count"] or not manifest["data"]["full_data"] or manifest["data"]["sampling"] or manifest["data"]["derived_dataset"]:
        raise RuntimeError("WFERP profile violated complete-data invariants")
    report_manifest_ref = profiled["refs"].get("report_manifest_ref")
    if not isinstance(report_manifest_ref, str):
        raise RuntimeError("profile execution did not return a canonical report_manifest_ref")
    report = mcp(8773, "prepare_ml_report", {"report_manifest_ref": report_manifest_ref})
    artifact_ids = [item["artifact_id"] for item in report["report_context"]["artifacts"]]
    inspection = mcp(8773, "inspect_report_artifacts", {"report_context_ref": report["refs"]["report_context_ref"], "artifact_ids": artifact_ids, "mode": "spec"})
    composed = request_json(8773, {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call", "params": {"name": "compose_ml_dashboard", "arguments": {"report_context_ref": report["refs"]["report_context_ref"], "inspection_refs": [inspection["refs"]["inspection_ref"]], "synthesis": make_synthesis(report["report_context"]), "uid": "wferp-profile-check", "title": "WFERP 資料理解報告", "output_mode": "full"}}})
    if not composed.get("dashboard"):
        raise RuntimeError("generic WFERP report composition returned no dashboard")
    print(json.dumps({"ok": True, "dataset": "wferp", "rows": manifest["data"]["rows"], "columns": manifest["data"]["columns"], "artifact_count": len(artifact_ids), "profile_before_ml": True, "ml_called": False, "generic_report_composed": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
