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
    known_sections = REQUIRED_SECTIONS | {"purpose", "conclusion", "operating_scenarios", "spec_recommendations", "narrative", "metric_guidance", "model_comparison", "evaluation_evidence", "data_atlas", "feature_target_relationships", "error_slices"}
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
    atlas = manifest.get("data_atlas")
    if atlas is not None:
        if not isinstance(atlas, dict) or not isinstance(atlas.get("fields"), list) or len(atlas["fields"]) > 24:
            raise ValueError("manifest data_atlas fields are invalid")
        correlation = atlas.get("correlation")
        warnings = atlas.get("warnings")
        if not isinstance(correlation, dict) or len(correlation.get("columns") or []) > 12 or not isinstance(warnings, list) or len(warnings) > 8:
            raise ValueError("manifest data_atlas is outside presentation bounds")
        if len(correlation.get("matrix") or []) != len(correlation.get("columns") or []):
            raise ValueError("manifest data_atlas correlation is not square")
    for section, maximum in (("feature_target_relationships", 4), ("error_slices", 3)):
        items = manifest.get(section)
        if items is not None:
            if not isinstance(items, list) or len(items) > maximum:
                raise ValueError(f"manifest {section} is outside presentation bounds")
            if any(not isinstance(item, dict) or not item.get("feature") or not isinstance(item.get("groups"), list) or len(item["groups"]) > 8 for item in items):
                raise ValueError(f"manifest {section} groups are invalid")
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


def build_data_atlas(
    frame: Any,
    *,
    target: str | None = None,
    fields_view: list[dict[str, Any]] | None = None,
    max_fields: int = 24,
) -> dict[str, Any]:
    """Compute bounded, ontology-ordered shape, concentration, and correlation facts."""
    import numpy as np  # type: ignore[reportMissingImports]
    import pandas as pd  # type: ignore[reportMissingImports]

    def number(value: Any, where: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"data atlas {where} is not numeric") from exc

    def count(value: Any, where: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"data atlas {where} is not an integer") from exc

    metadata = {
        str(item.get("name") or item.get("physical_name")): item
        for item in (fields_view or [])
        if item.get("name") or item.get("physical_name")
    }
    def infer_metadata(name: str) -> dict[str, Any]:
        import pandas as pd  # type: ignore[reportMissingImports]

        normalized = name.lower().replace(" ", "_")
        series = frame[name]
        if normalized == "id" or normalized.endswith("_id") or normalized.startswith("id_"):
            semantic_kind = "identifier"
        elif pd.api.types.is_datetime64_any_dtype(series):
            semantic_kind = "temporal"
        elif pd.api.types.is_numeric_dtype(series):
            semantic_kind = "measurement"
        else:
            semantic_kind = "categorical"
        unit = None
        if "temp" in normalized or normalized.endswith("_c"):
            unit = "°C"
        elif "month" in normalized or "tenure" in normalized:
            unit = "month"
        elif "age" in normalized or normalized.endswith("_years"):
            unit = "year"
        elif any(token in normalized for token in ("charge", "cost", "price", "revenue", "refund")):
            unit = "currency"
        elif "gb" in normalized:
            unit = "GB"
        elif any(token in normalized for token in ("percent", "ratio", "rate")):
            unit = "ratio"
        return {"semantic_kind": semantic_kind, "unit": unit, "analysis_role": "feature"}

    present = [str(column) for column in frame.columns if str(column) != target]
    absent_governed = [
        name for name, item in metadata.items()
        if name not in frame.columns and (item.get("analysis_role") == "forbidden" or item.get("semantic_kind") == "target_proxy")
    ]

    def order_key(name: str) -> tuple[str, str, str]:
        item = metadata.get(name) or (infer_metadata(name) if name in frame.columns else {})
        return (str(item.get("semantic_kind") or "unregistered"), str(item.get("unit") or ""), name)

    names = sorted(absent_governed, key=order_key) + sorted(present, key=order_key)
    fields: list[dict[str, Any]] = []
    warnings: list[str] = []
    for name in names[:max_fields]:
        available = name in frame.columns
        item = metadata.get(name) or (infer_metadata(name) if available else {})
        metadata_source = "ontology" if name in metadata else "inferred"
        entry: dict[str, Any] = {
            "name": name,
            "semantic_kind": str(item.get("semantic_kind") or "unregistered"),
            "unit": item.get("unit"),
            "role": str(item.get("analysis_role") or "unknown"),
            "metadata_source": metadata_source,
            "available": available,
            "missing_rate": None,
            "numeric": None,
            "categorical": None,
            "flag": None,
        }
        if item.get("reason"):
            entry["reason"] = str(item["reason"])
        if not available:
            fields.append(entry)
            continue
        series = frame[name]
        missing_rate = round(number(series.isna().mean(), f"{name} missing rate"), 4)
        entry["missing_rate"] = missing_rate
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            values = pd.to_numeric(series, errors="coerce").dropna().astype(float)
            if values.empty:
                numeric = {"mean": None, "std": None, "cv": None, "skew": None, "q1": None, "median": None, "q3": None, "outlier_share": None}
            else:
                mean = number(values.mean(), f"{name} mean")
                std = number(values.std(ddof=0), f"{name} standard deviation")
                q1, median, q3 = (number(value, f"{name} quantile") for value in values.quantile([0.25, 0.5, 0.75]).tolist())
                iqr = q3 - q1
                outlier_share = 0.0 if iqr == 0 else number(((values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)).mean(), f"{name} outlier share")
                skew = number(values.skew(), f"{name} skew") if len(values) >= 3 else 0.0
                if not math.isfinite(skew):
                    skew = 0.0
                numeric = {
                    "mean": round(mean, 4), "std": round(std, 4),
                    "cv": None if mean == 0 else round(abs(std / mean), 4),
                    "skew": round(skew, 4), "q1": round(q1, 4), "median": round(median, 4),
                    "q3": round(q3, 4), "outlier_share": round(outlier_share, 4),
                    "distinct": count(values.nunique(), f"{name} distinct count"),
                }
                if std == 0 or (mean != 0 and abs(std / mean) < 0.02):
                    entry["flag"] = "low_variance"
            entry["numeric"] = numeric
        else:
            values = series.dropna().astype(str)
            counts = values.value_counts()
            total = max(len(values), 1)
            top_share = number(counts.iloc[0] / total, f"{name} top share") if not counts.empty else 0.0
            entry["categorical"] = {
                "distinct": count(values.nunique(), f"{name} distinct count"),
                "top_share": round(top_share, 4),
                "top3_share": round(number(counts.head(3).sum() / total, f"{name} top-three share"), 4),
            }
            if top_share > 0.99:
                entry["flag"] = "low_variance"
        if missing_rate > 0.4:
            entry["flag"] = "high_missing"
        if entry["flag"] and len(warnings) < 8:
            warnings.append(f"{entry['flag']}:{name}")
        fields.append(entry)

    numeric_names = sorted(
        [name for name in present if pd.api.types.is_numeric_dtype(frame[name]) and name != target],
        key=order_key,
    )[:12]
    if numeric_names:
        correlation = frame[numeric_names].corr(method="spearman").fillna(0.0)
        correlation_values = correlation.to_numpy(copy=True)
        np.fill_diagonal(correlation_values, 1.0)
        matrix = [[round(number(value, "correlation"), 4) for value in row] for row in correlation_values]
    else:
        matrix = []
    sources = {item["metadata_source"] for item in fields}
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    return {"metadata_source": source, "fields": fields, "correlation": {"columns": numeric_names, "matrix": matrix}, "warnings": warnings}


