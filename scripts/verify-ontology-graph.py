#!/usr/bin/env python3
"""Verify generic ontology graph expansion without domain-specific identifiers."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ontology_graph", ROOT / "ontology_graph.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load ontology_graph.py")
ontology_graph = importlib.util.module_from_spec(spec)
sys.modules["ontology_graph"] = ontology_graph
spec.loader.exec_module(ontology_graph)


def main() -> int:
    snapshot = {
        "registry": {
            "datasets": [
                {"physical_id": "alpha", "relations": [{"canonical_id": "relation.alpha-beta", "from_dataset": "alpha", "to_dataset": "beta", "from_fields": ["alpha_id"], "to_fields": ["alpha_id"], "cardinality": "1:N", "status": "approved", "executable": True}]},
                {"physical_id": "beta", "relations": [{"canonical_id": "relation.beta-gamma", "from_dataset": "beta", "to_dataset": "gamma", "from_fields": ["beta_id"], "to_fields": ["beta_id"], "cardinality": "1:N", "status": "approved", "executable": True}]},
                {"physical_id": "gamma", "relations": [{"canonical_id": "relation.gamma-delta", "from_dataset": "gamma", "to_dataset": "delta", "from_fields": ["gamma_id"], "to_fields": ["gamma_id"], "cardinality": "1:N", "status": "proposed", "executable": False}]},
                {"physical_id": "delta", "relations": []},
            ]
        }
    }
    approved = ontology_graph.expand_datasets(snapshot, ["alpha"], max_hops=3, limit=10)
    with_proposed = ontology_graph.expand_datasets(snapshot, ["alpha"], max_hops=3, limit=10, include_proposed=True)
    bounded = ontology_graph.expand_datasets(snapshot, ["alpha"], max_hops=1, limit=10)
    checks = {
        "approved_two_hop_expansion": approved["datasets"] == ["alpha", "beta", "gamma"],
        "approved_paths_complete": [path["relation"]["canonical_id"] for path in approved["paths"]] == ["relation.alpha-beta", "relation.beta-gamma"],
        "proposed_excluded_by_default": "delta" not in approved["datasets"],
        "proposed_opt_in_is_labeled": with_proposed["datasets"] == ["alpha", "beta", "gamma", "delta"] and with_proposed["paths"][-1]["relation"]["status"] == "proposed",
        "hop_bound_enforced": bounded["datasets"] == ["alpha", "beta"],
    }
    if not all(checks.values()):
        raise RuntimeError(json.dumps(checks))
    print(json.dumps({"ok": True, "checks": checks}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
