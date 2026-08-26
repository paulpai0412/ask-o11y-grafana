#!/usr/bin/env python3
"""Public-seam check for ontology field metadata projection into the ML sandbox template."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sandbox-analysis-mcp"))


def load_server():
    path = ROOT / "sandbox-analysis-mcp/server.py"
    spec = importlib.util.spec_from_file_location("ml_atlas_server_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    server = load_server()
    fields_view = [
        {"name": "tenure", "semantic_kind": "measurement", "unit": "month", "analysis_role": "feature"},
        {"name": "churn", "semantic_kind": "label_target", "unit": None, "analysis_role": "target"},
    ]
    plan = {
        "dataset_id": "telco",
        "plan_sha256": "b" * 64,
        "ontology": {"snapshot_id": "s", "sha256": "a" * 64},
        "analysis_contract": {"field_views": fields_view},
        "analysis_input_contract": {"autoresearch": {"search_budget": 2, "objective": "pr_auc"}},
    }
    contract = {
        "kind": "xgboost", "target": "churn", "features": ["tenure"], "positive_class": "Yes",
        "split": {"kind": "stratified_holdout", "test_fraction": 0.2}, "purpose": "測試", "conclusion": "測試完成",
    }
    template = server.compose_ml_template(plan, contract, 42)
    assert f"FIELDS_VIEW = {fields_view!r}" in template, template[:2000]
    assert template.count("fields_view=FIELDS_VIEW") == 2, template
    assert template.count("evaluation_frame=X_hold") == 2, template
    assert template.count("target_values=y.tolist()") == 2, template
    assert "sample_values=transformed" in template, "Plotly SHAP must use the transformed matrix"
    print("ok: ontology fields_view is embedded in ML sandbox template")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
