#!/usr/bin/env python3
"""Fail while the active Ask O11y path still contains the fixed U1 workflow."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "scripts/configure-ask-o11y-workflow-tools.py": [
        "Start with data-query-planner.plan_query",
        "On `ok=true`, call only `next_step.tool`",
        "Copy `next_step.args` exactly",
        '"id": "scientific-method"',
        '"id": "thermal-power-analysis"',
    ],
    "data-query-planner-mcp/server.py": [
        'all(token in request for token in ["一號機", "熱耗率"])',
        "用於熱耗率製程變異分析",
        "Call next_step",
    ],
}

findings = []
for relative, forbidden in CHECKS.items():
    text = (ROOT / relative).read_text(encoding="utf-8")
    findings.extend(f"{relative}: {marker}" for marker in forbidden if marker in text)

if findings:
    print("fixed analysis flow remains:")
    print("\n".join(f"- {finding}" for finding in findings))
    raise SystemExit(1)

print("ok: active orchestration and planner contain no fixed U1 workflow markers")
