#!/usr/bin/env python3
"""Reconstruct installer patches and compare tested Go, prompt/skill and client sources."""
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".scratch/ask-o11y-release-build"


def main():
    installer = (ROOT / "scripts/build-install-ask-o11y.sh").read_text()
    match = re.search(r"^BASE_COMMIT=([0-9a-f]{40})$", installer, re.MULTILINE)
    if not match:
        raise SystemExit("FAIL: pinned source commit is missing")
    with tempfile.TemporaryDirectory(prefix="ask-o11y-patch-check-") as folder:
        # A standalone local clone avoids git apply silently using an ancestor repository.
        subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(SOURCE), folder], check=True)
        subprocess.run(["git", "checkout", "--quiet", match[1]], cwd=folder, check=True)
        count = 0
        for line in installer.splitlines():
            if line.startswith("git apply "):
                command = shlex.split(line.replace("$ROOT", str(ROOT)))
                subprocess.run(command, cwd=folder, check=True)
                count += 1
        expected_files = [
            Path("pkg/agent") / name
            for name in ("loop.go", "loop_test.go", "dashboard_resolver.go", "dashboard_resolver_test.go", "effect_receipts.go", "effect_receipts_test.go")
        ]
        expected_files.extend(
            Path("src/services") / name
            for name in ("grafanaFetch.ts", "agentClient.ts", "backendSessionClient.ts")
        )
        expected_files.extend([
            Path("pkg/plugin/prompt_defaults.go"),
            Path("pkg/plugin/analyst_prompt_test.go"),
            Path("pkg/plugin/sessionstore.go"),
            Path("pkg/plugin/sessionstore_redis.go"),
            Path("pkg/agent/delivery_state.go"),
            Path("pkg/agent/llm_turn_control_test.go"),
            Path("pkg/agent/analysis_autonomy.go"),
            Path("pkg/agent/analysis_autonomy_test.go"),
            Path("pkg/agent/delivery_evidence_test.go"),
            Path("pkg/agent/plotly_recovery_test.go"),
            Path("pkg/agent/native_presentation_test.go"),
            Path("pkg/agent/skills/data-understanding/SKILL.md"),
            Path("pkg/agent/skills/ml-method-selection/SKILL.md"),
            Path("pkg/agent/analyst_skills_test.go"),
            Path("pkg/agent/report_cursor_test.go"),
            Path("pkg/plugin/report_cursor_test.go"),
        ])
        for relative in expected_files:
            if (Path(folder) / relative).read_bytes() != (SOURCE / relative).read_bytes():
                raise SystemExit(f"FAIL: reconstructed {relative} differs from tested source")
    print(f"PASS: {count} patches applied in installer order; tested Go, prompt/skill and auth-refresh frontend files match")


if __name__ == "__main__":
    main()
