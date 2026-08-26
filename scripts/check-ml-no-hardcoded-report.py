#!/usr/bin/env python3
"""Guard production report synthesis/composition against fixed datasets, flows, and chart lists."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    production = [
        ROOT / "ml_report_contract.py",
        ROOT / "ml_dashboard_compositor.py",
        ROOT / "ml_dashboard_contract.py",
        ROOT / "artifact-bridge-mcp/server.py",
    ]
    forbidden_literals = ("Telco", "Month-to-Month", "Contract", "五幕", "required_roles", "fixed_chart")
    for path in production:
        source = path.read_text()
        for literal in forbidden_literals:
            if re.search(rf"(?<![A-Za-z]){re.escape(literal)}(?![A-Za-z])", source):
                raise AssertionError(f"hardcoded report literal {literal!r} in {path.relative_to(ROOT)}")

    skill = (ROOT / "patches/ask-o11y-ml-method-skill.patch").read_text()
    for fixed_instruction in ("優先圖：", "Dashboard 必須包含", "面板故事順序為五幕", "data_profile.png →"):
        if fixed_instruction in skill:
            raise AssertionError(f"skill still fixes report flow/content: {fixed_instruction}")
    for required in ("prepare_ml_report", "compose_ml_dashboard", "一次检视整份报告", "不得套用固定分析流程"):
        if required not in skill:
            raise AssertionError(f"skill lacks dynamic report requirement: {required}")

    print("ok: production report flow/content is not dataset or chart hardcoded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