def render_data_atlas_assets(
    manifest: dict[str, Any],
    output_dir: Path,
    *,
    frame: Any,
    target: str | None = None,
    fields_view: list[dict[str, Any]] | None = None,
    emit_figure: Callable[[Any, str], None] | None = None,
) -> list[dict[str, str]]:
    """Render ontology-ordered field map, distributions, and correlations."""
    import pandas as pd  # type: ignore[reportMissingImports]

    atlas = build_data_atlas(frame, target=target, fields_view=fields_view)
    manifest["data_atlas"] = atlas
    validate_manifest(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    plt, *_ = _plot_modules()
    colors = {
        "measurement": "#3274D9", "treatment_candidate": "#FF9830", "categorical": "#A352CC",
        "temporal": "#5794F2", "identifier": "#6B7280", "target_proxy": "#E02F44", "unregistered": "#6B7280",
    }

    fields = atlas["fields"]
    if fields:
        shown = fields[::-1]
        figure, axis = plt.subplots(figsize=(10, max(4, len(shown) * 0.38)))
        missing = [100.0 if not item["available"] else (item["missing_rate"] or 0) * 100 for item in shown]
        field_colors = ["#E02F44" if item["role"] == "forbidden" else colors.get(item["semantic_kind"], "#56A64B") for item in shown]
        axis.scatter(missing, [item["name"] for item in shown], c=field_colors, s=90, zorder=3)
        axis.grid(axis="x", alpha=0.25, zorder=0)
        for row, item in enumerate(shown):
            detail = "未進入查詢" if not item["available"] else f"缺失 {(item['missing_rate'] or 0) * 100:.1f}%"
            if item["flag"]:
                detail += f" · {item['flag']}"
            axis.text(missing[row] + 0.8, row, detail, va="center", fontsize=9)
        axis.set_xlim(0, max(105, max(missing, default=0) + 25)); axis.set_xlabel("缺失率（%）；未進入查詢欄位以 100% 顯示")
        axis.set_title("Ontology 欄位地圖：哪些資料可用、哪些被排除？", loc="left", fontsize=16, weight="bold")
        _save_figure(figure, output_dir / "ontology_field_map.png", emit_figure)
        source_note = "欄位角色來自 approved ontology。" if atlas["metadata_source"] == "ontology" else "未完整連結 approved ontology；標成 inferred 的角色與單位由 deterministic 規則推論。"
        register_artifact(manifest, name="ontology_field_map.png", caption=f"{source_note} 紅色 forbidden 欄位不進入模型，並標示缺失與低變異。", alt_text="欄位語義地圖，以顏色區分量測、類別、處置候選、禁止與推論欄位")

    available_fields = [item for item in fields if item["available"] and item["name"] in frame.columns][:12]
    if available_fields:
        columns = 3
        rows = math.ceil(len(available_fields) / columns)
        figure, axes = plt.subplots(rows, columns, figsize=(15, max(4, rows * 3.3)))
        axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]
        for axis, item in zip(axes_list, available_fields, strict=False):
            series = frame[item["name"]]
            color = colors.get(item["semantic_kind"], "#56A64B")
            if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > 10:
                axis.hist(series.dropna(), bins=20, color=color)
                if item["numeric"] and item["numeric"]["median"] is not None:
                    axis.axvline(item["numeric"]["median"], color="#111827", linestyle="--", linewidth=1)
            else:
                counts = series.dropna().astype(str).value_counts().head(8).sort_index()
                axis.bar(counts.index.tolist(), counts.tolist(), color=color)
                axis.tick_params(axis="x", rotation=35)
            unit = f" ({item['unit']})" if item["unit"] else ""
            axis.set_title(f"{item['name']}{unit}", fontsize=11, weight="bold")
        for axis in axes_list[len(available_fields):]:
            axis.axis("off")
        figure.suptitle("核可欄位的分布形狀與集中性", fontsize=17, weight="bold")
        _save_figure(figure, output_dir / "distribution_small_multiples.png", emit_figure)
        register_artifact(manifest, name="distribution_small_multiples.png", caption="每格呈現一個核可欄位的實際分布；虛線為數值欄中位數，可看出偏態、集中與離群。", alt_text="依 ontology 語義群排列的欄位分布小 multiples")

    correlation = atlas["correlation"]
    names = correlation["columns"]
    if len(names) >= 2:
        figure, axis = plt.subplots(figsize=(8.5, 7))
        image = axis.imshow(correlation["matrix"], cmap="coolwarm", vmin=-1, vmax=1)
        axis.set_xticks(range(len(names)), names, rotation=45, ha="right", fontsize=9)
        axis.set_yticks(range(len(names)), names, fontsize=9)
        for row, values in enumerate(correlation["matrix"]):
            for column, value in enumerate(values):
                axis.text(column, row, f"{value:.2f}", ha="center", va="center", fontsize=8,
                          color="white" if abs(value) > 0.6 else "black")
        figure.colorbar(image, ax=axis, shrink=0.8)
        axis.set_title("Ontology 分組的 Spearman 相關性", fontsize=15, weight="bold")
        _save_figure(figure, output_dir / "semantic_correlation.png", emit_figure)
        register_artifact(manifest, name="semantic_correlation.png", caption="欄位依 semantic kind 與單位分組排序；高相關表示一起變動，不代表因果。", alt_text="依 ontology 語義與單位排序的 Spearman 相關係數熱圖")

    return list(manifest["artifacts"])


