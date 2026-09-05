#!/usr/bin/env python3
"""Self-check for the bounded, plain-language ML presentation manifest."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_presentation.py"
    spec = importlib.util.spec_from_file_location("ml_presentation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def adult_manifest(presentation):
    return presentation.build_manifest(
        purpose="預測 income 是否 >50K，供主管判斷是否可試用。",
        conclusion="模型驗證通過，但營運門檻尚未核准，不能直接部署。",
        identity={"run_id": "run-adult", "dataset_id": "adult", "ontology_snapshot_id": "candidate:adult", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "income", "task_kind": "binary_classification", "primary_metric": "accuracy", "positive_class": ">50K", "threshold": 0.5, "threshold_cost_approved": False},
        data={"rows": 32561, "features": 13, "train_rows": 26048, "holdout_rows": 6513, "split_kind": "stratified_holdout", "excluded_fields": [{"name": "fnlwgt", "reason": "抽樣權重，不作為預測特徵"}]},
        process={"model_family": "LightGBM", "search_budget": 40, "completed_trials": 40, "cv_folds": 5, "preprocessing_fit_scope": "training_only", "best_params": {"n_estimators": 150}},
        baseline_metrics={"accuracy": 0.8346, "pr_auc": 0.8250, "roc_auc": 0.9243},
        selected_metrics={"accuracy": 0.8701, "pr_auc": 0.8221, "roc_auc": 0.9236},
        guards={"generalization_gap": 0.00374, "importance_stability": 0.7758, "max_psi": 0.00221, "verdict": "accepted"},
        trials=[{"rank": rank, "cv_score": 0.86 + rank / 1000, "params": {"trial": rank}} for rank in range(1, 6)],
        features=[{"name": "age", "importance": 0.2, "explanation": "年齡提高時，模型預測會改變"}],
        limitations=["觀察性資料，不能解讀為因果。"],
    )


def main() -> int:
    presentation = load_module()
    manifest = adult_manifest(presentation)
    presentation.validate_manifest(manifest)

    decision = manifest["decision"]
    assert decision["correct_per_1000"] == 870
    assert decision["errors_per_1000"] == 130
    assert decision["fewer_errors_per_1000"] == 36
    assert 21.4 <= decision["relative_error_reduction_percent"] <= 21.6
    assert decision["model_evidence_status"] == "模型驗證：通過"
    assert decision["operational_status"] == "營運使用：尚待確認誤判與漏判成本"

    guidance = manifest["metric_guidance"]
    assert "accuracy" in guidance["primary_metric_reason"]
    assert len(guidance["watch_first"]) > 10
    assert "取捨" in guidance["trade_off_explanation"] or "牽制" in guidance["trade_off_explanation"]
    assert "recall" in guidance["per_metric"] and "漏判" in guidance["per_metric"]["recall"]

    narrative = manifest["narrative"]
    assert "本次分析的目的是" in narrative and "每 1,000 筆約判對 870 筆" in narrative
    assert "少錯約 36 筆" in narrative and "模型驗證通過" in narrative
    assert "不代表因果" in narrative or "非因果" in narrative or "不能解讀為因果" in narrative

    explanations = manifest["plain_language"]
    assert "每 1,000 筆" in explanations["accuracy"]
    assert "約 4 筆" in explanations["generalization"]
    assert "78%" in explanations["stability"]
    different = adult_manifest(presentation)
    different["guards"]["verdict"] = "unstable"
    rebuilt = presentation.build_manifest(**{key: different[key] for key in ("purpose", "conclusion", "identity", "objective", "data", "process", "guards", "trials", "features", "limitations")}, baseline_metrics=different["results"]["baseline"], selected_metrics=different["results"]["selected"])
    assert "不可部署" in rebuilt["decision"]["operational_status"]
    assert "不代表未來" in explanations["drift"]

    for bad in (
        {**manifest, "raw_rows": [{"secret": 1}]},
        {**manifest, "physical_path": "/tmp/private.csv"},
        {**manifest, "trials": manifest["trials"] * 2},
        {**manifest, "features": manifest["features"] * 21},
        {**manifest, "results": {"selected": {"accuracy": math.nan}}},
        {**manifest, "process": {"estimator": object()}},
    ):
        try:
            presentation.validate_manifest(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe or unbounded manifest was accepted")

    print("ok: plain-language ML presentation manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
