#!/usr/bin/env python3
"""Self-check for deterministic plain-language ML presentation PNG assets."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np  # type: ignore[reportMissingImports]
import pandas as pd  # type: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "sandbox-analysis-mcp/ml_presentation.py"
    spec = importlib.util.spec_from_file_location("ml_presentation_assets", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def manifest(module):
    return module.build_manifest(
        purpose="預測 income 是否 >50K，供主管判斷是否可試用。",
        conclusion="模型驗證通過，但營運門檻尚未核准，不能直接部署。",
        identity={"run_id": "run-adult", "dataset_id": "adult", "ontology_snapshot_id": "candidate:adult", "ontology_sha256": "a" * 64, "contract_sha256": "b" * 64, "seed": 42},
        objective={"target": "income", "task_kind": "binary_classification", "primary_metric": "accuracy", "positive_class": ">50K", "threshold": 0.5, "threshold_cost_approved": False},
        data={"rows": 32561, "features": 13, "train_rows": 26048, "holdout_rows": 6513, "split_kind": "stratified_holdout", "excluded_fields": [{"name": "fnlwgt", "reason": "抽樣權重"}]},
        process={"model_family": "LightGBM", "search_budget": 40, "completed_trials": 40, "cv_folds": 5, "preprocessing_fit_scope": "training_only", "best_params": {"n_estimators": 150}},
        baseline_metrics={"accuracy": 0.8346, "pr_auc": 0.8250, "roc_auc": 0.9243},
        selected_metrics={"accuracy": 0.8701, "pr_auc": 0.8221, "roc_auc": 0.9236},
        guards={"generalization_gap": 0.00374, "importance_stability": 0.7758, "max_psi": 0.00221, "verdict": "accepted"},
        trials=[{"rank": rank, "cv_score": 0.871 - rank * 0.001, "params": {"trial": rank}} for rank in range(1, 6)],
        features=[{"name": name, "importance": score, "explanation": f"{name} 對判斷有影響"} for name, score in (("age", 0.25), ("education.num", 0.20), ("capital.gain", 0.15))],
        limitations=["觀察性資料，不能解讀為因果。"],
    )


def main() -> int:
    module = load_module()
    capture_path = ROOT / "sandbox-analysis-mcp/capture.py"
    capture_spec = importlib.util.spec_from_file_location("capture_cjk_check", capture_path)
    if capture_spec is None or capture_spec.loader is None:
        raise RuntimeError(f"cannot load {capture_path}")
    capture = importlib.util.module_from_spec(capture_spec)
    sys.modules[capture_spec.name] = capture
    capture_spec.loader.exec_module(capture)
    plt, *_ = capture.runtime_modules()
    plt.style.use("default")
    with warnings.catch_warnings(record=True) as caught:
        figure, axis = plt.subplots()
        axis.set_title("每一千筆判對與判錯")
        figure.canvas.draw()
    assert not any("Glyph" in str(item.message) for item in caught), caught
    report = manifest(module)
    y_true = [0, 0, 0, 1, 1, 1, 0, 1, 0, 1]
    probabilities = [0.02, 0.12, 0.28, 0.81, 0.71, 0.91, 0.42, 0.63, 0.18, 0.55]
    with tempfile.TemporaryDirectory() as tmp:
        import pandas as pd  # type: ignore[reportMissingImports]

        profile_frame = pd.DataFrame({"income": ["<=50K", ">50K", "<=50K", ">50K"], "age": [22, 44, 35, 51], "hours": [40, 45, 38, 50]})
        produced = module.render_assets(report, Path(tmp), y_true=y_true, probabilities=probabilities, frame=profile_frame, target="income")
        assert len(produced) >= 7, produced
        for asset in produced:
            path = Path(tmp) / asset["name"]
            assert path.exists() and path.stat().st_size > 3000, path
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), path
            assert asset["caption"].strip() and asset["alt_text"].strip()
        names = {asset["name"] for asset in produced}
        assert {"per_1000_outcomes.png", "baseline_error_comparison.png", "confusion_matrix.png", "roc_pr_curves.png", "trial_history.png", "generalization_health.png", "data_profile.png", "correlation_analysis.png"} <= names

        # SHAP summary with synthetic values.
        rng = np.random.default_rng(7)
        shap_values = rng.normal(0, 0.4, size=(60, 5))
        feature_names = ["age", "charge", "tenure", "contract_mt", "service_n"]
        sample_values = pd.DataFrame(rng.normal(50, 10, size=(60, 5)), columns=feature_names)
        module.render_shap_summary(report, shap_values, feature_names, sample_values, Path(tmp))

        # Spec recommendations: a column whose shap rises with its value must yield a numeric pivot.
        ratio = sample_values["charge"] / 10.0
        shap_by_column = {
            "charge": (ratio / ratio.max()).to_numpy() * 2 - 1 + rng.normal(0, 0.05, 60),
            "tenure": -(ratio / ratio.max()).to_numpy() * 2 + 1 + rng.normal(0, 0.05, 60),
        }
        specs = module.recommend_spec_values(report, shap_by_column=shap_by_column, sample_frame=sample_values, top_n=3)
        assert len(specs) == 2 and specs[0]["feature"] == "charge", specs
        assert specs[0]["positive_when"].startswith(">"), specs[0]
        assert "非因果" in specs[0]["suggestion"]
        module.validate_manifest(report)

    print("ok: plain-language ML presentation assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
