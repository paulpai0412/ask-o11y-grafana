#!/usr/bin/env python3
"""Check effective, rebuilt advisory skills rather than an obsolete early patch."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".scratch/ask-o11y-release-build/pkg/agent/skills"


def main() -> int:
    for name in ("data-understanding", "ml-method-selection"):
        content = (SOURCE / name / "SKILL.md").read_text()
        assert content.startswith("---\n")
        end = content.index("\n---\n", 4)
        fields = dict(line.split(":", 1) for line in content[4:end].splitlines())
        assert fields["name"].strip() == name
        assert fields["description"].strip()
        assert len(content.encode()) <= 32 * 1024
        assert "advisory knowledge" in content and "indeterminate" in content
        assert "session" in content and "publication" in content
        for retired in ("ask_o11y_select_capabilities", "ask_o11y_approve_analysis_scope", "wait for confirmation", "no sampling, truncation or derived datasets", "Every numeric claim must", "coverage is complete"):
            assert retired not in content, (name, retired)
    print("PASS: effective skills are advisory, retain safety and omit retired workflow gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
