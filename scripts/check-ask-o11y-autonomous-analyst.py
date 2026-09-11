#!/usr/bin/env python3
"""Rebuild the local installer patch stack and test the effective analyst prompts.

Offline, no installs, service calls or deployment. This verifies prompt assembly
and embedded skills, NOT live LLM analysis quality. Historical skill-patch checks
alone do not cover the final skills after the NLAP and analyst patches.
"""
from __future__ import annotations

import os
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
        "not an algorithm or analysis procedure", "not which algorithm to use",
        "Do not treat an unspecified method as ambiguous intent",
        "The user need not name ML", "plain-language decision summary",
        "Keep facts, uncertainty and limitations unchanged across audiences",
        "not a Grafana permission role and never grants access",
        "outside the confirmed contract", "wait for confirmation",
        "Never reinterpret prior confirmation as blanket permission",
        "lock a winner before final holdout", "indeterminate",
        "When the currently agreed work includes creating a Grafana Preview",
        "Use every approved row", "ask_o11y_select_capabilities",
        "ask_o11y_approve_analysis_scope", "Free-form Python cannot be scope-covered",
        "Beginner-facing question alignment", "alignment_basis",
        "proposal_not_confirmation", "actions_granted=[]",
        "never select the first match automatically", "do not turn not_recorded into a compulsory questionnaire",
    ):
        assert required in prompt, f"Configured analyst prompt missing: {required}"
    assert "as-of, chronological split" not in prompt, "No universal time split"
    assert data["approvalPolicy"] == "approved"
    assert "Include explicit `方法選擇理由`" not in prompt, "Preview needs method/evaluation substance, not mandatory headings"
    for required in ("explain why each proposed method fits", "concrete data-quality/precondition checks", "evaluation metrics or output-integrity checks"):
        assert required in prompt, required


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
               "ask-o11y-report-dashboard-provenance.patch", "ask-o11y-semantic-alignment.patch"]
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
            assert "alignment_basis" in text and "proposal_not_confirmation" in text, f"Missing alignment guidance: {relative}"
        for test in ("pkg/plugin/analyst_prompt_test.go", "pkg/agent/analyst_skills_test.go",
                     "pkg/agent/analysis_autonomy_test.go", "pkg/agent/analysis_autonomy_safety_test.go"):
            assert (Path(folder) / test).is_file(), f"Missing regression test: {test}"
        env = dict(os.environ, GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local")
        subprocess.run([str(go.resolve()), "test", "./pkg/plugin", "./pkg/agent", "./pkg/mcp", "-skip", "Redis", "-count=1"],
                       cwd=folder, env=env, check=True)
    print(f"PASS: {len(commands)} installer patches; prompt, skills, autonomy/approval seams and package regressions (not live LLM E2E)")


if __name__ == "__main__":
    main()
