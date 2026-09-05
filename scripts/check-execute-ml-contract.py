#!/usr/bin/env python3
"""Self-check for the structured ML executor (execute_ml_contract)."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    sandbox = load_module("executor_sandbox", ROOT / "sandbox-analysis-mcp/server.py")

    with tempfile.TemporaryDirectory() as tmp:
        setattr(sandbox, "ARTIFACTS", sandbox.ArtifactStore(Path(tmp) / "runs"))
        setattr(sandbox.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        context = {"org_id": "1", "user_id": "executor-check", "session_id": "session-exec"}
        uploaded = sandbox.uploaded_datasets.store_upload(
            context=context, session_id=context["session_id"], filename="mini.csv",
            raw=b"age,fnlwgt,income\n30,100,<=50K\n40,200,>50K\n50,300,<=50K\n60,400,>50K\n",
        )
        dataset_id = uploaded["id"]
        run_id = sandbox.ARTIFACTS.create_run(context)
        frame = {"schema": {"fields": [{"name": "age"}, {"name": "fnlwgt"}, {"name": "income"}]}, "data": {"values": [[30, 40, 50, 60], [100, 200, 300, 400], ["<=50K", ">50K", "<=50K", ">50K"]]}}
        frame_ref = sandbox.ARTIFACTS.write_json(context, run_id, "grafana-frame", [frame])

        analysis_contract = {
            "kind": "gradient_boosting", "dataset_id": dataset_id, "target": "income",
            "features": ["age"], "split": {"kind": "stratified_holdout", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only", "seed": 42},
            "seed": 42, "autotune": True, "objective": "accuracy", "search_budget": 2,
            "ontology_snapshot_sha256": uploaded["source_sha256"], "positive_class": ">50K",
            "purpose": "預測收入", "conclusion": "驗證通過，營運待確認",
            "cost_matrix": {"false_negative": 3.0, "false_positive": 1.0}, "minimum_recall": 0.7,
        }
        plan = {
            "dataset_id": dataset_id, "datasource_uid": "csv-poc", "upload_session_id": context["session_id"],
            "analysis_input_contract": {"required_fields": ["age", "income"], "optional_fields": [], "validity_rules": [], "minimum_rows": 2, "maximum_rows": 100000, "maximum_fields": 200, "maximum_response_bytes": 52428800, "execution_template": "ask_o11y_gradient_boosting_v1", "preprocessing_fit_scope": "training_only", "autoresearch": {"objective": "accuracy", "search_budget": 2, "max_search_budget": 40}},
            "ontology": {"snapshot_id": f"candidate:{dataset_id}", "sha256": uploaded["source_sha256"], "status": "observed"},
            "analysis_contract": analysis_contract,
        }
        plan["plan_sha256"] = hashlib.sha256(json.dumps({k: v for k, v in plan.items()}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        contract_ref = sandbox.ARTIFACTS.write_json(context, run_id, "query-plan", plan)

        captured = {}
        def fake_executor(frame_bundle_json: str, python_code: str, seed: int):
            captured["code"] = python_code
            captured["seed"] = seed
            return {"execution_id": "exec", "results": [], "stdout": [], "stderr": [], "error": None, "complete": {}, "input_audit": {"input_rows": 4, "valid_rows": 4, "excluded_rows": 0, "rules": []}}

        result = sandbox.execute_ml_contract({"frame_ref": frame_ref, "contract_ref": contract_ref, "seed": 42, "_server_context": context}, executor=fake_executor)
        assert result["ok"], result
        code = captured["code"]
        ast.parse(code)  # template must be syntactically valid Python
        for needle in ("run_multi_model_comparison", "build_manifest", "render_assets", "emit(manifest", "'accuracy'", "'>50K'", "BUDGET = 2", "n_iter=BUDGET", "COST_MATRIX = {'false_negative': 3.0, 'false_positive': 1.0}", "MIN_RECALL = 0.7", "operating_scenarios", "render_shap_summary", "render_model_comparison", "shap.TreeExplainer"):
            assert needle in code, f"template missing {needle}"
        assert "plt.style.use" not in code
        assert ".sample(" not in code and "recommend_spec_values" not in code

        # Fail-closed cases.
        missing = sandbox.execute_ml_contract({"frame_ref": frame_ref, "seed": 42, "_server_context": context}, executor=fake_executor)
        assert not missing["ok"] and "contract_ref" in missing["error"]
        try:
            tampered_plan = json.loads(json.dumps(plan))
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"plan fixture is not JSON-safe: {exc}") from exc
        tampered_plan["analysis_contract"]["target"] = "fnlwgt"
        tampered_ref = sandbox.ARTIFACTS.write_json(context, sandbox.ARTIFACTS.create_run(context), "query-plan", tampered_plan)
        tampered = sandbox.execute_ml_contract({"frame_ref": frame_ref, "contract_ref": tampered_ref, "seed": 42, "_server_context": context}, executor=fake_executor)
        assert not tampered["ok"], tampered

    # ml_autoresearch returns probabilities + top_features; pr_auc maps to average_precision.
    research = load_module("executor_autoresearch", ROOT / "sandbox-analysis-mcp/ml_autoresearch.py")
    assert research.OBJECTIVE_SCORING == {"accuracy": "accuracy", "roc_auc": "roc_auc", "pr_auc": "average_precision"}
    run_autoresearch_checks(research)

    print("ok: structured ML executor")
    return 0


def run_autoresearch_checks(research) -> None:
    import numpy as np  # type: ignore[reportMissingImports]
    import pandas as pd  # type: ignore[reportMissingImports]
    from sklearn.datasets import make_classification  # type: ignore[reportMissingImports]
    from sklearn.model_selection import train_test_split  # type: ignore[reportMissingImports]

    values, target = make_classification(n_samples=300, n_features=6, n_informative=4, weights=[0.7, 0.3], random_state=42)
    values = np.asarray(values)
    frame = pd.DataFrame(values, columns=[f"f{i}" for i in range(values.shape[1])])
    x_tr, x_te, y_tr, y_te = train_test_split(frame, target, test_size=0.25, stratify=target, random_state=42)
    outcome = research.run_classification_autoresearch(x_tr, y_tr, x_te, y_te, kind="random_forest_shap", objective="pr_auc", seed=42, n_iter=2, cv_folds=3)
    assert len(outcome["probabilities"]) == len(y_te)
    assert outcome["top_features"] and {"name", "importance"} <= set(outcome["top_features"][0])


if __name__ == "__main__":
    raise SystemExit(main())
