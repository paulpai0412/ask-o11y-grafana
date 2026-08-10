#!/usr/bin/env python3
"""Focused contract check for generic Sandbox artifact rendering."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_renderer():
    path = ROOT / "grafana-renderer-mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("renderer_dynamic_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load renderer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    renderer = load_renderer()
    with tempfile.TemporaryDirectory() as tmp:
        setattr(renderer, "ARTIFACTS", renderer.ArtifactStore(Path(tmp) / "runs"))
        setattr(renderer, "GRAFANA_URL", "http://grafana.example")
        context = {"org_id": "1", "user_id": "dynamic-check"}
        run_id = renderer.ARTIFACTS.create_run(context)
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nplot").decode()
        execution_ref = renderer.ARTIFACTS.write_json(context, run_id, "sandbox-execution", {
            "results": [
                {"mime": {"image/png": png}},
                {"mime": {"text/html": "<table><tr><td>ok</td></tr></table><script>alert(1)</script>"}},
                {"mime": {"application/vnd.plotly.v1+json": "{}"}},
            ],
            "stdout": [],
            "stderr": [],
            "error": None,
        })
        dashboards = []
        fake_post = lambda dashboard: dashboards.append(dashboard) or {"uid": dashboard["uid"], "url": "/d/dynamic"}
        prepared = renderer.prepare_dashboard_write({"execution_ref": execution_ref, "_server_context": context})
        previewed = renderer.create_temporary_dashboard_preview({"approval_ref": prepared.get("approval_ref"), "_server_context": context}, post_fn=fake_post)
        created = renderer.create_dashboard_from_artifacts(
            {"approval_ref": prepared.get("approval_ref"), "_server_context": context},
            post_fn=fake_post,
        )
        unsupported = renderer.prepare_dashboard_write({"execution_ref": execution_ref, "output_indices": [2], "_server_context": context})
        replay = renderer.create_dashboard_from_artifacts(
            {"approval_ref": prepared.get("approval_ref"), "_server_context": context},
            post_fn=lambda dashboard: {"url": "/d/replay"},
        )
        content = json.dumps(dashboards)
        checks = {
            "dynamic_supported_output_count": prepared.get("publication_preview") == [
                {"output_index": 0, "mime_type": "image/png"},
                {"output_index": 1, "mime_type": "text/html"},
            ],
            "grafana_preview_precedes_publication": prepared.get("ok") and previewed.get("ok") and len(dashboards) == 2 and renderer.PREVIEW_TAG in dashboards[0].get("tags", []) and renderer.PREVIEW_TAG not in dashboards[1].get("tags", []),
            "created_two_panels": created.get("ok") and created.get("panel_count") == 2,
            "html_sanitized": "script" not in content and "alert(1)" not in content,
            "plotly_requires_compatible_panel_or_png": not unsupported.get("ok"),
            "approval_replay_rejected": not replay.get("ok"),
        }
        if not all(checks.values()):
            raise AssertionError(json.dumps({"checks": checks, "prepared": prepared, "created": created}, indent=2))
        print(json.dumps({"ok": True, "checks": list(checks)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
