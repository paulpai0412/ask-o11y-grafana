#!/usr/bin/env python3
"""Reconstruct the install patch stack locally and compare the four changed Go files."""
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
            for name in ("loop.go", "loop_test.go", "effect_receipts.go", "effect_receipts_test.go")
        ]
        expected_files.extend(
            Path("src/services") / name
            for name in ("grafanaFetch.ts", "agentClient.ts", "backendSessionClient.ts")
        )
        for relative in expected_files:
            if (Path(folder) / relative).read_bytes() != (SOURCE / relative).read_bytes():
                raise SystemExit(f"FAIL: reconstructed {relative} differs from tested source")
    print(f"PASS: {count} patches applied in installer order; tested Go and auth-refresh frontend files match")


if __name__ == "__main__":
    main()
