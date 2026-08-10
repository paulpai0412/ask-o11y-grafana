#!/usr/bin/env python3
"""Self-check for artifact store authorization, refs, and cleanup."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

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
ArtifactAuthError = artifact_store.ArtifactAuthError
ArtifactStore = artifact_store.ArtifactStore
WorkflowContractError = workflow_node.WorkflowContractError


def expect_error(fn, *exc_types):
    try:
        fn()
    except exc_types:
        return
    raise AssertionError("expected error")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        chart_root = Path(tmp) / "analysis-csv"
        store = ArtifactStore(Path(tmp) / "runs", retention_days=7)
        owner = {"org_id": "org-real", "user_id": "engineer-a"}
        other = {"org_id": "org-real", "user_id": "engineer-b"}
        run_id = store.create_run(owner, "run_artifact01")
        frame_ref = store.write_json(owner, run_id, "grafana-frame", {"rows": 3})
        analysis_ref = store.write_json(owner, run_id, "analysis-result", {"data_frames": [{"rows": 3}]})
        summary_ref = store.write_json(owner, run_id, "evidence", {"ok": True})
        sandbox_ref = store.write_json(owner, run_id, "sandbox-provenance", {"code_sha256": "abc"})
        store.write_json(owner, run_id, "dashboard", {"dashboard": {"panels": [{"content": "data:image/png;base64,..."}]}})
        chart_dir = chart_root / run_id
        chart_dir.mkdir(parents=True)
        (chart_dir / "panel.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        if "org-real" in frame_ref or "engineer-a" in frame_ref or str(tmp) in frame_ref:
            raise AssertionError(frame_ref)
        if store.read_json(owner, frame_ref) != {"rows": 3}:
            raise AssertionError("owner could not read artifact")
        expect_error(lambda: store.read_json(other, frame_ref), ArtifactAuthError)
        expect_error(lambda: store.read_json(owner, "artifact://run_artifact01/../secret"), WorkflowContractError)
        expect_error(lambda: store.read_json(owner, "artifact://run_artifact01/path/to/file"), WorkflowContractError)
        if store.list_refs(owner, "sandbox-provenance") != [sandbox_ref] or store.list_refs(other, "sandbox-provenance"):
            raise AssertionError("artifact listing did not enforce owner context")

        meta = Path(tmp) / "runs" / run_id / "metadata.json"
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AssertionError(f"cannot load metadata: {exc}") from exc
        data["created_at"] = 0
        meta.write_text(json.dumps(data), encoding="utf-8")
        old_chart_root = __import__("os").environ.get("ANALYSIS_CSV_OUTPUT_DIR")
        __import__("os").environ["ANALYSIS_CSV_OUTPUT_DIR"] = str(chart_root)
        removed = store.cleanup_expired(now=8 * 24 * 60 * 60)
        if old_chart_root is None:
            __import__("os").environ.pop("ANALYSIS_CSV_OUTPUT_DIR", None)
        else:
            __import__("os").environ["ANALYSIS_CSV_OUTPUT_DIR"] = old_chart_root
        if removed != {"removed_large": 4, "removed_runs": 0, "removed_chart_files": 1}:
            raise AssertionError(removed)
        if (Path(tmp) / "runs" / run_id / "grafana-frame.json").exists() or (Path(tmp) / "runs" / run_id / "analysis-result.json").exists() or (Path(tmp) / "runs" / run_id / "sandbox-provenance.json").exists() or (Path(tmp) / "runs" / run_id / "dashboard.json").exists() or chart_dir.exists():
            raise AssertionError("large expired artifacts or chart CSVs were not removed")
        if store.read_json(owner, summary_ref) != {"ok": True}:
            raise AssertionError("small evidence artifact should be retained")
        try:
            cleaned_meta = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AssertionError(f"cannot load cleaned metadata: {exc}") from exc
        if not cleaned_meta.get("large_artifacts_cleaned_at"):
            raise AssertionError(cleaned_meta)
        if not summary_ref.startswith("artifact://") or not analysis_ref.startswith("artifact://"):
            raise AssertionError((summary_ref, analysis_ref))
    print("artifact store checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
