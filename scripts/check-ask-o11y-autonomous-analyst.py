#!/usr/bin/env python3
"""Rebuild the local installer patch stack and test the effective analyst prompts.

Offline, no installs, service calls or deployment. This verifies prompt assembly
and embedded skills, NOT live LLM analysis quality. Historical skill-patch checks
alone do not cover the final skills after the NLAP and analyst patches.
"""
from __future__ import annotations

import os
import io
import json
from unittest.mock import patch
import re
import runpy
import shlex
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_configured_prompt() -> None:
    # The configuration helper supplies a custom prompt, overriding the Go default.
    config = runpy.run_path(str(ROOT / "scripts/configure-ask-o11y-workflow-tools.py"))
    data = config["build_json_data"]({}, True)
    config["validate_payload"]({"jsonData": data})
    prompt = data["defaultSystemPrompt"]
    for required in (
        "Choose methods and next steps yourself", "optional helpers", "generic Python",
        "does not need repeated", "derived data", "explicit task-specific restrictions",
        "RBAC", "session-private", "indeterminate", "publication requires separate authorization",
    ):
        assert required in prompt, f"Configured analyst prompt missing: {required}"
    assert "as-of, chronological split" not in prompt, "No universal time split"
    assert data["approvalPolicy"] == "approved"
    assert "Include explicit `方法選擇理由`" not in prompt, "Preview needs method/evaluation substance, not mandatory headings"
    for forbidden in ("ask_o11y_select_capabilities", "ask_o11y_approve_analysis_scope", "wait for confirmation", "Use every approved row", "inspection_ref"):
        assert forbidden not in prompt, forbidden
    custom = "Operator-authored prompt: preserve exactly."
    assert config["build_json_data"]({"defaultSystemPrompt": custom}, True)["defaultSystemPrompt"] == custom
    apply = config["apply_settings"]
    with patch.dict(apply.__globals__, {"auth_headers": lambda: {}, "secure_mcp_headers": lambda: {}}), patch.object(config["urllib"].request, "urlopen", side_effect=[io.BytesIO(json.dumps({"jsonData": {"defaultSystemPrompt": custom}}).encode()), io.BytesIO(b"{}")]) as request:
        apply("http://fixture.invalid", {"jsonData": data})
        posted = json.loads(request.call_args_list[1].args[0].data)
        assert posted["jsonData"]["defaultSystemPrompt"] == custom
        assert data["defaultSystemPrompt"] == prompt, "apply must not mutate the supplied payload"


def main() -> None:
    check_configured_prompt()
    source = ROOT / ".scratch/ask-o11y-release-build"
    go = Path(os.environ.get("GO_BIN", str(ROOT / ".scratch/go/bin/go")))
    if not go.is_file() or not (source / ".git").exists():
        raise SystemExit("Local Ask O11y source and Go required; this check does not install them")
    installer = (ROOT / "scripts/build-install-ask-o11y.sh").read_text()
    base = re.search(r"^BASE_COMMIT=([0-9a-f]{40})$", installer, re.MULTILINE)
    if not base:
        raise SystemExit("Missing pinned installer commit")
    commands = [shlex.split(line.replace("$ROOT", str(ROOT)))
                for line in installer.splitlines() if line.startswith("git apply ")]
    names = [Path(command[-1]).name for command in commands]
    ordered = ["ask-o11y-nlap-authority-and-effects.patch", "ask-o11y-autonomous-analyst.patch",
               "ask-o11y-bounded-autonomy.patch", "ask-o11y-approval-default.patch",
               "ask-o11y-report-dashboard-provenance.patch", "ask-o11y-semantic-alignment.patch", "ask-o11y-llm-flow-removal.patch"]
    assert all(names.count(name) == 1 for name in ordered), "Required overlays must be applied exactly once"
    positions = [names.index(name) for name in ordered]
    assert positions == sorted(positions), "Overlay prerequisite order changed"
    with tempfile.TemporaryDirectory(prefix="ask-o11y-analyst-check-") as folder:
        subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(source), folder], check=True)
        subprocess.run(["git", "checkout", "--quiet", base[1]], cwd=folder, check=True)
        for command in commands:
            subprocess.run(command, cwd=folder, check=True)
        for relative in ("pkg/plugin/prompt_defaults.go", "pkg/agent/skills/data-understanding/SKILL.md"):
            text = (Path(folder) / relative).read_text()
            assert "ask_o11y_approve_analysis_scope" not in text and "ask_o11y_select_capabilities" not in text, f"Retired gates survived: {relative}"
        for test in ("pkg/plugin/analyst_prompt_test.go", "pkg/agent/analyst_skills_test.go",
                     "pkg/agent/llm_flow_test.go"):
            assert (Path(folder) / test).is_file(), f"Missing regression test: {test}"
        env = dict(os.environ, GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local")
        subprocess.run([str(go.resolve()), "test", "./pkg/plugin", "./pkg/agent", "./pkg/mcp", "-skip", "Redis", "-count=1"],
                       cwd=folder, env=env, check=True)
    print(f"PASS: {len(commands)} installer patches; prompt, skills, autonomy/approval seams and package regressions (not live LLM E2E)")


if __name__ == "__main__":
    main()
