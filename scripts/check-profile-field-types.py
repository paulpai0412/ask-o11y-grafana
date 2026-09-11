#!/usr/bin/env python3
"""Regression for real profile type inference; synthetic data, no live artifacts."""
import importlib.util
import sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "sandbox-analysis-mcp/data_profile.py"
spec = importlib.util.spec_from_file_location("profile_field_types", path)
assert spec and spec.loader
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)

columns = {
    "source": ["Country-A", "Country-B", "Country-A"],
    "measurement": ["1", "2.5", None],
    "mixed": ["1", "invalid", None],
    "date": ["2026-01-01", "2026-01-02", None],
    "empty": [None, "", None],
    "flag": [True, False, True],
}
result = profile.profile_columns(columns)
fields = {field["name"]: field for field in result["fields"]}
assert fields["source"]["numeric"] is None
assert fields["source"]["categorical"]["distinct"] == 2
assert fields["source"]["semantic_kind"] == "categorical"
assert fields["measurement"]["numeric"]["count"] == 2
assert fields["mixed"]["numeric"] is None  # Do not silently discard conversion failures.
assert fields["mixed"]["categorical"]["distinct"] == 2
assert fields["date"]["temporal"]["count"] == 2
assert fields["empty"]["numeric"] is None
assert fields["flag"]["numeric"] is None
assert result["correlation"]["columns"] == ["measurement"]
assert result["rows"] == 3 and result["full_data"] and not result["sampling"]
print("PASS: categorical, numeric, mixed, temporal, empty and boolean fields retain their real types")
