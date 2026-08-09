#!/usr/bin/env python3
"""Turn preserved fixed-flow red evidence into three-intent green regression evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "engineering-intent-differentiation.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read evidence: {path}") from exc


def analysis_result(status: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    events = status.get("events") or []
    for event in events:
        data = event.get("data") or {}
        if event.get("type") != "tool_call_result" or not str(data.get("name") or "").startswith("engineering-analysis_"):
            continue
        try:
            return str(data["name"]), json.loads(data.get("content") or "{}")
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("invalid Engineering tool result evidence") from exc
    raise RuntimeError("Engineering tool result missing")


def signature(case_id: str, preview_text: str, execution: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    tool, result = analysis_result(status)
    preview = result.get("preview") or {}
    visualization = preview.get("visualizations") or ([preview["visualization"]] if isinstance(preview.get("visualization"), dict) else [])
    visualizations = [item.get("type") for item in visualization if isinstance(item, dict)]
    return {"intent": case_id, "preview_sha256": hashlib.sha256(preview_text.encode()).hexdigest(), "tool_trace": execution["tools"], "selected_analysis_tool": tool, "analysis_arguments": execution.get("analysis_arguments") or execution.get("engineering_arguments"), "method_result_ref": result.get("method_result_ref"), "analysis_result_ref": result.get("analysis_result_ref"), "visualizations": visualizations, "method_preview": preview}


def main() -> int:
    red = load_json(ROOT / ".scratch" / "poc" / "dynamic-ml-fixed-flow-red.json")
    correlation = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-correlation-preview-e2e.json")
    dynamic = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-dynamic-engineering-e2e.json")
    red_identical = red.get("all_contracts_identical")
    require(isinstance(red_identical, bool) and red_identical and len(set(red.get("contract_hashes", {}).values())) == 1, "preserved red evidence no longer proves the fixed-flow defect")

    signatures = [signature("correlation_heatmap", correlation["preview"]["visible_text"], {"tools": correlation["execution"]["tool_call_starts"], "engineering_arguments": correlation["execution"]["engineering_arguments"]}, correlation["raw"]["execution_status"])]
    for case in dynamic["cases"]:
        signatures.append(signature(case["id"], case["preview"]["text"], case["execution"], case["raw"]["execution_status"]))

    require(len(signatures) == 3, "expected three Engineering intent signatures")
    require(len({item["preview_sha256"] for item in signatures}) == 3, "supported intents still produce identical previews")
    require(len({item["selected_analysis_tool"] for item in signatures}) == 3, "supported intents still select the same analysis tool")
    require(len({tuple(item["tool_trace"]) for item in signatures}) == 3, "supported intents still produce identical tool traces")
    require(len({item["method_result_ref"] for item in signatures}) == 3 and all(item["method_result_ref"] for item in signatures), "method artifacts are missing or identical")
    require(len({item["analysis_result_ref"] for item in signatures}) == 3 and all(item["analysis_result_ref"] for item in signatures), "analysis artifacts are missing or identical")
    require({tuple(item["visualizations"]) for item in signatures} == {("heatmap",), ("timeseries", "timeseries"), ("bar", "scatter")}, f"intent visualizations are not distinct: {signatures}")
    for item in signatures:
        require(not any(name.startswith("finance-analysis_") or name.startswith("scientific-method_") or name.startswith("thermal-power-analysis_") for name in item["tool_trace"]), f"wrong/legacy domain in {item['intent']}")

    evidence = {"ok": True, "before_fix": {"evidence": ".scratch/poc/dynamic-ml-fixed-flow-red.json", "all_contracts_identical": True, "contract_hash": next(iter(red["contract_hashes"].values()))}, "after_fix": {"preview_signatures_distinct": True, "tool_traces_distinct": True, "analysis_tools_distinct": True, "method_artifacts_distinct": True, "analysis_artifacts_distinct": True, "visualizations_distinct": True, "signatures": signatures}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "before_hashes": red["contract_hashes"], "after": [{"intent": item["intent"], "tool": item["selected_analysis_tool"], "visualizations": item["visualizations"], "method_result_ref": item["method_result_ref"]} for item in signatures], "artifact": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
