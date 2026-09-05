#!/usr/bin/env python3
"""Regression check for dynamic ISO/epoch upload date semantics."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uploaded_datasets as uploads  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "vestas.csv"
        path.write_text("Timestamp,Power\n1672531200000,10\n1672617600000,20\n", encoding="utf-8")
        fields, rows = uploads._csv_profile(path)
        assert rows == 2
        assert fields[0] == {"name": "Timestamp", "type": "date"}
        assert uploads._date_range(path, fields) == {"all_from": "2023-01-01", "all_to": "2023-01-02"}
    print("ok: upload date range is derived from epoch timestamps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