def _safe_number(value: Any, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} is not numeric") from exc


def _safe_count(value: Any, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} is not an integer") from exc


def _group_feature(series: Any, *, bins: int) -> Any:
    """Return bounded display groups: quantiles for continuous values, labels otherwise."""
    import pandas as pd  # type: ignore[reportMissingImports]

    if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > 10:
        grouped = pd.qcut(series, q=min(bins, series.nunique(dropna=True)), duplicates="drop")
        return grouped.astype(str).where(series.notna(), "缺失")
    values = series.astype("string").fillna("缺失")
    keep = set(values.value_counts().head(8).index.tolist())
    return values.where(values.isin(keep), "其他").astype(str)


def build_feature_target_relationships(
    frame: Any,
    *,
    target: str,
    features: list[str],
    target_values: list[int] | None = None,
    positive_class: Any = None,
    max_features: int = 4,
) -> list[dict[str, Any]]:
    """Describe feature/target shapes without using them to select the model or threshold."""
    import pandas as pd  # type: ignore[reportMissingImports]

    if target_values is not None:
        if len(target_values) != len(frame):
            raise ValueError("feature-target values must align with the descriptive frame")
        labels = pd.Series(target_values, index=frame.index, dtype=int)
    elif positive_class is not None:
        labels = (frame[target].astype(str).str.strip() == str(positive_class)).astype(int)
    else:
        labels = pd.to_numeric(frame[target], errors="coerce")
    output: list[dict[str, Any]] = []
    for feature in [name for name in features if name in frame.columns and name != target][:max_features]:
        groups = _group_feature(frame[feature], bins=6)
        view = pd.DataFrame({"group": groups, "target": labels}).dropna(subset=["target"])
        facts: list[dict[str, Any]] = []
        for label, values in view.groupby("group", sort=True):
            facts.append({
                "label": str(label),
                "count": _safe_count(len(values), f"{feature} group count"),
                "positive_rate": round(_safe_number(values["target"].mean(), f"{feature} positive rate"), 4),
            })
        if len(facts) >= 2:
            output.append({"feature": feature, "groups": facts[:8]})
    return output


def build_error_slices(
    frame: Any,
    *,
    y_true: list[int],
    probabilities: list[float],
    threshold: float,
    features: list[str],
    max_features: int = 3,
) -> list[dict[str, Any]]:
    """Describe where locked-threshold holdout errors concentrate; not a fairness verdict."""
    import numpy as np  # type: ignore[reportMissingImports]
    import pandas as pd  # type: ignore[reportMissingImports]

    if not y_true or len(frame) != len(y_true) or len(y_true) != len(probabilities):
        raise ValueError("error slices require an aligned non-empty holdout frame, labels, and probabilities")
    actual = np.asarray(y_true, dtype=int)
    predicted = (np.asarray(probabilities, dtype=float) >= threshold).astype(int)
    minimum_count = max(2, math.ceil(len(actual) * 0.02))
    output: list[dict[str, Any]] = []
    for feature in [name for name in features if name in frame.columns][:max_features]:
        groups = _group_feature(frame[feature], bins=4)
        view = pd.DataFrame({"group": groups, "actual": actual, "predicted": predicted}, index=frame.index)
        facts: list[dict[str, Any]] = []
        for label, values in view.groupby("group", sort=True):
            count = len(values)
            if count < minimum_count:
                continue
            errors = values["actual"] != values["predicted"]
            facts.append({
                "label": str(label), "count": _safe_count(count, f"{feature} slice count"),
                "error_rate": round(_safe_number(errors.mean(), f"{feature} error rate"), 4),
                "fn": _safe_count(((values["actual"] == 1) & (values["predicted"] == 0)).sum(), f"{feature} false negatives"),
                "fp": _safe_count(((values["actual"] == 0) & (values["predicted"] == 1)).sum(), f"{feature} false positives"),
            })
        if len(facts) >= 2:
            output.append({"feature": feature, "groups": facts[:8]})
    return output


