"""Validator for plain-language ML Grafana Preview dashboards."""
from __future__ import annotations

import json
from typing import Any

REQUIRED_FIRST_VIEW = (
    "模型驗證：通過",
    "營運使用：尚待確認誤判與漏判成本",
    "每 1,000 筆約 870 筆判對、130 筆判錯",
    "每 1,000 筆少錯約 36 筆",
    "確認漏判與誤判成本",
)
ROW_MEANINGS = (("決策",), ("過程", "流程"), ("結果",), ("泛化", "解釋", "工程"))
FORBIDDEN_KEYS = {"raw_rows", "frame", "python_code", "credentials", "physical_path", "signed_url"}
ML_TAG = "ask-o11y-ml"
PREVIEW_TAG = "ask-o11y-preview"


def _panels(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for panel in value:
        flattened.append(panel)
        nested = panel.get("panels")
        if isinstance(nested, list):
            flattened.extend(_panels([item for item in nested if isinstance(item, dict)]))
    return flattened


def _walk(value: Any, key: str = "") -> None:
    if key in FORBIDDEN_KEYS or key.endswith("_path") or key.endswith("_url"):
        raise ValueError(f"forbidden dashboard key: {key}")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _walk(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            _walk(child, key)
    elif isinstance(value, str):
        if "/assets/" in value or "?token=" in value or value.startswith("/tmp/"):
            raise ValueError("dashboard contains a resolved URL or physical path before bridge resolution")


def validate_ml_dashboard_minimum(dashboard: dict[str, Any]) -> None:
    """Runtime write-gate for dashboards tagged as ML (any tag containing 'ml').

    Deliberately looser than validate_preview_dashboard: any layout is allowed as
    long as the decision summary, collapsed technical details, opaque bindings,
    and image/text-only panels are present.
    """
    tags = dashboard.get("tags")
    if not isinstance(tags, list):
        raise ValueError("dashboard tags are required")
    if any("ml" in str(tag).lower() for tag in tags) and PREVIEW_TAG not in tags:
        raise ValueError("ML dashboard must remain a tagged ask-o11y-preview")
    top_level = dashboard.get("panels")
    if not isinstance(top_level, list) or not top_level:
        raise ValueError("dashboard panels are required")
    flattened = _panels([panel for panel in top_level if isinstance(panel, dict)])
    if any(panel.get("targets") for panel in flattened):
        raise ValueError("Sandbox analysis dashboard may contain image/text panels only")
    allowed_types = {"row", "text"}
    if any(panel.get("type") not in allowed_types for panel in flattened):
        raise ValueError("ML Preview contains an unsupported panel type")
    serialized_first_row = json.dumps(top_level[: max(len(top_level) // 2, 1)], ensure_ascii=False)
    if "模型驗證" not in serialized_first_row:
        raise ValueError("ML dashboard first half must contain a model-evidence statement")
    serialized_all = json.dumps(flattened, ensure_ascii=False)
    for required_phrase in ("分析目的", "結論", "資料分布"):
        if required_phrase not in serialized_all:
            raise ValueError(f"ML dashboard must state its {required_phrase}")
    image_count = 0
    data_profile_images = 0
    technical_collapsed = False
    for panel in flattened:
        title = str(panel.get("title") or "")
        options = panel.get("options") or {}
        content = str(options.get("content") or "")
        if "<img" in content:
            image_count += 1
            if "alt=" not in content:
                raise ValueError("image panels require alt text")
            visible_text = content.replace("<img", "\x00<img").split("\x00")[0]
            for fragment in content.split("<img")[1:]:
                after = fragment.split(">", 1)[-1]
                stripped = after.replace("<div", " ").replace("</div", " ").replace(">", " ").strip()
                if len(stripped) >= 8:
                    visible_text += stripped
            if len(visible_text.strip()) < 10:
                raise ValueError("image panels require a visible plain-language caption below the chart")
            if "資料分布" in title or "分布" in title:
                data_profile_images += 1
        if "技術" in title:
            if "<details" in content:
                technical_collapsed = True
            elif bool(panel.get("collapsed")):
                technical_collapsed = True
    if image_count < 1:
        raise ValueError("ML dashboard must present at least one image evidence panel")
    if data_profile_images < 1:
        raise ValueError("ML dashboard must include at least one data-distribution panel")
    if not technical_collapsed:
        raise ValueError("technical details must be collapsed (HTML <details> or collapsed row)")
    bindings = []
    for panel in flattened:
        panel_bindings = panel.get("askO11yAssetBindings")
        if panel_bindings is not None:
            if not isinstance(panel_bindings, list):
                raise ValueError("panel asset bindings must be an array")
            bindings.extend(panel_bindings)
    if len(bindings) < 1:
        raise ValueError("ML dashboard requires opaque asset bindings")
    for binding in bindings:
        if not isinstance(binding, dict) or not str(binding.get("placeholder") or "").startswith("$asset_url_") or not str(binding.get("$execution_ref") or "").startswith("artifact://"):
            raise ValueError("invalid opaque asset binding")
    _walk(dashboard)


def validate_preview_dashboard(dashboard: dict[str, Any], manifest: dict[str, Any] | None = None) -> None:
    if not isinstance(dashboard.get("uid"), str) or not dashboard["uid"].strip():
        raise ValueError("dashboard requires a stable UID")
    tags = dashboard.get("tags")
    if not isinstance(tags, list) or "ask-o11y-preview" not in tags or "ask-o11y-ml" not in tags:
        raise ValueError("ML dashboard must remain a tagged Preview")
    top_level = dashboard.get("panels")
    if not isinstance(top_level, list) or not top_level:
        raise ValueError("dashboard panels are required")
    rows = [panel for panel in top_level if isinstance(panel, dict) and panel.get("type") == "row"]
    titles = tuple(str(row.get("title") or "") for row in rows)
    if len(titles) != 4 or any(not any(keyword in title for keyword in meanings) for title, meanings in zip(titles, ROW_MEANINGS, strict=True)):
        raise ValueError("dashboard requires four ordered semantic rows: decision, process, results, generalization/explanation")
    if not bool(rows[-1].get("collapsed")):
        raise ValueError("Generalization/Engineering details must be collapsed by default")
    first_row_index = top_level.index(rows[0])
    second_row_index = top_level.index(rows[1])
    first_view_json = json.dumps(top_level[first_row_index:second_row_index], ensure_ascii=False)
    required_first_view = REQUIRED_FIRST_VIEW
    if manifest is not None:
        decision = manifest.get("decision") or {}
        required_first_view = (
            str(decision.get("model_evidence_status") or ""),
            str(decision.get("operational_status") or ""),
            f"每 1,000 筆約 {decision.get('correct_per_1000')} 筆判對、{decision.get('errors_per_1000')} 筆判錯",
            f"每 1,000 筆少錯約 {decision.get('fewer_errors_per_1000')} 筆",
            str(decision.get("recommended_action") or ""),
        )
    for phrase in required_first_view:
        if not phrase or phrase not in first_view_json:
            raise ValueError(f"first viewport missing plain-language fact: {phrase}")
    flattened = _panels([panel for panel in top_level if isinstance(panel, dict)])
    if any(panel.get("targets") for panel in flattened):
        raise ValueError("Sandbox analysis dashboard may contain image/text panels only")
    allowed_types = {"row", "text"}
    if any(panel.get("type") not in allowed_types for panel in flattened):
        raise ValueError("ML Preview contains an unsupported panel type")
    serialized = json.dumps(dashboard, ensure_ascii=False)
    placeholders = {"$asset_url_" + token.split('"', 1)[0] for token in serialized.split("$asset_url_")[1:]}
    bindings = []
    for panel in flattened:
        panel_bindings = panel.get("askO11yAssetBindings")
        if panel_bindings is not None:
            if not isinstance(panel_bindings, list):
                raise ValueError("panel asset bindings must be an array")
            bindings.extend(panel_bindings)
    if len(bindings) < 2 or len(placeholders) < 2:
        raise ValueError("ML Preview requires at least two opaque image bindings")
    bound_placeholders = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"placeholder", "$execution_ref", "output_index"}:
            raise ValueError("invalid opaque asset binding shape")
        placeholder = binding.get("placeholder")
        output_index = binding.get("output_index")
        if not isinstance(placeholder, str) or not placeholder.startswith("$asset_url_") or not isinstance(binding.get("$execution_ref"), str) or not str(binding["$execution_ref"]).startswith("artifact://") or isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
            raise ValueError("invalid opaque asset binding")
        bound_placeholders.add(placeholder)
    if not bound_placeholders.issubset(placeholders):
        raise ValueError("asset binding placeholder is not used by a panel")
    for panel in flattened:
        content = json.dumps(panel.get("options") or {}, ensure_ascii=False)
        if "<img" in content and "alt=" not in content:
            raise ValueError("image panels require alt text")
    _walk(dashboard)
