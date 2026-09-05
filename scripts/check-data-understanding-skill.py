#!/usr/bin/env python3
"""Check the data-first Ask O11y skill patch without synthetic data."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/ask-o11y-data-understanding.patch"


def skill_text() -> str:
    lines = PATCH.read_text(encoding="utf-8").splitlines()
    start = lines.index("+++ b/pkg/agent/skills/data-understanding/SKILL.md") + 1
    added: list[str] = []
    for line in lines[start:]:
        if line.startswith("diff --git "):
            break
        if line.startswith("+"):
            added.append(line[1:])
    return "\n".join(added)


def main() -> int:
    content = skill_text()
    assert content.startswith("---\n") and "\nname: data-understanding\n" in content
    assert len(content.encode("utf-8")) < 32 * 1024
    for required in (
        "profile_dataset",
        "full-data",
        "observed candidate",
        "Analysis Preview",
        "WFERP/ERP",
        "visual-only aggregations",
        "native LLM",
        "prepare_ml_report",
        "independent per-view narratives",
        "Dummy",
    ):
        assert required in content, required
    lowered = content.lower()
    for forbidden in ("mock", "fixture", "fabricated", "hardcode", "fallback"):
        assert forbidden in lowered, f"safety wording missing: {forbidden}"
    assert "fixed retry state machine" in lowered
    print("ok: data-understanding skill is data-first and evidence-bound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
