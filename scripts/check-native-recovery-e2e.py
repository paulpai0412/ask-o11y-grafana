#!/usr/bin/env python3
"""Verify raw host-recorded events, never model-authored E2E summary flags."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.nlap_receipts import verify_recovery  # pyright: ignore[reportMissingImports] -- namespace resolves from ROOT above.
EVIDENCE = ROOT / ".scratch/real-vestas-ask-o11y-e2e.json"


def main() -> int:
    try:
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("fresh real E2E evidence is unavailable") from exc
    if not isinstance(evidence, dict):
        raise RuntimeError("E2E evidence must be an object")
    print(json.dumps(verify_recovery(evidence), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
