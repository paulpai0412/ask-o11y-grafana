#!/usr/bin/env python3
"""Prove workflow-node MCP startup invokes artifact cleanup."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "artifact-cleanup-startup-check.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "runs"
        run = root / "run_startup01"
        run.mkdir(parents=True)
        (run / "metadata.json").write_text(json.dumps({"run_id": "run_startup01", "org_id": "1", "user_id": "u", "created_at": 0}), encoding="utf-8")
        (run / "grafana-frame.json").write_text("{}", encoding="utf-8")
        (run / "analysis-result.json").write_text("{\"data_frames\": [{\"rows\": 3}]}", encoding="utf-8")
        dynamic_engineering = [f"{kind}-engineering-{family}" for family in ["profile", "predictive", "patterns", "timeseries"] for kind in ["method", "analysis"]]
        dynamic_engineering.append("render-approval-expired")
        for name in dynamic_engineering:
            (run / f"{name}.json").write_text("{\"data_frames\": [{\"rows\": 5000}]}", encoding="utf-8")
        (run / "evidence.json").write_text("{\"ok\": true}", encoding="utf-8")
        chart_root = Path(tmp) / "analysis-csv"
        chart_dir = chart_root / "run_startup01"
        chart_dir.mkdir(parents=True)
        (chart_dir / "panel.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        os.environ["ANALYSIS_ARTIFACT_ROOT"] = str(root)
        os.environ["ANALYSIS_ARTIFACT_RETENTION_DAYS"] = "7"
        os.environ["ANALYSIS_CSV_OUTPUT_DIR"] = str(chart_root)
        load_module("data_query_planner_startup_cleanup_check", ROOT / "data-query-planner-mcp" / "server.py")
        large_removed = not (run / "grafana-frame.json").exists() and not (run / "analysis-result.json").exists() and all(not (run / f"{name}.json").exists() for name in dynamic_engineering)
        chart_removed = not chart_dir.exists()
        evidence_retained = (run / "evidence.json").exists()
        try:
            cleaned_meta = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot load cleaned metadata: {exc}") from exc
        out = {"ok": large_removed and chart_removed and evidence_retained and bool(cleaned_meta.get("large_artifacts_cleaned_at")), "large_removed": large_removed, "dynamic_engineering_removed": dynamic_engineering, "chart_removed": chart_removed, "evidence_retained": evidence_retained, "metadata": cleaned_meta}
        if not out["ok"]:
            raise RuntimeError(json.dumps(out, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
