#!/usr/bin/env python3
"""Behavior check for regression autoresearch and the trusted ML contract route."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]
CORE_KINDS = {"dummy", "ridge", "random_forest", "extra_trees", "hist_gradient_boosting"}
OPTIONAL_KINDS = {"catboost", "xgboost"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def regression_fixture() -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    index = np.arange(180, dtype=float)
    frame = pd.DataFrame({
        "load": index,
        "ambient": np.sin(index / 8),
        "grade": np.where((index.astype(int) % 3) == 0, "A", "B"),
    })
    target = pd.Series(2.5 * index - 4 * np.sin(index / 8), name="target")
    return frame.iloc[:140], target.iloc[:140], frame.iloc[140:], target.iloc[140:]


def check_core() -> None:
    dockerfile = (ROOT / "sandbox-analysis-mcp/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / "sandbox-analysis-mcp/.dockerignore").read_text(encoding="utf-8")
    assert "COPY ml_regression.py /opt/ask-o11y/ml_regression.py" in dockerfile
    assert "!ml_regression.py" in dockerignore
    regression = load_module("check_ml_regression_core", ROOT / "sandbox-analysis-mcp/ml_regression.py")
    train, target, holdout, holdout_target = regression_fixture()
    available_optional = OPTIONAL_KINDS & set(regression.available_regression_kinds())
    for unavailable in OPTIONAL_KINDS - available_optional:
        try:
            regression.run_multi_model_regression(train, target, holdout, holdout_target, kinds=[unavailable], n_iter=2, cv_folds=3)
        except ValueError as exc:
            assert "unavailable" in str(exc)
        else:
            raise AssertionError("missing algorithm was silently substituted")
    candidate = regression.run_multi_model_regression(train, target, holdout, object(), kinds=["ridge"], n_iter=2, cv_folds=3, selection_only=True)
    assert "holdout_metrics" not in candidate and candidate["completed_trials"] <= 2
    result = regression.run_multi_model_regression(
        train,
        target,
        holdout,
        holdout_target,
        kinds=sorted((CORE_KINDS - {"dummy"}) | available_optional),
        seed=42,
        n_iter=8,
        cv_folds=3,
        bootstrap_samples=200,
    )

    rows = result["comparison"]
    assert {row["kind"] for row in rows} == CORE_KINDS | available_optional
    assert not result["unavailable_kinds"]
    assert not (ROOT / "catboost_info").exists(), "CatBoost must not write training files"
    assert len({row["cv_fold_signature"] for row in rows}) == 1
    assert sum(row["search_trials"] for row in rows) == result["completed_trials"] <= 8
    for row in rows:
        assert {"cv_mae_mean", "cv_mae_std", "cv_rmse_mean"} <= set(row)
        assert "holdout_metrics" not in row, "holdout must not feed model comparison"
    assert result["split_kind"] == "chronological"
    assert all(fold["train_end"] < fold["validation_start"] for fold in result["cv_folds"])
    assert result["selected_kind"] != "dummy"
    assert result["beats_baseline"]
    assert result["can_run_constrained_search"]
    assert {"mae", "rmse", "r2", "mae_interval"} <= set(result["holdout_metrics"])

    support = pd.DataFrame({
        "load": [10.0] * 12 + [20.0] * 12 + [30.0] * 2,
        "ambient": np.linspace(-0.2, 0.2, 26),
        "grade": ["A"] * 6 + ["B"] * 6 + ["A"] * 6 + ["B"] * 6 + ["A"] * 2,
    })
    candidates = regression.search_candidate_settings(
        result["selected_estimator"], support,
        beats_baseline=result["beats_baseline"], target_direction="minimize",
        controllable_fields=["load"], support_group_fields=["grade"], fixed_context={},
        bounds={"load": [10.0, 20.0]}, minimum_support=5, top_k=3, seed=42,
    )
    assert candidates["status"] == "candidate_settings"
    assert {row["settings"]["load"] for row in candidates["candidate_settings"]} <= {10.0, 20.0}
    assert all(row["support"] >= 5 and row["context"]["grade"] in {"A", "B"} and not row["causal_claim"] for row in candidates["candidate_settings"])
    assert all({"lower", "upper", "method"} <= set(row["uncertainty"]) for row in candidates["candidate_settings"])
    assert "受控試驗" in candidates["experiment_recommendation"] and candidates["stop_conditions"]
    approved_wide = regression.search_candidate_settings(
        result["selected_estimator"], support,
        beats_baseline=True, target_direction="minimize", controllable_fields=["load"],
        bounds={"load": [0.0, 40.0]}, bounds_approved=True, minimum_support=2,
    )
    assert all(10.0 <= row["settings"]["load"] <= 30.0 for row in approved_wide["candidate_settings"])
    sparse = regression.search_candidate_settings(
        result["selected_estimator"], support,
        beats_baseline=True, target_direction="minimize", controllable_fields=["load"],
        fixed_context={"grade": "A"}, bounds={"load": [10.0, 20.0]}, minimum_support=20,
    )
    assert sparse["status"] == "insufficient_support" and not sparse["candidate_settings"]
    for kwargs, message in (
        ({"beats_baseline": False, "bounds": {"load": [10.0, 20.0]}}, "baseline"),
        ({"beats_baseline": True, "bounds": {"load": [0.0, 40.0]}}, "observed support"),
        ({"beats_baseline": True, "bounds": {"load": [10.0, 20.0]}, "fixed_context": {"grade": "unseen"}}, "unseen"),
    ):
        try:
            regression.search_candidate_settings(
                result["selected_estimator"], support,
                target_direction="minimize", controllable_fields=["load"], minimum_support=2,
                **kwargs,
            )
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"unsafe constrained search was accepted: {kwargs}")

    # Independent worked example: final Dummy median=4.5, holdout errors=.5,.5,1.5,2.5.
    tiny_train = pd.DataFrame({"x": np.arange(10, dtype=float)})
    tiny_holdout = pd.DataFrame({"x": np.arange(10, 14, dtype=float)})
    dummy = regression.run_multi_model_regression(
        tiny_train,
        pd.Series(np.arange(10, dtype=float)),
        tiny_holdout,
        pd.Series([4.0, 5.0, 6.0, 7.0]),
        kinds=["dummy"],
        seed=7,
        n_iter=1,
        cv_folds=2,
        bootstrap_samples=200,
    )
    assert dummy["holdout_metrics"]["mae"] == 1.25
    assert dummy["holdout_metrics"]["mae_interval"] == [0.5, 2.25]
    assert not dummy["beats_baseline"]
    assert not dummy["can_run_constrained_search"]

    grouped = regression.run_multi_model_regression(
        train.iloc[:120], target.iloc[:120], holdout, holdout_target,
        kinds=["ridge"], groups=pd.Series([f"batch-{index // 5}" for index in range(120)]),
        seed=42, n_iter=2, cv_folds=3, bootstrap_samples=200,
    )
    assert grouped["split_kind"] == "grouped"
    assert all(fold["group_overlap_count"] == 0 for fold in grouped["cv_folds"])

    regime_train = pd.DataFrame({"x": np.arange(100, dtype=float)})
    regime_holdout = pd.DataFrame({"x": np.arange(100, 120, dtype=float)})
    regime = regression.run_multi_model_regression(
        regime_train, pd.Series(np.arange(100, dtype=float)),
        regime_holdout, pd.Series(np.zeros(20)), kinds=["ridge"],
        seed=42, n_iter=2, cv_folds=3, bootstrap_samples=200,
    )
    assert regime["selected_kind"] == "ridge"
    assert regime["selected_cv_beats_baseline"]
    assert regime["holdout_metrics"]["mae"] > regime["baseline_holdout_mae"]
    assert not regime["beats_baseline"] and not regime["can_run_constrained_search"]


def check_execute_ml_contract_route() -> None:
    sandbox = load_module("check_ml_regression_server", ROOT / "sandbox-analysis-mcp/server.py")
    with tempfile.TemporaryDirectory() as tmp:
        setattr(sandbox, "ARTIFACTS", sandbox.ArtifactStore(Path(tmp) / "runs"))
        setattr(sandbox.uploaded_datasets, "UPLOAD_ROOT", Path(tmp) / "uploads")
        context = {"org_id": "1", "user_id": "regression-check", "session_id": "session-regression"}
        uploaded = sandbox.uploaded_datasets.store_upload(
            context=context,
            session_id=context["session_id"],
            filename="regression.csv",
            raw=b"date,load,target\n2026-01-01,1,3\n2026-01-02,2,4\n",
        )
        dataset_id = uploaded["id"]
        run_id = sandbox.ARTIFACTS.create_run(context)
        frame_ref = sandbox.ARTIFACTS.write_json(context, run_id, "grafana-frame", [{
            "schema": {"fields": [{"name": "date"}, {"name": "load"}, {"name": "target"}]},
            "data": {"values": [["2026-01-01", "2026-01-02"], [1, 2], [3.0, 4.0]]},
        }])
        contract = {
            "task_kind": "regression",
            "kind": "ridge",
            "algorithms": ["dummy", "ridge", "extra_trees"],
            "dataset_id": dataset_id,
            "target": "target",
            "target_direction": "minimize",
            "features": ["load"],
            "controllable_fields": [],
            "context_fields": ["load"],
            "forbidden_fields": [],
            "constrained_search": {"enabled": True, "minimum_support": 2, "top_k": 3, "fixed_context": {}, "bounds": {"load": [1.0, 2.0]}},
            "split": {"kind": "chronological_holdout", "time_field": "date", "test_fraction": 0.2, "preprocessing_fit_scope": "training_only"},
            "seed": 42,
        }
        plan = {
            "dataset_id": dataset_id,
            "ontology": {"snapshot_id": f"candidate:{dataset_id}", "sha256": uploaded["source_sha256"]},
            "analysis_contract": contract,
            "analysis_input_contract": {
                "execution_template": "ask_o11y_regression_v1",
                "preprocessing_fit_scope": "training_only",
                "autoresearch": {"objective": "mae", "search_budget": 6},
            },
        }
        plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        contract_ref = sandbox.ARTIFACTS.write_json(context, run_id, "query-plan", plan)
        captured: dict[str, str] = {}

        def fake_executor(frame_bundle_json: str, python_code: str, seed: int):
            captured["code"] = python_code
            return {"execution_id": "exec", "results": [], "stdout": [], "stderr": [], "error": None, "complete": {}, "input_audit": {"input_rows": 2, "valid_rows": 2, "excluded_rows": 0, "rules": []}}

        outcome = sandbox.execute_ml_contract({"frame_ref": frame_ref, "contract_ref": contract_ref, "seed": 42, "_server_context": context}, executor=fake_executor)
        assert outcome["ok"], outcome
        code = captured["code"]
        ast.parse(code)
        for literal in ("run_multi_model_regression", "search_candidate_settings", "sort_values(SPLIT_FIELD", "ALGORITHMS = ['dummy', 'ridge', 'extra_trees']", "OBJECTIVE = 'mae'", "sample_weight_fields", "candidate_settings", "regression_model_comparison.png", "regression_candidate_settings.png", "bounds_approved=True", "both CV and holdout", "can_run_constrained_search"):
            assert literal in code, f"regression template missing {literal}"
        assert "run_classification_autoresearch" not in code


def main() -> int:
    check_core()
    check_execute_ml_contract_route()
    print("ok: regression autoresearch + execute_ml_contract route")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
