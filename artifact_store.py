"""Tiny server-side artifact store for workflow-node MCP tools."""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from workflow_node import WorkflowContractError, make_artifact_ref, parse_artifact_ref

LARGE_ARTIFACT_NAMES = {
    "grafana-frame",
    "grafana-query-response",
    "data-frames",
    "query-response",
    "analysis-result",
    "analysis-efficiency",
    "analysis-anomaly-detection",
    "analysis-metric-forecasting",
    "analysis-explanation",
    "method-profile",
    "method-association",
    "method-anomalies",
    "method-forecast",
    "method-explanation",
    "method-validation",
    "datasource-catalog",
    "dataset-metadata",
}
LARGE_ARTIFACT_PREFIXES = (
    "method-engineering-",
    "analysis-engineering-",
    "method-finance-",
    "analysis-finance-",
    "render-approval-",
)


class ArtifactAuthError(PermissionError):
    """Raised when caller context does not own an artifact run."""


class ArtifactStore:
    def __init__(self, root: str | Path = ".analysis-artifacts/runs", retention_days: int | None = None):
        self.root = Path(root)
        raw_days = retention_days if retention_days is not None else os.environ.get("ANALYSIS_ARTIFACT_RETENTION_DAYS", "7")
        try:
            days = max(0, int(raw_days))
        except (TypeError, ValueError) as exc:
            raise WorkflowContractError("retention_days must be an integer") from exc
        self.retention_seconds = days * 24 * 60 * 60

    def create_run(self, context: dict[str, Any], run_id: str | None = None) -> str:
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        make_artifact_ref(run_id, "metadata")
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        self._metadata_path(run_id).write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "org_id": str(context.get("org_id", "")),
                    "user_id": str(context.get("user_id", "")),
                    "created_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return run_id

    def write_json(self, context: dict[str, Any], run_id: str, name: str, value: Any) -> str:
        self._authorize(context, run_id)
        ref = make_artifact_ref(run_id, name)
        path = self._artifact_path(ref)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ref

    def read_json(self, context: dict[str, Any], ref: str) -> Any:
        run_id, _ = parse_artifact_ref(ref)
        self._authorize(context, run_id)
        try:
            return json.loads(self._artifact_path(ref).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowContractError(f"cannot read artifact: {ref}") from exc

    def cleanup_expired(self, now: float | None = None) -> dict[str, int]:
        now = time.time() if now is None else now
        removed_large = 0
        removed_runs = 0
        removed_chart_files = 0
        chart_output_root = os.environ.get("ANALYSIS_CSV_OUTPUT_DIR") or "data/poc/analysis"
        if not self.root.exists():
            return {"removed_large": 0, "removed_runs": 0, "removed_chart_files": 0}
        for meta_path in self.root.glob("run_*/metadata.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                created_at = float(meta.get("created_at", now))
            except (TypeError, ValueError):
                continue
            if now - created_at < self.retention_seconds:
                continue
            run_dir = meta_path.parent
            run_id = run_dir.name
            for artifact in run_dir.glob("*.json"):
                name = artifact.stem
                if name not in LARGE_ARTIFACT_NAMES and not name.startswith(LARGE_ARTIFACT_PREFIXES):
                    continue
                try:
                    artifact.unlink()
                except OSError:
                    continue
                removed_large += 1
            if chart_output_root:
                chart_dir = Path(chart_output_root) / run_id
                if chart_dir.exists() and chart_dir.is_dir():
                    removed_chart_files += sum(1 for item in chart_dir.rglob("*") if item.is_file())
                    try:
                        shutil.rmtree(chart_dir)
                    except OSError:
                        meta["chart_cleanup_error"] = "failed to remove chart CSV directory"
            try:
                meta["large_artifacts_cleaned_at"] = now
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError:
                continue
        return {"removed_large": removed_large, "removed_runs": removed_runs, "removed_chart_files": removed_chart_files}

    def _run_dir(self, run_id: str) -> Path:
        make_artifact_ref(run_id, "metadata")
        return self.root / run_id

    def _metadata_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "metadata.json"

    def _artifact_path(self, ref: str) -> Path:
        run_id, parts = parse_artifact_ref(ref)
        path = self._run_dir(run_id) / f"{parts[0]}.json"
        root = self.root.resolve()
        resolved = path.resolve()
        if root not in resolved.parents:
            raise WorkflowContractError("artifact path escaped store root")
        return path

    def _authorize(self, context: dict[str, Any], run_id: str) -> None:
        try:
            meta = json.loads(self._metadata_path(run_id).read_text(encoding="utf-8"))
        except OSError as exc:
            raise WorkflowContractError(f"unknown artifact run: {run_id}") from exc
        context_org = str(context.get("org_id", ""))
        context_user = str(context.get("user_id", ""))
        if context_org != meta.get("org_id") or context_user != meta.get("user_id"):
            raise ArtifactAuthError("artifact context mismatch")
