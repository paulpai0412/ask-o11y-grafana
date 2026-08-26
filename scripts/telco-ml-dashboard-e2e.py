#!/usr/bin/env python3
"""Generic report-tool E2E over the local tabular classification fixture."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".scratch/telco-data-atlas-e2e"
GRAFANA = "http://127.0.0.1:3000"
SYNTHESIS_PATH = OUTPUT / "llm-report-synthesis.json"
UID = "dynamic-llm-report-e2e"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "artifact-bridge-mcp"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def http_json(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        GRAFANA + path, data=payload, method=method,
        headers={"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Grafana request failed: {method} {path}: {exc}") from exc


def assemble_execution(bridge, context: dict[str, str]) -> tuple[str, int]:
    try:
        manifest = json.loads((OUTPUT / "ml-presentation.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"analysis manifest unavailable: {exc}") from exc
    results: list[dict[str, Any]] = []
    for artifact in manifest.get("artifacts") or []:
        name = artifact["name"]
        png = OUTPUT.joinpath(name).read_bytes()
        results.append({"mime": {"image/png": base64.b64encode(png).decode()}, "display_name": name})
        artifact_id = name.removesuffix(".png")
        plotly_path = OUTPUT / f"ml-plotly-{artifact_id}.json"
        if plotly_path.exists():
            results.append({"mime": {"application/json": plotly_path.read_text()}, "display_name": plotly_path.name})
    manifest_index = len(results)
    results.append({"mime": {"application/json": json.dumps(manifest, ensure_ascii=False)}, "display_name": "ml-presentation.json"})
    run_id = bridge.ARTIFACTS.create_run(context)
    execution_ref = bridge.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {"results": results, "error": None})
    return execution_ref, manifest_index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    bridge = load_module("dynamic_report_e2e_bridge", ROOT / "artifact-bridge-mcp/server.py")
    setattr(bridge, "ARTIFACTS", bridge.ArtifactStore(ROOT / ".analysis-artifacts/runs"))
    context = {"org_id": "1", "user_id": "dynamic-report-e2e"}
    execution_ref, manifest_index = assemble_execution(bridge, context)
    prepared = bridge.prepare_ml_report({"execution_ref": execution_ref, "manifest_output_index": manifest_index, "_server_context": context})
    if not prepared.get("ok"):
        raise RuntimeError(f"prepare_ml_report failed: {prepared}")
    report_context_ref = prepared["refs"]["report_context_ref"]
    OUTPUT.joinpath("report-runtime.json").write_text(json.dumps({"execution_ref": execution_ref, "manifest_output_index": manifest_index, "report_context_ref": report_context_ref, "context": context}, ensure_ascii=False, indent=2))
    OUTPUT.joinpath("report-context.json").write_text(json.dumps(prepared["report_context"], ensure_ascii=False, indent=2))
    if args.prepare_only:
        print(json.dumps({"ok": True, "report_context": str(OUTPUT / "report-context.json"), "artifact_count": prepared["evidence"]["artifact_count"], "fact_count": prepared["evidence"]["fact_count"]}, ensure_ascii=False))
        return 0

    artifact_ids = [item["artifact_id"] for item in prepared["report_context"]["artifacts"]]
    inspection_refs = []
    for start in range(0, len(artifact_ids), 8):
        inspected = bridge.inspect_report_artifacts({"report_context_ref": report_context_ref, "artifact_ids": artifact_ids[start:start + 8], "mode": "vision", "_server_context": context})
        if not inspected.get("ok"):
            raise RuntimeError(f"inspect_report_artifacts failed: {inspected}")
        inspection_refs.append(inspected["refs"]["inspection_ref"])
    OUTPUT.joinpath("inspection-refs.json").write_text(json.dumps(inspection_refs, indent=2))

    try:
        synthesis = json.loads(SYNTHESIS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"LLM synthesis unavailable: {exc}; run --prepare-only and synthesize the whole report first") from exc
    composed = bridge.compose_ml_dashboard({
        "report_context_ref": report_context_ref, "inspection_refs": inspection_refs, "synthesis": synthesis,
        "uid": UID, "title": synthesis["report_title"], "_server_context": context,
    })
    if not composed.get("ok"):
        raise RuntimeError(f"compose_ml_dashboard failed: {composed}")
    dashboard = composed["dashboard"]
    resolved = bridge.resolve_dashboard_refs({"dashboard": dashboard, "_server_context": context})
    if not resolved.get("ok"):
        raise RuntimeError(f"resolve_dashboard_refs failed: {resolved}")
    OUTPUT.joinpath("dynamic-dashboard-raw.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2))
    OUTPUT.joinpath("dynamic-dashboard-resolved.json").write_text(json.dumps(resolved["dashboard"], ensure_ascii=False, indent=2))

    written = http_json("/api/dashboards/db", method="POST", body={"dashboard": resolved["dashboard"], "overwrite": True, "message": "Dynamic LLM report E2E"})
    fetched = http_json(f"/api/dashboards/uid/{UID}")
    stored = fetched["dashboard"]
    section_ids = [item.get("askO11ySectionId") for item in stored["panels"] if item.get("type") == "row"]
    expected_sections = [item["section_id"] for item in synthesis["sections"]]
    if section_ids != expected_sections:
        raise RuntimeError(f"Grafana changed LLM section order: {section_ids}")
    flattened = []
    for item in stored["panels"]:
        flattened.append(item)
        flattened.extend(item.get("panels") or [])
    evidence_panels = [item for item in flattened if item.get("askO11yArtifactId")]
    expected_panels = sum(len(section["panels"]) for section in synthesis["sections"])
    if len(evidence_panels) != expected_panels:
        raise RuntimeError("Grafana evidence panel count differs from LLM synthesis")
    serialized = json.dumps(stored)
    if "$asset_url_" in serialized or "$plotly_" in serialized:
        raise RuntimeError("stored dashboard contains unresolved placeholders")

    OUTPUT.joinpath("dynamic-dashboard-readback.json").write_text(json.dumps(fetched, ensure_ascii=False, indent=2))
    summary = {
        "uid": UID, "url": written.get("url"), "sections": section_ids,
        "evidence_panels": len(evidence_panels),
        "plotly_panels": sum(item.get("type") == "asko11y-plotly-panel" for item in evidence_panels),
        "resolved_assets": resolved["evidence"]["resolved_assets"],
        "resolved_plotly": resolved["evidence"]["resolved_plotly"],
        "inspected_artifacts": len(artifact_ids),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
