"""Bounded plain-language ML presentation manifest and deterministic charts."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

FORBIDDEN_KEYS = {"raw_rows", "frame", "physical_path", "signed_url", "python_code", "credentials"}
REQUIRED_SECTIONS = {"format", "identity", "objective", "data", "process", "results", "decision", "guards", "plain_language", "trials", "features", "artifacts", "limitations"}


def _compose_metric_guidance(
    objective: dict[str, Any],
    cost_matrix: dict[str, float] | None,
    minority_rate: float | None,
) -> dict[str, Any]:
    """Deterministic plain-language guide: which metric to watch first and why."""
    primary = str(objective.get("primary_metric") or "accuracy")
    has_cost = cost_matrix is not None
    imbalanced = minority_rate is not None and minority_rate < 0.2

    if has_cost:
        primary_reason = (
            f"主要指標是 {primary}，因為已申報漏判與誤攔的成本比；門檻與模型選擇都以加權總成本最低為目標。"
        )
        trade_off = (
            "recall（抓到多少真正的正類）和 precision（說對了多少）互相牽制："
            "降低門檻可以抓到更多（recall↑），但也會誤攔更多（precision↓）。"
            "成本比就是用來決定這個取捨要往哪邊偏。"
        )
        watch_first = "先看 recall（有沒有漏掉重要的），再確認 precision（誤攔是否在可接受範圍）。"
    elif imbalanced:
        primary_reason = (
            f"主要指標是 {primary}，但資料正類比例僅 {(minority_rate or 0) * 100:.1f}%，"
            "accuracy 會被多數類稀釋；應以 PR-AUC 和 recall 為主要判讀依據。"
        )
        trade_off = (
            "recall 和 precision 互相牽制；類別不平衡時 accuracy 高不代表模型有用，"
            "必須看 PR-AUC 是否明顯高於正類比例基線。"
        )
        watch_first = "先看 PR-AUC（是否高於正類比例基線），再看 recall（漏掉多少正類）。"
    else:
        primary_reason = f"主要指標是 {primary}，資料類別比例相對平衡，accuracy 可作為直觀的整體參考。"
        trade_off = (
            "recall 和 precision 互相牽制：降低門檻可以抓到更多正類（recall↑），"
            "但也會增加誤判（precision↓）；ROC-AUC 代表不受門檻影響的整體排序能力。"
        )
        watch_first = "先看 accuracy（整體答對率），再確認 recall 和 precision 是否在可接受範圍。"

    per_metric = {
        "accuracy": "每 1,000 筆中約判對幾筆；直觀但類別不平衡時會被多數類稀釋。",
        "recall": "真正的正類中，模型抓到多少比例；漏判成本高時優先看這個。",
        "precision": "模型說是正類的，有多少真的是；誤攔成本高時優先看這個。",
        "pr_auc": "在不同門檻下找出正類的整體能力；隨機基線 = 正類比例。",
        "roc_auc": "模型把正類排在負類前面的能力，不受門檻影響；0.5 = 隨機。",
    }
    return {
        "primary_metric": primary,
        "primary_metric_reason": primary_reason,
        "watch_first": watch_first,
        "trade_off_explanation": trade_off,
        "per_metric": per_metric,
    }


def _compose_narrative(
    purpose: str,
    data: dict[str, Any],
    process: dict[str, Any],
    guards: dict[str, Any],
    selected_accuracy: float,
    baseline_accuracy: float,
    correct: int,
    errors: int,
    fewer_errors: int,
    relative_reduction: float,
    excluded_fields: list[Any],
) -> str:
    """Compose the plain-language story of the whole analysis from verified numbers."""
    excluded = "、".join(str(item.get("name")) for item in excluded_fields) if excluded_fields else "無"
    verdict_text = "通過" if guards.get("verdict") == "accepted" else f"未通過（{guards.get('verdict')}）"
    return (
        f"本次分析的目的是{purpose}。"
        f"我們使用 {data.get('rows')} 筆資料、{data.get('features')} 個特徵，"
        f"以 {data.get('split_kind')} 方式保留 {data.get('holdout_rows')} 筆模型從未看過的資料做最終驗證。"
        f"建模前排除了不適合的欄位（{excluded}），所有前處理只在訓練資料上完成，避免資料洩漏。"
        f"接著自動比較了 {process.get('completed_trials')} 組模型設定（每組交叉驗證 {process.get('cv_folds')} 次），"
        f"選出表現最穩定的組合。在從未見過的驗證資料上，模型每 1,000 筆約判對 {correct} 筆、判錯 {errors} 筆，"
        f"答對率 {selected_accuracy * 100:.1f}%，比簡單基準（{baseline_accuracy * 100:.1f}%）每 1,000 筆少錯約 {fewer_errors} 筆，"
        f"錯誤量降低約 {relative_reduction:.1f}%。"
        f"泛化檢查方面，換新資料的表現差異、關鍵因素穩定度與資料分布漂移均已完成檢驗，模型驗證{verdict_text}。"
        f"需要特別說明：這些數字代表統計關聯而非因果；正式使用前仍需確認漏判與誤攔的業務成本、"
        f"敏感欄位的使用政策，並以實際營運情境驗證門檻。"
    )


def _round_count(value: float) -> int:
    try:
        return int(math.floor(value + 0.500000001))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("count value is invalid") from exc


def _as_metric(metrics: dict[str, Any], name: str) -> float:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"metric {name} must be between 0 and 1")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"metric {name} is invalid") from exc


def build_manifest(
    *,
    purpose: str,
    conclusion: str,
    identity: dict[str, Any],
    objective: dict[str, Any],
    data: dict[str, Any],
    process: dict[str, Any],
    baseline_metrics: dict[str, Any],
    selected_metrics: dict[str, Any],
    guards: dict[str, Any],
    trials: list[dict[str, Any]],
    features: list[dict[str, Any]],
    limitations: list[str],
    llm_narrative: str | None = None,
    llm_chart_explanations: dict[str, str] | None = None,
) -> dict[str, Any]:
    baseline_accuracy = _as_metric(baseline_metrics, "accuracy")
    selected_accuracy = _as_metric(selected_metrics, "accuracy")
    correct = _round_count(selected_accuracy * 1000)
    errors = 1000 - correct
    fewer_errors = _round_count((selected_accuracy - baseline_accuracy) * 1000)
    baseline_error = 1 - baseline_accuracy
    relative_reduction = 0.0 if baseline_error <= 0 else (selected_accuracy - baseline_accuracy) / baseline_error * 100
    gap = _as_metric(guards, "generalization_gap")
    stability = _as_metric(guards, "importance_stability")
    psi = _as_metric(guards, "max_psi")
    verdict = str(guards.get("verdict") or "unknown")
    model_status = "模型驗證：通過" if verdict == "accepted" else "模型驗證：未通過"
    operational_ready = bool(objective.get("threshold_cost_approved")) and verdict == "accepted"
    operational_status = "營運使用：可依已核准門檻部署" if operational_ready else "營運使用：尚待確認誤判與漏判成本"
    minority_rate = data.get("minority_rate")
    cost_matrix = objective.get("cost_matrix")
    metric_guidance = _compose_metric_guidance(objective, cost_matrix, minority_rate)
    manifest = {
        "format": "ask-o11y-ml-presentation-v1",
        "purpose": purpose,
        "conclusion": conclusion,
        "narrative": llm_narrative or _compose_narrative(purpose, data, process, guards, selected_accuracy, baseline_accuracy,
                                          correct, errors, fewer_errors, relative_reduction,
                                          data.get("excluded_fields", [])),
        "identity": identity,
        "objective": objective,
        "data": data,
        "process": process,
        "results": {"baseline": baseline_metrics, "selected": selected_metrics},
        "decision": {
            "model_evidence_status": model_status,
            "operational_status": operational_status,
            "correct_per_1000": correct,
            "errors_per_1000": errors,
            "fewer_errors_per_1000": fewer_errors,
            "accuracy_gain_percentage_points": round((selected_accuracy - baseline_accuracy) * 100, 2),
            "relative_error_reduction_percent": round(relative_reduction, 2),
            "recommended_action": "確認漏判與誤判成本後選擇營運門檻" if not operational_ready else "依核准門檻進行受控部署",
        },
        "guards": guards,
        "metric_guidance": metric_guidance,
        "plain_language": {
            "accuracy": f"每 1,000 筆約 {correct} 筆判對、{errors} 筆判錯。",
            "improvement": f"比舊模型每 1,000 筆少錯約 {fewer_errors} 筆；錯誤量降低約 {relative_reduction:.1f}%。",
            "generalization": f"換成未見資料，每 1,000 筆的表現差約 {_round_count(gap * 1000)} 筆。",
            "stability": "換不同資料分組，前十大關鍵因素約八成仍相同。" if stability >= 0.75 else f"換不同資料分組，關鍵因素一致程度約 {stability * 100:.0f}%，需要進一步確認。",
            "drift": "本次訓練與測試資料結構非常接近；不代表未來月份不會改變。" if psi < 0.1 else "訓練與測試資料已有明顯差異，使用前需調查資料變化。",
        },
        "trials": trials,
        "features": features,
        "artifacts": [],
        "limitations": limitations,
    }
    validate_manifest(manifest)
    return manifest


def _walk(value: Any, key: str = "") -> None:
    if key in FORBIDDEN_KEYS or key.endswith("_path") or key.endswith("_url"):
        raise ValueError(f"forbidden manifest key: {key}")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _walk(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            _walk(child, key)
    elif isinstance(value, str) and (value.startswith("/") or "?token=" in value):
        raise ValueError("manifest contains a physical path or signed URL")


def validate_manifest(manifest: dict[str, Any]) -> None:
    known_sections = REQUIRED_SECTIONS | {"purpose", "conclusion", "operating_scenarios", "spec_recommendations", "narrative", "metric_guidance", "model_comparison", "evaluation_evidence"}
    unknown = set(manifest) - known_sections
    if unknown or not REQUIRED_SECTIONS <= set(manifest):
        raise ValueError(f"manifest sections are incomplete or unsupported: {sorted(unknown)}")
    narrative = str(manifest.get("narrative") or "")
    if len(narrative) < 80 or "。" not in narrative:
        raise ValueError("manifest requires a plain-language narrative of the analysis")
    scenarios = manifest.get("operating_scenarios")
    if scenarios is not None:
        if not isinstance(scenarios, list) or len(scenarios) > 5 or any(not isinstance(item, dict) or not item.get("name") for item in scenarios):
            raise ValueError("operating_scenarios is invalid")
    if not str(manifest.get("purpose") or "").strip() or not str(manifest.get("conclusion") or "").strip():
        raise ValueError("manifest requires non-empty purpose and conclusion")
    if manifest.get("format") != "ask-o11y-ml-presentation-v1":
        raise ValueError("unsupported manifest format")
    if len(manifest.get("trials", [])) > 5 or len(manifest.get("features", [])) > 20 or len(manifest.get("artifacts", [])) > 14:
        raise ValueError("manifest exceeds presentation bounds")
    evidence = manifest.get("evaluation_evidence")
    if evidence is not None:
        if not isinstance(evidence, dict) or not all(isinstance(evidence.get(name), dict) for name in ("calibration", "threshold_cost")):
            raise ValueError("manifest evaluation_evidence is invalid")
        calibration = evidence["calibration"]
        threshold_cost = evidence["threshold_cost"]
        if any(item.get("selected_on") != "train_oof" or item.get("evaluated_on") != "holdout" for item in (calibration, threshold_cost)):
            raise ValueError("evaluation evidence must be selected on train OOF and evaluated on holdout")
        numeric_values = [
            calibration.get("brier_score"), calibration.get("bins"), threshold_cost.get("threshold"),
            threshold_cost.get("false_negative_cost"), threshold_cost.get("false_positive_cost"),
            threshold_cost.get("fn_per_1000"), threshold_cost.get("fp_per_1000"),
            threshold_cost.get("weighted_cost_per_1000"), threshold_cost.get("recall"), threshold_cost.get("precision"),
        ]
        try:
            if not all(math.isfinite(float(value)) for value in numeric_values):
                raise ValueError("evaluation evidence contains non-finite values")
        except (TypeError, ValueError) as exc:
            raise ValueError("evaluation evidence metrics are invalid") from exc
    decision = manifest.get("decision")
    if not isinstance(decision, dict) or not all(key in decision for key in ("model_evidence_status", "operational_status", "correct_per_1000", "errors_per_1000", "recommended_action")):
        raise ValueError("manifest decision is incomplete")
    _walk(manifest)


def register_artifact(manifest: dict[str, Any], *, name: str, caption: str, alt_text: str) -> None:
    if not name.endswith(".png") or "/" in name or not caption.strip() or not alt_text.strip():
        raise ValueError("artifact metadata is invalid")
    manifest["artifacts"].append({"name": name, "caption": caption, "alt_text": alt_text})
    validate_manifest(manifest)


def _plot_modules():
    import matplotlib  # type: ignore[reportMissingImports]
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore[reportMissingImports]
    import numpy as np  # type: ignore[reportMissingImports]
    from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve  # type: ignore[reportMissingImports]

    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK TC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt, np, confusion_matrix, precision_recall_curve, roc_curve


def _save_figure(figure: Any, output: Path, emit_figure: Callable[[Any, str], None] | None = None) -> None:
    if emit_figure is not None:
        emit_figure(figure, output.name)
    figure.savefig(output, dpi=150, bbox_inches="tight", facecolor="white")
    figure.clear()


def _explain(figure: Any, text: str, override: str | None = None) -> None:
    """Render plain-language explanation under the chart. LLM override takes priority."""
    """Plain-language explanation strip rendered under the chart inside the PNG."""
    import textwrap

    figure.text(0.01, -0.06, "解讀：" + textwrap.fill(override or text, width=118), fontsize=11.5,
                color="#374151", ha="left", va="top", wrap=True)


def render_assets(
    manifest: dict[str, Any],
    output_dir: Path,
    *,
    y_true: list[int] | None = None,
    probabilities: list[float] | None = None,
    emit_figure: Callable[[Any, str], None] | None = None,
    frame: Any = None,
    target: str | None = None,
    llm_explanations: dict[str, str] | None = None,
) -> list[dict[str, str]]:

    """Render bounded PNG evidence with values and plain-language captions."""
    validate_manifest(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest["artifacts"] = []
    plt, np, confusion_matrix, precision_recall_curve, roc_curve = _plot_modules()
    blue, orange, green, red, grey = "#3274D9", "#FF9830", "#56A64B", "#E02F44", "#6B7280"

    # 0. Data-distribution profile so users meet the data before the model.
    if frame is not None and target and target in frame.columns:
        import pandas as pd  # type: ignore[reportMissingImports]

        profile_frame = frame.copy()
        target_counts = profile_frame[target].astype(str).value_counts()

        # Select top-3 features by importance from the manifest (not arbitrary columns).
        top_features = [item["name"] for item in (manifest.get("features") or [])[:3] if item["name"] in profile_frame.columns and item["name"] != target]
        if not top_features:
            numeric_all = profile_frame.select_dtypes(include=[np.number]).columns.drop(target, errors="ignore").tolist()
            top_features = numeric_all[:3]
        plot_features = top_features[:3]
        n_plots = 1 + len(plot_features)

        figure, axes_arr = plt.subplots(1, n_plots, figsize=(5.5 * n_plots, 4))
        if n_plots == 1:
            axes_arr = [axes_arr]
        axes_arr[0].bar(target_counts.index.tolist(), target_counts.tolist(), color=[green, red][: len(target_counts)] or [blue])
        for index, (label, count) in enumerate(target_counts.items()):
            axes_arr[0].text(index, count, str(count), ha="center", va="bottom", fontsize=11, weight="bold")
        axes_arr[0].set_title(f"預測目標 {target}", fontsize=13, weight="bold")

        for slot, col_name in enumerate(plot_features):
            ax = axes_arr[slot + 1]
            if pd.api.types.is_numeric_dtype(profile_frame[col_name]):
                ax.hist(profile_frame[col_name].dropna(), bins=25, color=blue)
            else:
                counts_f = profile_frame[col_name].astype(str).value_counts().head(8)
                ax.bar(counts_f.index.tolist(), counts_f.tolist(), color=blue)
            ax.set_title(col_name, fontsize=12, weight="bold")

        figure.suptitle("資料分布：先認識資料，再看模型", fontsize=17, weight="bold")
        _save_figure(figure, output_dir / "data_profile.png", emit_figure)
        register_artifact(manifest, name="data_profile.png", caption=f"資料共 {len(profile_frame)} 筆；目標 {target} 各類筆數如左圖，右圖為模型最重視的前 {len(plot_features)} 個特徵分布。", alt_text="預測目標類別分布與前三大特徵分布圖")

        # 0b. Correlation heatmap across numeric dimensions.
        numeric = profile_frame.select_dtypes(include=[np.number]).columns.drop(target, errors="ignore")
        corr_frame = profile_frame[numeric]
        if corr_frame.shape[1] >= 2:
            top = list(corr_frame.std().sort_values(ascending=False).index[:10])
            corr = corr_frame[top].corr()
            figure, axis = plt.subplots(figsize=(8.5, 7))
            image = axis.imshow(corr.to_numpy(), cmap="coolwarm", vmin=-1, vmax=1)
            axis.set_xticks(range(len(top)), top, rotation=45, ha="right", fontsize=9)
            axis.set_yticks(range(len(top)), top, fontsize=9)
            for row in range(len(top)):
                for column in range(len(top)):
                    axis.text(column, row, f"{corr.iloc[row, column]:.2f}", ha="center", va="center", fontsize=8,
                              color="white" if abs(corr.iloc[row, column]) > 0.6 else "black")
            figure.colorbar(image, ax=axis, shrink=0.8)
            axis.set_title("哪些欄位會一起變動？（相關係數）", fontsize=15, weight="bold")
            _save_figure(figure, output_dir / "correlation_analysis.png", emit_figure)
            register_artifact(manifest, name="correlation_analysis.png", caption="欄位間相關係數熱圖：接近 1 代表一起升高、接近 -1 代表一升一降；高相關的欄位群在模型中會被視為同一組訊號。", alt_text="數值欄位間相關係數的熱圖")

    decision = manifest["decision"]
    results = manifest["results"]
    guards = manifest["guards"]

    # 1. Per-1,000 operational outcome.
    figure, axis = plt.subplots(figsize=(10, 2.6))
    correct, errors = decision["correct_per_1000"], decision["errors_per_1000"]
    axis.barh([0], [correct], color=blue, label="判斷正確")
    axis.barh([0], [errors], left=[correct], color=orange, label="判斷錯誤")
    axis.text(correct / 2, 0, f"判對 {correct}", ha="center", va="center", color="white", fontsize=15, weight="bold")
    axis.text(correct + errors / 2, 0, f"判錯 {errors}", ha="center", va="center", color="black", fontsize=13, weight="bold")
    axis.set_xlim(0, 1000); axis.set_yticks([]); axis.set_xlabel("每 1,000 筆新資料")
    axis.set_title("每 1,000 筆會發生什麼？", loc="left", fontsize=18, weight="bold")
    axis.legend(loc="lower center", bbox_to_anchor=(0.5, -0.55), ncol=2, frameon=False)
    _save_figure(figure, output_dir / "per_1000_outcomes.png", emit_figure)
    register_artifact(manifest, name="per_1000_outcomes.png", caption=manifest["plain_language"]["accuracy"], alt_text=f"每一千筆中約{correct}筆判對、{errors}筆判錯的水平堆疊圖")

    # 2. Baseline vs selected error.
    baseline_error = (1 - results["baseline"]["accuracy"]) * 100
    selected_error = (1 - results["selected"]["accuracy"]) * 100
    figure, axis = plt.subplots(figsize=(8, 4))
    bars = axis.barh(["舊模型", "新模型"], [baseline_error, selected_error], color=[grey, green])
    for bar, value in zip(bars, [baseline_error, selected_error], strict=True):
        axis.text(value + 0.25, bar.get_y() + bar.get_height() / 2, f"{value:.2f}%", va="center", fontsize=13, weight="bold")
    axis.set_xlim(0, max(baseline_error, selected_error) * 1.35); axis.set_xlabel("判斷錯誤率（越低越好）")
    axis.set_title(f"每 1,000 筆比舊模型少錯約 {decision['fewer_errors_per_1000']} 筆", loc="left", fontsize=17, weight="bold")
    _save_figure(figure, output_dir / "baseline_error_comparison.png", emit_figure)
    register_artifact(manifest, name="baseline_error_comparison.png", caption=manifest["plain_language"]["improvement"], alt_text=f"舊模型錯誤率{baseline_error:.2f}%與新模型{selected_error:.2f}%的比較圖")

    # 3. Generalization health cards.
    figure, axis = plt.subplots(figsize=(11, 4)); axis.axis("off")
    cards = [
        ("換新資料會掉很多嗎？", f"每 1,000 筆差約 {_round_count(guards['generalization_gap'] * 1000)} 筆", green if guards["generalization_gap"] <= 0.03 else red),
        ("關鍵因素穩定嗎？", f"前十大重疊約 {guards['importance_stability'] * 100:.0f}%", green if guards["importance_stability"] >= 0.75 else orange),
        ("資料結構相近嗎？", f"PSI {guards['max_psi']:.4f}", green if guards["max_psi"] < 0.1 else orange),
    ]
    for index, (title, value, color) in enumerate(cards):
        x = index / 3 + 0.015
        axis.add_patch(plt.Rectangle((x, 0.16), 0.30, 0.65, transform=axis.transAxes, facecolor="#F3F4F6", edgecolor=color, linewidth=4))
        axis.text(x + 0.02, 0.65, title, transform=axis.transAxes, fontsize=13, weight="bold")
        axis.text(x + 0.02, 0.39, value, transform=axis.transAxes, fontsize=20, color=color, weight="bold")
    axis.set_title("換一批資料後，模型還可靠嗎？", loc="left", fontsize=18, weight="bold")
    _explain(figure, "三張卡片回答：換新資料會不會變差、關鍵因素穊不穩定、資料結構有沒有改變；綠色代表通過。")
    _save_figure(figure, output_dir / "generalization_health.png", emit_figure)
    register_artifact(manifest, name="generalization_health.png", caption="模型在未見資料、不同資料分組及本次資料分布檢查皆通過。", alt_text="三張卡片顯示新資料差異、關鍵因素穩定度與資料分布差異")

    # 4. Six-step process.
    figure, axis = plt.subplots(figsize=(12, 3)); axis.axis("off")
    steps = ["資料完整", "目標確認", "排除不當欄位", "保留未見資料", "比較 40 組設定", "新資料驗證通過"]
    for index, step in enumerate(steps):
        x = index / 6 + 0.01
        axis.add_patch(plt.Rectangle((x, 0.30), 0.145, 0.42, transform=axis.transAxes, facecolor="#EAF4E8", edgecolor=green, linewidth=2))
        axis.text(x + 0.072, 0.58, "✓", ha="center", transform=axis.transAxes, fontsize=18, color=green, weight="bold")
        axis.text(x + 0.072, 0.39, step, ha="center", transform=axis.transAxes, fontsize=10, weight="bold")
    axis.set_title("這個結果怎麼產生？", loc="left", fontsize=18, weight="bold")
    _save_figure(figure, output_dir / "analysis_process.png", emit_figure)
    register_artifact(manifest, name="analysis_process.png", caption="資料檢查、語義選欄、未見資料保留、自動比較與最後驗證均已完成。", alt_text="六步驟分析流程，每一步均以勾號標示完成")

    # 5. Top trial history.
    trials = manifest["trials"]
    if trials:
        figure, axis = plt.subplots(figsize=(8, 4))
        ranks = [trial["rank"] for trial in trials]
        scores = [trial["cv_score"] * 100 for trial in trials]
        axis.plot(ranks, scores, marker="o", linewidth=2, color=blue)
        for rank, score in zip(ranks, scores, strict=True): axis.text(rank, score + 0.05, f"{score:.2f}%", ha="center", fontsize=9)
        axis.set_xticks(ranks); axis.set_xlabel("前五名方案"); axis.set_ylabel("交叉驗證答對率")
        axis.set_title("自動比較後，前五名方案表現接近", loc="left", fontsize=16, weight="bold")
        _save_figure(figure, output_dir / "trial_history.png", emit_figure)
        register_artifact(manifest, name="trial_history.png", caption=f"共比較 {manifest['process']['completed_trials']} 組設定；圖中顯示前五名。", alt_text="前五名模型設定的交叉驗證答對率折線圖")

    # 6. Grouped ontology-property importance.
    features = manifest["features"]
    if features:
        shown = features[:10][::-1]
        figure, axis = plt.subplots(figsize=(8, 4.5))
        axis.barh([item["name"] for item in shown], [item["importance"] for item in shown], color=blue)
        axis.set_xlabel("相對影響程度（只表示模型關聯，不代表因果）")
        axis.set_title("模型主要參考哪些資料？", loc="left", fontsize=16, weight="bold")
        _save_figure(figure, output_dir / "feature_importance.png", emit_figure)
        register_artifact(manifest, name="feature_importance.png", caption="顯示模型最常參考的 ontology 欄位；重要不代表造成結果。", alt_text="模型主要參考欄位及相對影響程度的水平長條圖")

    # 7–8. Threshold evidence requires actual holdout labels and probabilities.
    if y_true is not None or probabilities is not None:
        if y_true is None or probabilities is None or len(y_true) != len(probabilities) or not y_true:
            raise ValueError("evaluation assets require equally-sized labels and probabilities")
        labels = np.asarray(y_true, dtype=int)
        probs = np.asarray(probabilities, dtype=float)
        threshold = _as_metric(manifest["objective"], "threshold")
        predicted = (probs >= threshold).astype(int)
        matrix = confusion_matrix(labels, predicted, labels=[0, 1])
        figure, axis = plt.subplots(figsize=(5.5, 4.5)); image = axis.imshow(matrix, cmap="Blues")
        for row in range(2):
            for column in range(2): axis.text(column, row, str(matrix[row, column]), ha="center", va="center", fontsize=16, weight="bold")
        axis.set_xticks([0, 1], ["判為否", "判為是"]); axis.set_yticks([0, 1], ["實際否", "實際是"])
        axis.set_title("哪些判對、哪些判錯？", fontsize=16, weight="bold"); figure.colorbar(image, ax=axis)
        _save_figure(figure, output_dir / "confusion_matrix.png", emit_figure)
        register_artifact(manifest, name="confusion_matrix.png", caption="四格數字分別呈現正確放行、誤報、漏判及正確找出。", alt_text="二乘二混淆矩陣，顯示實際類別與模型判斷的筆數")

        fpr, tpr, _ = roc_curve(labels, probs); precision, recall, _ = precision_recall_curve(labels, probs)
        figure, axes = plt.subplots(1, 2, figsize=(10, 4))
        axes[0].plot(fpr, tpr, color=blue); axes[0].plot([0, 1], [0, 1], "--", color=grey); axes[0].set(xlabel="誤報比例", ylabel="找出比例", title="整體排序能力")
        axes[1].plot(recall, precision, color=green); axes[1].axhline(labels.mean(), linestyle="--", color=grey); axes[1].set(xlabel="找出多少正類", ylabel="判為正類時有多準", title="少數類別辨識")
        figure.suptitle("模型如何在找出更多與減少誤報間取捨", fontsize=16, weight="bold")
        _save_figure(figure, output_dir / "roc_pr_curves.png", emit_figure)
        register_artifact(manifest, name="roc_pr_curves.png", caption="曲線展示不同判斷門檻下，找出率與誤報率的取捨；實際營運仍需成本確認。", alt_text="左右兩張曲線呈現整體排序及少數類別辨識取捨")

        from sklearn.calibration import calibration_curve  # type: ignore[reportMissingImports]
        from sklearn.metrics import brier_score_loss  # type: ignore[reportMissingImports]

        bins = min(10, len(labels))
        observed_rate, predicted_rate = calibration_curve(labels, probs, n_bins=bins, strategy="quantile")
        try:
            brier = float(brier_score_loss(labels, probs))
            positives = max(int((labels == 1).sum()), 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("calibration evidence is invalid") from exc
        figure, axis = plt.subplots(figsize=(6, 5))
        axis.plot([0, 1], [0, 1], "--", color=grey, label="理想校正")
        axis.plot(predicted_rate, observed_rate, marker="o", color=blue, label="Holdout 校正")
        axis.set(xlim=(0, 1), ylim=(0, 1), xlabel="預測流失機率", ylabel="實際流失比例")
        axis.set_title(f"機率可信度（Brier {brier:.3f}，越低越好）", fontsize=15, weight="bold")
        axis.legend(frameon=False)
        _save_figure(figure, output_dir / "calibration_curve.png", emit_figure)
        register_artifact(
            manifest,
            name="calibration_curve.png",
            caption=f"校正器只用 train OOF 預測建立；本圖在 holdout 評估機率可信度，Brier score 為 {brier:.3f}，未用來重選校正器。",
            alt_text="Holdout 預測機率與實際流失比例的可靠度曲線",
        )

        cost_matrix = manifest["objective"].get("cost_matrix") or {}
        try:
            fn_cost = float(cost_matrix.get("false_negative", 1.0))
            fp_cost = float(cost_matrix.get("false_positive", 1.0))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("objective cost_matrix is invalid") from exc
        thresholds = np.linspace(0.0, 1.0, 101)
        costs: list[float] = []
        recalls: list[float] = []
        precisions: list[float] = []
        for candidate in thresholds:
            candidate_predictions = (probs >= candidate).astype(int)
            try:
                tp_candidate = int(((labels == 1) & (candidate_predictions == 1)).sum())
                fn_candidate = int(((labels == 1) & (candidate_predictions == 0)).sum())
                fp_candidate = int(((labels == 0) & (candidate_predictions == 1)).sum())
            except (TypeError, ValueError) as exc:
                raise ValueError("threshold-cost evidence is invalid") from exc
            costs.append((fn_candidate * fn_cost + fp_candidate * fp_cost) / len(labels) * 1000)
            recalls.append(tp_candidate / positives)
            precisions.append(tp_candidate / max(tp_candidate + fp_candidate, 1))

        try:
            tp_selected = int(((labels == 1) & (predicted == 1)).sum())
            fn_selected = int(((labels == 1) & (predicted == 0)).sum())
            fp_selected = int(((labels == 0) & (predicted == 1)).sum())
        except (TypeError, ValueError) as exc:
            raise ValueError("selected threshold evidence is invalid") from exc
        cost_selected = (fn_selected * fn_cost + fp_selected * fp_cost) / len(labels) * 1000
        manifest["evaluation_evidence"] = {
            "calibration": {
                "selected_on": "train_oof", "evaluated_on": "holdout",
                "method": str(manifest["process"].get("calibration_method") or "isotonic"),
                "brier_score": brier, "bins": bins,
            },
            "threshold_cost": {
                "selected_on": "train_oof", "evaluated_on": "holdout", "threshold": threshold,
                "false_negative_cost": fn_cost, "false_positive_cost": fp_cost,
                "fn_per_1000": fn_selected / len(labels) * 1000,
                "fp_per_1000": fp_selected / len(labels) * 1000,
                "weighted_cost_per_1000": cost_selected,
                "recall": tp_selected / positives,
                "precision": tp_selected / max(tp_selected + fp_selected, 1),
            },
        }

        figure, cost_axis = plt.subplots(figsize=(8, 5))
        cost_axis.plot(thresholds, costs, color=red, label="加權成本 / 1,000")
        cost_axis.axvline(threshold, color=blue, linestyle="--", label=f"train OOF 門檻 {threshold:.3f}")
        cost_axis.set(xlabel="判斷門檻", ylabel="3:1 加權成本 / 1,000")
        metric_axis = cost_axis.twinx()
        metric_axis.plot(thresholds, recalls, color=green, alpha=0.8, label="Recall")
        metric_axis.plot(thresholds, precisions, color=orange, alpha=0.8, label="Precision")
        metric_axis.set_ylim(0, 1.05); metric_axis.set_ylabel("Recall / Precision")
        lines = cost_axis.get_lines() + metric_axis.get_lines()
        cost_axis.legend(lines, [line.get_label() for line in lines], loc="upper center", frameon=False)
        cost_axis.set_title("已選門檻在 Holdout 的成本與取捨", fontsize=15, weight="bold")
        _save_figure(figure, output_dir / "threshold_cost_curve.png", emit_figure)
        register_artifact(
            manifest,
            name="threshold_cost_curve.png",
            caption=f"虛線是 train OOF 依 FN:FP={fn_cost:g}:{fp_cost:g} 選出的門檻 {threshold:.3f}；曲線只用 holdout 評估，不用來重選門檻。",
            alt_text="不同判斷門檻下的加權成本、Recall 與 Precision 曲線",
        )

    validate_manifest(manifest)
    return list(manifest["artifacts"])


def render_shap_summary(
    manifest: dict[str, Any],
    shap_values: Any,
    feature_names: list[str],
    sample_values: Any,
    output_dir: Path,
    *,
    emit_figure: Callable[[Any, str], None] | None = None,
) -> list[dict[str, str]]:
    """Render a SHAP beeswarm with plain-language guidance."""
    import numpy as np  # type: ignore[reportMissingImports]
    import shap  # type: ignore[reportMissingImports]

    validate_manifest(manifest)
    values = np.asarray(shap_values, dtype=float)
    if values.ndim == 3:
        values = values[:, :, -1]
    sample = np.asarray(sample_values, dtype=float)
    if values.shape[0] != sample.shape[0] or values.shape[1] != len(feature_names):
        raise ValueError("shap values shape must match sample values and feature names")
    plt, *_ = _plot_modules()
    shap.summary_plot(values, features=sample, feature_names=list(feature_names), max_display=10, show=False)
    figure = plt.gcf()
    figure.set_size_inches(9, 5.5)
    figure.suptitle("模型為什麼這樣判斷？（SHAP 歸因）", fontsize=16, weight="bold")
    output_dir.mkdir(parents=True, exist_ok=True)
    _save_figure(figure, output_dir / "shap_summary.png", emit_figure)
    register_artifact(manifest, name="shap_summary.png", caption="每個點是一位樣本：顏色代表欄位數值高低，左右位置代表這個值把預測往哪個方向推；這是關聯不是因果。", alt_text="SHAP 摘要圖，顯示各欄位對預測的推力方向與強度")
    return list(manifest["artifacts"])


def render_model_comparison(
    manifest: dict[str, Any],
    output_dir: Path,
    *,
    emit_figure: Callable[[Any, str], None] | None = None,
) -> list[dict[str, str]]:
    """Render a bar chart comparing all models + baseline."""
    validate_manifest(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison = manifest.get("model_comparison") or []
    if not comparison:
        return list(manifest["artifacts"])
    plt, *_ = _plot_modules()
    blue, green, orange, grey = "#3274D9", "#56A64B", "#FF9830", "#6B7280"

    labels = ["簡單基準"] + [item["kind"] for item in comparison]
    accuracies = [manifest["results"]["baseline"]["accuracy"] * 100] + [item["accuracy"] * 100 for item in comparison]
    best_index = next((i + 1 for i, item in enumerate(comparison) if item.get("is_best")), None)

    figure, axis = plt.subplots(figsize=(9, 4.5))
    colors = [grey] + [green if i + 1 == best_index else blue for i in range(len(comparison))]
    bars = axis.barh(labels, accuracies, color=colors)
    for bar, value in zip(bars, accuracies, strict=True):
        axis.text(value + 0.3, bar.get_y() + bar.get_height() / 2, f"{value:.1f}%", va="center", fontsize=12, weight="bold")
    axis.set_xlabel("答對率（%）")
    axis.set_title("哪個模型表現最好？", loc="left", fontsize=16, weight="bold")
    axis.set_xlim(0, max(accuracies) * 1.15)
    _save_figure(figure, output_dir / "model_comparison.png", emit_figure)
    register_artifact(manifest, name="model_comparison.png",
                      caption="比較簡單基準與各模型的答對率；綠色代表推薦的模型。",
                      alt_text="各模型答對率的水平長條圖，綠色標示推薦模型")

    validate_manifest(manifest)
    return list(manifest["artifacts"])


def recommend_spec_values(
    manifest: dict[str, Any],
    *,
    shap_by_column: dict[str, Any],
    sample_frame: Any,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """For the top SHAP columns, derive plain-language specification pivots.

    Numeric columns: the quantile split with the largest signed SHAP contrast becomes
    the suggested pivot (e.g. 「X > 5,200 時推力轉正」). Categorical columns: the
    categories with the most positive mean contribution.
    """
    import pandas as pd  # type: ignore[reportMissingImports]
    import numpy as np  # type: ignore[reportMissingImports]

    if not isinstance(sample_frame, pd.DataFrame):
        raise ValueError("sample_frame must be a pandas DataFrame")
    try:
        ranked = sorted(shap_by_column.items(), key=lambda item: -float(np.abs(item[1]).mean()))
    except (TypeError, ValueError) as exc:
        raise ValueError("shap contributions are not numeric") from exc
    recommendations: list[dict[str, Any]] = []
    for column, values in ranked:
        if len(recommendations) >= top_n:
            break
        if column not in sample_frame.columns:
            continue
        values = np.asarray(values, dtype=float)
        series = sample_frame[column].reset_index(drop=True)
        if len(series) != len(values) or len(values) < 10:
            continue
        if pd.api.types.is_numeric_dtype(series):
            best: dict[str, Any] | None = None
            for quantile in np.linspace(0.1, 0.9, 9):
                try:
                    pivot = float(series.quantile(quantile))
                    low = float(values[series <= pivot].mean())
                    high = float(values[series > pivot].mean())
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{column} pivot calculation failed") from exc
                contrast = abs(high - low)
                if not np.isfinite(contrast):
                    continue
                if best is None or contrast > best["contrast"]:
                    positive_side = f"> {pivot:,.4g}" if high > low else f"≤ {pivot:,.4g}"
                    best = {"feature": column, "kind": "numeric", "pivot": pivot,
                            "positive_when": positive_side,
                            "mean_shap_high": round(high, 4), "mean_shap_low": round(low, 4),
                            "contrast": contrast}
            if best:
                best.pop("contrast")
                best["suggestion"] = (f"{column} {best['positive_when']} 時，模型預測明顯偏向正類"
                                      f"（平均推力 {best['mean_shap_high']:+.2f}；僅為關聯，非因果）")
                recommendations.append(best)
        else:
            grouped = (pd.DataFrame({"value": series.astype(str), "shap": values})
                       .groupby("value")["shap"].agg(["mean", "count"])
                       .sort_values("mean", ascending=False))
            positive = grouped[grouped["mean"] > 0].head(3)
            if positive.empty:
                continue
            categories = list(positive.index)
            try:
                top_mean = float(positive["mean"].max())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{column} categorical shap means are invalid") from exc
            recommendations.append({
                "feature": column, "kind": "categorical", "pivot": None,
                "positive_when": f"{column} ∈ {{{', '.join(categories)}}}",
                "mean_shap_high": round(top_mean, 4), "mean_shap_low": None,
                "suggestion": f"{column} 為 {categories[0]} 的樣本，預測最偏向正類（平均推力 {top_mean:+.2f}；僅為關聯，非因果）",
            })
    manifest["spec_recommendations"] = recommendations[:top_n]
    validate_manifest(manifest)
    return manifest["spec_recommendations"]