def render_data_story_assets(
    manifest: dict[str, Any],
    output_dir: Path,
    *,
    frame: Any,
    target: str,
    evaluation_frame: Any = None,
    y_true: list[int] | None = None,
    probabilities: list[float] | None = None,
    target_values: list[int] | None = None,
    emit_figure: Callable[[Any, str], None] | None = None,
) -> list[dict[str, str]]:
    """Render descriptive feature/target relationships and locked-threshold holdout error slices."""
    feature_names = [str(item["name"]) for item in manifest.get("features") or []]
    relationships = build_feature_target_relationships(
        frame, target=target, features=feature_names, target_values=target_values,
        positive_class=manifest["objective"].get("positive_class"),
    )
    manifest["feature_target_relationships"] = relationships
    slices: list[dict[str, Any]] = []
    if evaluation_frame is not None and y_true is not None and probabilities is not None:
        slices = build_error_slices(
            evaluation_frame, y_true=y_true, probabilities=probabilities,
            threshold=_as_metric(manifest["objective"], "threshold"), features=feature_names,
        )
    manifest["error_slices"] = slices
    validate_manifest(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    plt, *_ = _plot_modules()

    if relationships:
        columns = 2
        rows = math.ceil(len(relationships) / columns)
        figure, axes = plt.subplots(rows, columns, figsize=(14, max(4, rows * 4)))
        axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]
        for axis, relationship in zip(axes_list, relationships, strict=False):
            labels = [item["label"] for item in relationship["groups"]]
            rates = [item["positive_rate"] for item in relationship["groups"]]
            axis.bar(labels, rates, color="#3274D9")
            axis.set_ylim(0, 1); axis.set_ylabel("正類比例")
            axis.set_title(relationship["feature"], weight="bold")
            axis.tick_params(axis="x", rotation=25, labelsize=9)
        figure.subplots_adjust(hspace=0.7, wspace=0.25, top=0.86, bottom=0.12)
        for axis in axes_list[len(relationships):]:
            axis.axis("off")
        figure.suptitle("資料本身顯示哪些欄位與目標一起變動？（描述性、非因果）", fontsize=17, weight="bold")
        _save_figure(figure, output_dir / "feature_target_relationships.png", emit_figure)
        register_artifact(manifest, name="feature_target_relationships.png", caption="前四個重要欄位分組後的實際正類比例；這是描述性關聯，不用來重選模型或門檻。", alt_text="重要欄位各分組的正類比例圖")

    if slices:
        columns = 2
        rows = math.ceil(len(slices) / columns)
        figure, axes = plt.subplots(rows, columns, figsize=(14, max(4, rows * 4)))
        axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]
        for axis, sliced in zip(axes_list, slices, strict=False):
            labels = [item["label"] for item in sliced["groups"]]
            fn_rates = [item["fn"] / item["count"] for item in sliced["groups"]]
            fp_rates = [item["fp"] / item["count"] for item in sliced["groups"]]
            rates = [fn + fp for fn, fp in zip(fn_rates, fp_rates, strict=True)]
            axis.bar(labels, fn_rates, color="#E02F44", label="漏判 FN")
            axis.bar(labels, fp_rates, bottom=fn_rates, color="#FF9830", label="誤報 FP")
            axis.set_ylim(0, max(0.1, min(1.0, max(rates, default=0) * 1.25))); axis.set_ylabel("Holdout 錯誤率")
            axis.set_title(sliced["feature"], weight="bold")
            axis.tick_params(axis="x", rotation=25, labelsize=9)
            axis.legend(frameon=False)
        figure.subplots_adjust(hspace=0.7, wspace=0.25, top=0.86, bottom=0.12)
        for axis in axes_list[len(slices):]:
            axis.axis("off")
        figure.suptitle("鎖定門檻後，錯誤集中在哪些資料切片？（非公平性結論）", fontsize=17, weight="bold")
        _save_figure(figure, output_dir / "error_slice_analysis.png", emit_figure)
        register_artifact(manifest, name="error_slice_analysis.png", caption="以鎖定門檻在 holdout 比較各資料切片的漏判 FN 與誤報 FP；只用來找調查方向，不作公平性或因果結論。", alt_text="Holdout 各資料切片錯誤率比較圖")

    return list(manifest["artifacts"])


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
    fields_view: list[dict[str, Any]] | None = None,
    evaluation_frame: Any = None,
    target_values: list[int] | None = None,
) -> list[dict[str, str]]:

    """Render bounded PNG evidence with values and plain-language captions."""
    validate_manifest(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest["artifacts"] = []
    plt, np, confusion_matrix, precision_recall_curve, roc_curve = _plot_modules()
    blue, orange, green, red, grey = "#3274D9", "#FF9830", "#56A64B", "#E02F44", "#6B7280"

    if frame is not None:
        render_data_atlas_assets(
            manifest, output_dir, frame=frame, target=target,
            fields_view=fields_view, emit_figure=emit_figure,
        )

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

    if frame is not None and target and target in frame.columns:
        render_data_story_assets(
            manifest, output_dir, frame=frame, target=target,
            evaluation_frame=evaluation_frame, y_true=y_true, probabilities=probabilities,
            target_values=target_values, emit_figure=emit_figure,
        )

    decision = manifest["decision"]
    results = manifest["results"]
    guards = manifest["guards"]

    # 1. Baseline vs selected outcomes per 1,000 (merged decision view).
    baseline_errors = _round_count((1 - results["baseline"]["accuracy"]) * 1000)
    selected_errors = decision["errors_per_1000"]
    error_counts = [baseline_errors, selected_errors]
    correct_counts = [1000 - value for value in error_counts]
    figure, axis = plt.subplots(figsize=(9, 4))
    axis.barh(["舊模型", "新模型"], correct_counts, color=[grey, green], label="判對")
    axis.barh(["舊模型", "新模型"], error_counts, left=correct_counts, color=orange, label="判錯")
    for row, (correct_count, error_count) in enumerate(zip(correct_counts, error_counts, strict=True)):
        axis.text(correct_count / 2, row, f"判對 {correct_count}", ha="center", va="center", color="white", weight="bold")
        axis.text(correct_count + error_count / 2, row, f"判錯 {error_count}", ha="center", va="center", weight="bold")
    axis.set_xlim(0, 1000); axis.set_xlabel("每 1,000 筆新資料")
    axis.set_title(f"新模型每 1,000 筆少錯約 {decision['fewer_errors_per_1000']} 筆", loc="left", fontsize=17, weight="bold")
    axis.legend(frameon=False, ncol=2)
    _save_figure(figure, output_dir / "baseline_error_comparison.png", emit_figure)
    register_artifact(manifest, name="baseline_error_comparison.png", caption=f"{manifest['plain_language']['accuracy']} {manifest['plain_language']['improvement']}", alt_text=f"舊模型每千筆判錯{baseline_errors}筆，新模型判錯{selected_errors}筆的比較圖")

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


# ---------------------------------------------------------------------------
# Plotly-first interactive figures (data-driven chart-type adaptation)
# ---------------------------------------------------------------------------

def responsive_subplot_grid(count: int) -> list[dict[str, Any]]:
    """Return non-overlapping top-to-bottom subplot domains for one to twelve views."""
    if not 1 <= count <= 12:
        raise ValueError("responsive subplot grid supports one to twelve views")
    if count == 1:
        rows, columns = 1, 1
    elif count == 2:
        rows, columns = 1, 2
    elif count <= 4:
        rows, columns = 2, 2
    elif count <= 6:
        rows, columns = 2, 3
    elif count <= 9:
        rows, columns = 3, 3
    else:
        rows, columns = 3, 4
    left, right, bottom, top = 0.04, 0.98, 0.08, 0.94
    x_gap = 0.035 if columns > 1 else 0.0
    y_gap = 0.10 if rows > 1 else 0.0
    cell_width = (right - left - x_gap * (columns - 1)) / columns
    cell_height = (top - bottom - y_gap * (rows - 1)) / rows
    output = []
    for index in range(count):
        row, column = divmod(index, columns)
        x_start = left + column * (cell_width + x_gap)
        y_end = top - row * (cell_height + y_gap)
        output.append({
            "row": row, "column": column,
            "x": [round(x_start, 4), round(x_start + cell_width, 4)],
            "y": [round(y_end - cell_height, 4), round(y_end, 4)],
        })
    return output


def build_plotly_figures(
    manifest: dict[str, Any],
    *,
    y_true: list[int] | None = None,
    probabilities: list[float] | None = None,
    frame: Any = None,
    target: str | None = None,
    shap_values: Any = None,
    feature_names: list[str] | None = None,
    sample_values: Any = None,
    fields_view: list[dict[str, Any]] | None = None,
    evaluation_frame: Any = None,
    target_values: list[int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build bounded interactive figures for every ML asset, adapting the
    Plotly chart type to each data shape (bar / heatmap / scatter / indicator).
    Output is sanitized through ml_plotly_contract."""
    import sys

    import numpy as np  # type: ignore[reportMissingImports]

    try:
        import ml_plotly_contract  # baked next to this module in the sandbox image
    except ModuleNotFoundError:  # host-side checks load this file by path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import ml_plotly_contract

    def _number(value: Any, where: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"plotly figure {where} is not numeric: {value!r}") from exc

    def _count(value: Any, where: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"plotly figure {where} is not an integer: {value!r}") from exc

    figures: dict[str, dict[str, Any]] = {}
    decision = manifest["decision"]
    guards = manifest["guards"]
    objective = manifest["objective"]

    def _finish(name: str, data: list[dict[str, Any]], layout: dict[str, Any]) -> None:
        figures[name] = ml_plotly_contract.sanitize_figure({"data": data, "layout": layout})

    def axis(**kwargs: Any) -> dict[str, Any]:
        return {key: value for key, value in kwargs.items() if value is not None}

    # 1. Data profile: target counts + top feature distributions (bar / binned bar).
    if frame is not None and target and target in frame.columns:
        import pandas as pd  # type: ignore[reportMissingImports]

        counts = frame[target].astype(str).value_counts()
        data: list[dict[str, Any]] = [{
            "type": "bar", "name": str(target), "x": [str(value) for value in counts.index.tolist()],
            "y": [_count(value, "target count") for value in counts.tolist()],
            "marker": {"color": "#4fd1c5"},
        }]
        layout: dict[str, Any] = {"title": f"資料分布：{target} 與前三大特徵", "showlegend": True, "barmode": "group"}
        top_features = [item["name"] for item in (manifest.get("features") or [])[:3] if item["name"] in frame.columns and item["name"] != target]
        if not top_features:
            top_features = [str(column) for column in frame.select_dtypes(include=[np.number]).columns.drop(target, errors="ignore").tolist()[:3]]
        profile_grid = responsive_subplot_grid(1 + len(top_features[:3]))
        layout["xaxis"] = axis(title=target, type="category", domain=profile_grid[0]["x"], anchor="y")
        layout["yaxis"] = axis(title="样本数", rangemode="tozero", domain=profile_grid[0]["y"], anchor="x")
        for slot, column in enumerate(top_features[:3]):
            series = frame[column]
            if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > 10:
                hist_counts, hist_edges = np.histogram(series.dropna(), bins=15)
                x_values: list[Any] = ((hist_edges[:-1] + hist_edges[1:]) / 2).round(2).tolist()
                y_values: list[Any] = [_count(value, f"{column} bin") for value in hist_counts]
                axis_type = "linear"
            else:
                feature_counts = series.astype(str).value_counts().head(8)
                x_values = [str(value) for value in feature_counts.index.tolist()]
                y_values = [_count(value, f"{column} count") for value in feature_counts.tolist()]
                axis_type = "category"
            axis_id = str(slot + 2)
            data.append({"type": "bar", "name": str(column), "x": x_values, "y": y_values, "xaxis": f"x{axis_id}", "yaxis": f"y{axis_id}", "marker": {"color": "#7fcaa6"}})
            domain = profile_grid[slot + 1]
            layout[f"xaxis{axis_id}"] = axis(title=str(column), type=axis_type, domain=domain["x"], anchor=f"y{axis_id}")
            layout[f"yaxis{axis_id}"] = axis(title="样本数", rangemode="tozero", domain=domain["y"], anchor=f"x{axis_id}")
        _finish("data_profile", data, layout)

    # 2-4. Ontology data atlas: field governance, shape, and semantic correlation.
    if frame is not None:
        import pandas as pd  # type: ignore[reportMissingImports]

        atlas = build_data_atlas(frame, target=target, fields_view=fields_view)
        atlas_fields = atlas["fields"]
        kind_colors = {"measurement": "#4fd1c5", "treatment_candidate": "#eda06a", "categorical": "#a352cc", "temporal": "#5794f2", "identifier": "#9aaab8", "target_proxy": "#e05260", "unregistered": "#9aaab8"}
        if atlas_fields:
            shown_fields = atlas_fields[::-1]
            _finish("ontology_field_map", [{
                "type": "scatter", "mode": "markers", "name": "缺失率 %",
                "x": [100.0 if not item["available"] else round((item["missing_rate"] or 0) * 100, 2) for item in shown_fields],
                "y": [item["name"] for item in shown_fields],
                "marker": {"color": ["#e05260" if item["role"] == "forbidden" else kind_colors.get(item["semantic_kind"], "#7fcaa6") for item in shown_fields]},
                "text": ["未進入查詢" if not item["available"] else " · ".join(value for value in (item["metadata_source"], item["flag"]) if value) for item in shown_fields],
            }], {"title": "Ontology 欄位地圖（顏色＝語義角色）", "xaxis": axis(title="缺失率 %；未進入查詢＝100", range=[0, 100])})

        available = [item for item in atlas_fields if item["available"] and item["name"] in frame.columns][:12]
        if available:
            distribution_data: list[dict[str, Any]] = []
            distribution_layout: dict[str, Any] = {"title": "核可欄位的分布形狀與集中性", "showlegend": False}
            distribution_grid = responsive_subplot_grid(len(available))
            for slot, item in enumerate(available):
                series = frame[item["name"]]
                if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > 10:
                    hist_counts, hist_edges = np.histogram(series.dropna(), bins=15)
                    x_values = ((hist_edges[:-1] + hist_edges[1:]) / 2).round(3).tolist()
                    y_values = [_count(value, f"{item['name']} bin") for value in hist_counts]
                    axis_type = "linear"
                else:
                    value_counts = series.dropna().astype(str).value_counts().head(8).sort_index()
                    x_values = [str(value) for value in value_counts.index.tolist()]
                    y_values = [_count(value, f"{item['name']} count") for value in value_counts.tolist()]
                    axis_type = "category"
                axis_id = "" if slot == 0 else str(slot + 1)
                distribution_data.append({"type": "bar", "name": item["name"], "x": x_values, "y": y_values, "xaxis": f"x{axis_id}", "yaxis": f"y{axis_id}", "marker": {"color": kind_colors.get(item["semantic_kind"], "#7fcaa6")}})
                suffix = "" if slot == 0 else str(slot + 1)
                domain = distribution_grid[slot]
                distribution_layout[f"xaxis{suffix}"] = axis(title=item["name"], type=axis_type, domain=domain["x"], anchor=f"y{axis_id}")
                distribution_layout[f"yaxis{suffix}"] = axis(title="样本数", rangemode="tozero", domain=domain["y"], anchor=f"x{axis_id}")
            _finish("distribution_small_multiples", distribution_data, distribution_layout)

        correlation = atlas["correlation"]
        if len(correlation["columns"]) >= 2:
            _finish("semantic_correlation", [{
                "type": "heatmap", "z": correlation["matrix"],
                "x": correlation["columns"], "y": correlation["columns"], "coloraxis": "coloraxis",
            }], {
                "title": "Ontology 分組的 Spearman 相關性（相關非因果）",
                "coloraxis": {"cmin": -1.0, "cmax": 1.0, "colorscale": [[0.0, "#1f3a4d"], [0.5, "#2b6a7c"], [1.0, "#eda06a"]], "showscale": True},
            })

    # 5-6. Data-to-target relationships and locked-threshold holdout error slices.
    story_features = [str(item["name"]) for item in manifest.get("features") or []]
    if frame is not None and target and target in frame.columns:
        relationships = build_feature_target_relationships(
            frame, target=target, features=story_features, target_values=target_values,
            positive_class=objective.get("positive_class"),
        )
        if relationships:
            traces: list[dict[str, Any]] = []
            layout: dict[str, Any] = {"title": "資料本身的欄位與目標關係（描述性、非因果）", "showlegend": False}
            relationship_grid = responsive_subplot_grid(len(relationships))
            for slot, relationship in enumerate(relationships):
                axis_id = "" if slot == 0 else str(slot + 1)
                traces.append({
                    "type": "bar", "name": relationship["feature"],
                    "x": [item["label"] for item in relationship["groups"]],
                    "y": [item["positive_rate"] for item in relationship["groups"]],
                    "xaxis": f"x{axis_id}", "yaxis": f"y{axis_id}", "marker": {"color": "#4fd1c5"},
                })
                domain = relationship_grid[slot]
                suffix = "" if slot == 0 else str(slot + 1)
                layout[f"xaxis{suffix}"] = axis(title=relationship["feature"], domain=domain["x"], anchor=f"y{axis_id}")
                layout[f"yaxis{suffix}"] = axis(title="正類比例", tickformat=".0%", domain=domain["y"], anchor=f"x{axis_id}", range=[0, 1])
            _finish("feature_target_relationships", traces, layout)

    if evaluation_frame is not None and y_true is not None and probabilities is not None:
        error_slices = build_error_slices(
            evaluation_frame, y_true=y_true, probabilities=probabilities,
            threshold=_as_metric(objective, "threshold"), features=story_features,
        )
        if error_slices:
            traces = []
            layout = {"title": "鎖定門檻後的 Holdout 錯誤切片（非公平性結論）", "showlegend": True, "barmode": "stack"}
            error_grid = responsive_subplot_grid(len(error_slices))
            for slot, sliced in enumerate(error_slices):
                axis_id = "" if slot == 0 else str(slot + 1)
                labels = [item["label"] for item in sliced["groups"]]
                traces.extend([
                    {
                        "type": "bar", "name": f"{sliced['feature']} FN", "x": labels,
                        "y": [round(item["fn"] / item["count"], 4) for item in sliced["groups"]],
                        "xaxis": f"x{axis_id}", "yaxis": f"y{axis_id}", "marker": {"color": "#e05260"},
                    },
                    {
                        "type": "bar", "name": f"{sliced['feature']} FP", "x": labels,
                        "y": [round(item["fp"] / item["count"], 4) for item in sliced["groups"]],
                        "xaxis": f"x{axis_id}", "yaxis": f"y{axis_id}", "marker": {"color": "#eda06a"},
                    },
                ])
                domain = error_grid[slot]
                suffix = "" if slot == 0 else str(slot + 1)
                layout[f"xaxis{suffix}"] = axis(title=sliced["feature"], domain=domain["x"], anchor=f"y{axis_id}")
                layout[f"yaxis{suffix}"] = axis(title="錯誤率", tickformat=".0%", domain=domain["y"], anchor=f"x{axis_id}", range=[0, 1])
            _finish("error_slice_analysis", traces, layout)

    # 7. Baseline vs selected outcomes per 1,000 (merged decision view).
    baseline_errors = _count(round((1 - manifest["results"]["baseline"]["accuracy"]) * 1000), "baseline errors")
    selected_errors = _count(decision["errors_per_1000"], "selected errors")
    _finish("baseline_error_comparison", [
        {"type": "bar", "name": "判對", "orientation": "h", "x": [1000 - baseline_errors, 1000 - selected_errors], "y": ["舊模型", "新模型"], "marker": {"color": "#7fcaa6"}},
        {"type": "bar", "name": "判錯", "orientation": "h", "x": [baseline_errors, selected_errors], "y": ["舊模型", "新模型"], "marker": {"color": "#eda06a"}},
    ], {"title": f"每 1,000 筆少錯約 {decision['fewer_errors_per_1000']} 筆", "barmode": "stack", "xaxis": axis(title="每 1,000 筆", range=[0, 1000])})

    # 5. Generalization health (three KPI indicators).
    health_items = [
        ("泛化差距", guards["generalization_gap"]),
        ("因素穩定度", guards["importance_stability"]),
        ("PSI 漂移", guards["max_psi"]),
    ]
    indicator_data = []
    for slot, (title, value) in enumerate(health_items):
        indicator_data.append({
            "type": "indicator", "value": _number(value, f"guard {title}"), "title": {"text": title},
            "number": {"valueformat": ".3f"},
            "domain": {"x": [slot / 3 + 0.01, (slot + 1) / 3 - 0.01], "y": [0.1, 0.85]},
        })
    _finish("generalization_health", indicator_data, {"title": f"泛化健康檢查（verdict：{guards.get('verdict', 'unknown')}）"})

    # 8. Feature importance (horizontal bar).
    features = manifest.get("features") or []
    if features:
        shown = features[:10][::-1]
        _finish("feature_importance", [
            {"type": "bar", "orientation": "h", "name": "重要性",
             "x": [round(_number(item["importance"], "importance"), 4) for item in shown],
             "y": [str(item["name"]) for item in shown],
             "marker": {"color": "#4fd1c5"}},
        ], {"title": "模型主要參考欄位（關聯非因果）", "xaxis": axis(title="相對影響")})

    # 9-12. Holdout evidence requires labels + calibrated probabilities.
    if y_true is not None and probabilities is not None:
        if len(y_true) != len(probabilities) or not y_true:
            raise ValueError("plotly evidence requires equally-sized labels and probabilities")
        from sklearn.calibration import calibration_curve  # type: ignore[reportMissingImports]
        from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve  # type: ignore[reportMissingImports]

        labels_array = np.asarray(y_true, dtype=int)
        probs_array = np.asarray(probabilities, dtype=float)
        threshold = _as_metric(objective, "threshold")
        predictions = (probs_array >= threshold).astype(int)

        # 9. Confusion matrix heatmap.
        matrix = confusion_matrix(labels_array, predictions, labels=[0, 1])
        _finish("confusion_matrix", [{
            "type": "heatmap",
            "z": [[_count(cell, "confusion") for cell in row] for row in matrix],
            "x": ["判為留存", "判為流失"], "y": ["實際留存", "實際流失"],
            "coloraxis": "coloraxis",
        }], {
            "title": f"混淆矩陣（holdout，門檻 {threshold:.3f}）",
            "coloraxis": {"colorscale": [[0.0, "#1f3a4d"], [1.0, "#4fd1c5"]], "showscale": False},
        })

        # 10. ROC / PR curves (two subplots).
        fpr, tpr, _ = roc_curve(labels_array, probs_array)
        precision, recall, _ = precision_recall_curve(labels_array, probs_array)
        _finish("roc_pr_curves", [
            {"type": "scatter", "mode": "lines", "name": "ROC", "x": [round(_number(v, "fpr"), 4) for v in fpr], "y": [round(_number(v, "tpr"), 4) for v in tpr], "line": {"color": "#4fd1c5", "width": 3}},
            {"type": "scatter", "mode": "lines", "name": "隨機", "x": [0.0, 1.0], "y": [0.0, 1.0], "line": {"color": "#9aaab8", "width": 2}},
            {"type": "scatter", "mode": "lines", "name": "PR", "x": [round(_number(v, "recall"), 4) for v in recall], "y": [round(_number(v, "precision"), 4) for v in precision], "line": {"color": "#eda06a", "width": 3}, "xaxis": "x2", "yaxis": "y2"},
        ], {
            "title": "ROC 與 PR 曲線（holdout）",
            "xaxis": axis(title="誤報比例", domain=[0.06, 0.46]),
            "yaxis": axis(title="找出比例", domain=[0.08, 0.95]),
            "xaxis2": axis(title="找出比例", domain=[0.56, 0.96]),
            "yaxis2": axis(title="判為流失時有多準", domain=[0.08, 0.95]),
            "showlegend": True,
        })

        # 11. Calibration curve (binned observed vs predicted + ideal diagonal).
        bin_count = min(10, len(labels_array))
        observed_rate, predicted_rate = calibration_curve(labels_array, probs_array, n_bins=bin_count, strategy="quantile")
        _finish("calibration_curve", [
            {"type": "scatter", "mode": "lines", "name": "理想校正", "x": [0.0, 1.0], "y": [0.0, 1.0], "line": {"color": "#9aaab8", "width": 2}},
            {"type": "scatter", "mode": "lines+markers", "name": "Holdout 校正", "x": [round(_number(v, "predicted_rate"), 4) for v in predicted_rate], "y": [round(_number(v, "observed_rate"), 4) for v in observed_rate], "line": {"color": "#4fd1c5", "width": 3}},
        ], {"title": "機率校正（train OOF 選、holdout 評估）", "xaxis": axis(title="預測流失機率", range=[0, 1]), "yaxis": axis(title="實際流失比例", range=[0, 1])})

        # 12. Threshold-cost curve (cost + recall/precision, locked threshold annotated).
        cost_matrix = objective.get("cost_matrix") or {}
        fn_cost = _number(cost_matrix.get("false_negative", 3.0), "fn_cost")
        fp_cost = _number(cost_matrix.get("false_positive", 1.0), "fp_cost")
        thresholds = np.linspace(0.0, 1.0, 101)
        costs: list[float] = []
        recalls: list[float] = []
        precisions: list[float] = []
        for candidate in thresholds:
            candidate_predictions = (probs_array >= candidate).astype(int)
            tp_candidate = _count(((labels_array == 1) & (candidate_predictions == 1)).sum(), "tp")
            fn_candidate = _count(((labels_array == 1) & (candidate_predictions == 0)).sum(), "fn")
            fp_candidate = _count(((labels_array == 0) & (candidate_predictions == 1)).sum(), "fp")
            costs.append(round((fn_candidate * fn_cost + fp_candidate * fp_cost) / len(labels_array) * 1000, 2))
            recalls.append(round(tp_candidate / max(_count((labels_array == 1).sum(), "positives"), 1), 4))
            precisions.append(round(tp_candidate / max(tp_candidate + fp_candidate, 1), 4))
        _finish("threshold_cost_curve", [
            {"type": "scatter", "mode": "lines", "name": "加權成本/1,000", "x": [round(_number(v, "threshold"), 3) for v in thresholds], "y": costs, "line": {"color": "#eda06a", "width": 3}},
            {"type": "scatter", "mode": "lines", "name": "Recall", "x": [round(_number(v, "threshold"), 3) for v in thresholds], "y": recalls, "line": {"color": "#4fd1c5"}, "yaxis": "y2"},
            {"type": "scatter", "mode": "lines", "name": "Precision", "x": [round(_number(v, "threshold"), 3) for v in thresholds], "y": precisions, "line": {"color": "#9aaab8"}, "yaxis": "y2"},
        ], {
            "title": f"門檻取捨（train OOF 選 {threshold:.3f}；holdout 僅評估）",
            "xaxis": axis(title="判斷門檻"),
            "yaxis": axis(title="成本/1,000"),
            "yaxis2": axis(title="Recall / Precision", overlaying="y", side="right", range=[0, 1.05]),
            "annotations": [{"text": f"已選門檻 {threshold:.3f}", "x": round(threshold, 3), "y": 1.0, "yref": "paper", "showarrow": False}],
            "showlegend": True,
        })

    # 13. SHAP summary (beeswarm scatter, binned color, no sample IDs).
    if shap_values is not None and feature_names and sample_values is not None:
        shap_array = np.asarray(shap_values, dtype=float)
        mean_abs = np.abs(shap_array).mean(axis=0)
        order = np.argsort(mean_abs)[::-1][:10]
        rng = np.random.default_rng(0)
        sample_array = np.asarray(sample_values)
        data = []
        for slot, column_index in enumerate(order):
            values = shap_array[:, column_index]
            feature_column = sample_array[:, column_index]
            quantiles = np.quantile(feature_column, [0.2, 0.4, 0.6, 0.8])
            color_bins = np.searchsorted(quantiles, feature_column, side="right") + 1
            jitter = rng.uniform(-0.28, 0.28, size=values.shape[0])
            data.append({
                "type": "scatter", "mode": "markers", "name": str(feature_names[column_index])[:40],
                "x": [round(_number(value, "shap"), 4) for value in values],
                "y": [round(_number(slot + 1 + offset, "shap_y"), 4) for offset in jitter],
                "marker": {"color": [_count(bin_index, "shap bin") for bin_index in color_bins], "colorscale": [[0.0, "#1f3a4d"], [1.0, "#4fd1c5"]], "cmin": 1, "cmax": 5, "opacity": 0.75, "size": 5},
            })
        _finish("shap_summary", data, {
            "title": "SHAP 影響（顏色＝特徵值分箱；關聯非因果）",
            "xaxis": axis(title="SHAP 值"),
            "yaxis": axis(title="特徵（上→下依重要性）", tickvals=list(range(1, len(order) + 1)), ticktext=[str(feature_names[index])[:30] for index in order]),
            "showlegend": False,
        })

    return figures
