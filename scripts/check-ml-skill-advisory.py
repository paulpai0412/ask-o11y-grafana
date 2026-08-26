#!/usr/bin/env python3
"""Self-check for the ask-o11y ml-method-selection agent skill.

Validates that patches/ask-o11y-ml-method-skill.patch contains exactly one
SKILL.md addition whose frontmatter is parseable by pkg/agent/skill_registry.go
(single-line key:value only), stays under maxSkillContextBytes (32 KiB), and
keeps advisory wording: no mandatory workflow language.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "ask-o11y-ml-method-skill.patch"
MAX_SKILL_CONTEXT_BYTES = 32 * 1024  # skill_registry.go maxSkillContextBytes

# Wording that would impose a fixed analysis flow (check-no-fixed-analysis-flow
# contract). The reporting template must stay framed as a template, not steps.
FORBIDDEN_PATTERNS = (
    "you must first",
    "step 1:",
    "固定回報模板",
    "章節可留白但不得刪除標題",
    "面板故事順序為五幕",
)


def extract_added_file(patch_text: str) -> tuple[str, str]:
    """Return (path, content) of the single new file added by the patch."""
    added: dict[str, list[str]] = {}
    current: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and "new file mode" in patch_text:
                current = None  # resolved below from +++ lines
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):]
            added.setdefault(current, [])
        elif line.startswith("+") and current:
            added[current].append(line[1:])
    files = {p: "\n".join(ls) for p, ls in added.items() if p.endswith("SKILL.md")}
    if len(files) != 1:
        raise AssertionError(f"expected exactly one SKILL.md in patch, got {sorted(files)}")
    path, content = next(iter(files.items()))
    return path, content


def parse_frontmatter(content: str) -> dict[str, str]:
    """Mirror skill_registry.go skillFrontmatter: single-line 'key: value' only."""
    assert content.startswith("---\n"), "SKILL.md must start with ---"
    end = content.index("\n---\n", 4)
    fields: dict[str, str] = {}
    for line in content[4:end].split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def main() -> int:
    patch_text = PATCH.read_text(encoding="utf-8")
    path, content = extract_added_file(patch_text)

    assert path == "pkg/agent/skills/ml-method-selection/SKILL.md", f"unexpected path: {path}"

    size = len(content.encode("utf-8"))
    assert size <= MAX_SKILL_CONTEXT_BYTES, f"skill too large: {size} > {MAX_SKILL_CONTEXT_BYTES}"

    fields = parse_frontmatter(content)
    assert fields.get("name") == "ml-method-selection", f"bad name: {fields.get('name')!r}"
    description = fields.get("description", "")
    assert len(description) > 100, f"description missing/too short: {len(description)}"
    for keyword in ("設備", "製程", "良率", "anomaly detection", "predictive maintenance"):
        assert keyword in description, f"trigger keyword missing from description: {keyword}"

    body = content[content.index("\n---\n", 4) + 5:]
    lowered = body.lower()
    for pattern in FORBIDDEN_PATTERNS:
        assert pattern not in lowered, f"forbidden fixed-flow wording: {pattern}"
    assert "advisory knowledge" in body, "must declare advisory status"

    # Requested coverage: equipment health index, value-level thresholds, dynamic report synthesis.
    for section in ("健康度 HI", "Cpk≥1.33", "PSI<0.1".replace("<", "<"), "PR-AUC", "TimeSeriesSplit"):
        assert section in body, f"required content missing: {section}"
    for required in ("prepare_ml_report", "inspect_report_artifacts", "compose_ml_dashboard", "inspection_ref", "view_ids", "ask-o11y-report-synthesis-v1", "不得套用固定分析流程", "一次整份报告 synthesis"):
        assert required in body, f"dynamic report synthesis advisory missing: {required}"
    for required in ("calibrated_probabilities", "CatBoost", "受控 challenger"):
        assert required in body, f"P0/P1 advisory missing: {required}"

    print(f"ok: {path} ({size} bytes, frontmatter ok, advisory ok, template ok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
