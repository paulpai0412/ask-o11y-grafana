#!/usr/bin/env python3
"""Generate the data-science system design deck (ML + data flow + Grafana)."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation  # type: ignore[reportMissingImports]
from pptx.dml.color import RGBColor  # type: ignore[reportMissingImports]
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE  # type: ignore[reportMissingImports]
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN  # type: ignore[reportMissingImports]
from pptx.util import Inches, Pt  # type: ignore[reportMissingImports]

OUT = Path("docs/ask-o11y-ml-datascience-design.pptx")
FONT = "Noto Sans CJK TC"
W, H = Inches(13.333), Inches(7.5)

BG = RGBColor(10, 18, 32)
PANEL = RGBColor(20, 31, 50)
PANEL_2 = RGBColor(28, 42, 64)
WHITE = RGBColor(245, 248, 252)
MUTED = RGBColor(174, 188, 207)
CYAN = RGBColor(72, 202, 228)
BLUE = RGBColor(75, 129, 255)
GREEN = RGBColor(67, 202, 147)
ORANGE = RGBColor(255, 169, 77)
RED = RGBColor(255, 103, 117)
PURPLE = RGBColor(177, 119, 255)
LINE = RGBColor(71, 89, 116)

prs = Presentation()
prs.slide_width = W
prs.slide_height = H
blank = prs.slide_layouts[6]


def add_text(slide, x, y, w, h, text, size=18.0, color=WHITE, bold=False,
             align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP, margin=0.04):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def add_lines(slide, x, y, w, h, lines, size=15.0, color=MUTED, line_spacing=1.18):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.margin_left = tf.margin_right = Inches(0.06)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = line_spacing
        run = p.add_run()
        run.text = line
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.color.rgb = color
    return box


def rect(slide, x, y, w, h, fill=PANEL, line=LINE, lw=1.2):
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    s.line.color.rgb = line
    s.line.width = Pt(lw)
    return s


def arrow(slide, x1, y1, x2, y2, color=LINE, width=2.0):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    c.line.end_arrowhead = True
    return c


def node(slide, x, y, w, h, title, subtitle="", accent=CYAN, fill=PANEL_2, ts=14, ss=10):
    rect(slide, x, y, w, h, fill=fill, line=accent)
    add_text(slide, x + 0.08, y + 0.09, w - 0.16, 0.32, title, ts, WHITE, True, PP_ALIGN.CENTER)
    if subtitle:
        add_text(slide, x + 0.08, y + 0.44, w - 0.16, h - 0.5, subtitle, ss, MUTED, False, PP_ALIGN.CENTER)


def base_slide(title, kicker=None):
    slide = prs.slides.add_slide(blank)
    bg = slide.background.fill
    bg.solid(); bg.fore_color.rgb = BG
    if kicker:
        add_text(slide, 0.65, 0.32, 8.0, 0.28, kicker.upper(), 10, CYAN, True)
    add_text(slide, 0.65, 0.62 if kicker else 0.42, 12.0, 0.6, title, 26, WHITE, True)
    add_text(slide, 12.15, 7.05, 0.55, 0.25, f"{len(prs.slides):02d}", 9, MUTED, False, PP_ALIGN.RIGHT)
    return slide


# ── 1 · Title ────────────────────────────────────────────────────────────────
slide = prs.slides.add_slide(blank)
slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
for i, (x, y, c, label) in enumerate([
    (8.6, 0.9, CYAN, "ONTOLOGY"), (10.7, 1.7, BLUE, "ML GATE"),
    (8.6, 2.5, GREEN, "SANDBOX"), (10.7, 3.3, ORANGE, "COST"),
    (8.6, 4.1, PURPLE, "GRAFANA"), (10.7, 4.9, RED, "GOVERN"),
]):
    rect(slide, x, y, 1.7, 0.62, fill=PANEL_2, line=c)
    add_text(slide, x, y + 0.13, 1.7, 0.35, label, 12, c, True, PP_ALIGN.CENTER)
add_text(slide, 0.8, 1.0, 7.0, 0.4, "ASK O11Y × GRAFANA · DATA SCIENCE", 15, CYAN, True)
add_text(slide, 0.8, 1.55, 7.4, 1.6, "機器學習分析平台\n資料科學系統設計", 38, WHITE, True)
add_lines(slide, 0.82, 3.6, 6.9, 2.6, [
    "上傳即語意：ontology candidate + 洩漏/敏感欄位治理閘門",
    "結構化執行：可信模板取代生成碼，訓練-only 前處理",
    "自動調參：untouched holdout + 泛化護欄 + 成本閾值最佳化",
    "主管可讀：每 1,000 筆白話呈現，write-gate 強制最低可讀標準",
], 16, MUTED)
add_text(slide, 0.82, 6.7, 8.0, 0.3, "2026-08 · 實測資料集：UCI Adult / IBM Telco Churn（HuggingFace）", 11, MUTED)

# ── 2 · System topology ─────────────────────────────────────────────────────
slide = base_slide("系統總覽：一個 LLM 規劃者，五個 MCP 信任邊界", "system topology")
node(slide, 0.65, 1.55, 2.5, 1.0, "Ask O11y LLM", "動態規劃 · 唯一規劃者\n選 tools/skills/契約", CYAN, ts=15)
node(slide, 0.65, 3.0, 2.5, 0.95, "Dashboarding Skill", "Grafana JSON 作者\n+ ml-method-selection", BLUE, ts=14)
node(slide, 3.9, 1.55, 2.35, 0.95, "Ontology MCP :8771", "唯讀語意層\nclassify/validate", GREEN)
node(slide, 3.9, 3.0, 2.35, 0.95, "Data Query Planner :8768", "plan-only\n語意/品質/洩漏最終閘門", GREEN)
node(slide, 3.9, 4.45, 2.35, 0.95, "Grafana Query :8772", "唯一資料執行者\nuploads + frame ≤100k/50MB", ORANGE)
node(slide, 6.9, 1.55, 2.35, 0.95, "Sandbox :8777", "隔離·無網路\nexecute_ml_contract", RED)
node(slide, 6.9, 3.0, 2.35, 0.95, "Artifact Bridge :8773", "隱藏·模型不可見\nopaque 綁定 + write-gate", PURPLE)
node(slide, 6.9, 4.45, 2.35, 0.95, "Grafana Write", "內建 update_dashboard\n唯一 Dashboard 寫入者", ORANGE)
node(slide, 9.9, 2.3, 2.75, 2.6, "信任邊界原則", "", CYAN)
add_lines(slide, 10.05, 2.75, 2.5, 2.1, [
    "· 資料查詢只有 Grafana Query",
    "· Dashboard 寫入只有內建 MCP",
    "· Artifact Bridge 對模型隱藏",
    "· Sandbox 無網路無憑證",
    "· 模型只見 opaque refs",
    "· 全部 loopback + bearer",
], 12, MUTED)
for a in [((3.15, 2.05), (3.9, 2.02)), ((3.15, 3.47), (3.9, 3.47)), ((3.15, 4.92), (3.9, 4.92)),
          ((6.25, 2.02), (6.9, 2.02)), ((6.25, 3.47), (6.9, 3.47)), ((6.25, 4.92), (6.9, 4.92)),
          ((9.25, 2.02), (9.9, 3.0)), ((9.25, 3.47), (9.9, 3.4)), ((9.25, 4.92), (9.9, 4.3))]:
    arrow(slide, *a[0], *a[1])
add_lines(slide, 0.65, 6.0, 12.0, 0.9, [
    "治理貫穿全链路：upload 欄位角色 → Planner 語意/品質/洩漏閘門 → Grafana Query plan-hash → Sandbox contract 驗證 → Artifact Bridge ML write-gate。任一層不通即 fail-closed。",
], 13, MUTED)

# ── 3 · End-to-end data flow ────────────────────────────────────────────────
slide = base_slide("端到端資料流：上傳到主管可讀 Preview", "end-to-end data flow")
flow = [
    ("1 · 上傳", "CSV/XLSX ≤50MB\n自動語意標註", CYAN),
    ("2 · 語意候選", "candidate ontology\n+ analysis hints", GREEN),
    ("3 · Analysis Preview", "欄位角色·排除理由\n等使用者確認", BLUE),
    ("4 · Query Plan", "語意閘門釘 hash\nbounded ≤100k rows", GREEN),
    ("5 · Grafana Frame", "唯一執行者\nopaque frame_ref", ORANGE),
    ("6 · execute_ml_contract", "可信模板·零生成碼\n訓練-only 前處理", RED),
    ("7 · Manifest + PNG", "白話 manifest\n≤10 張圖·bounded", PURPLE),
    ("8 · Grafana Preview", "write-gate 驗證\nsame-UID 發布", ORANGE),
]
x = 0.55
for i, (title, sub, color) in enumerate(flow):
    node(slide, x, 1.7, 1.52, 1.15, title, sub, accent=color, ts=12, ss=10)
    if i < len(flow) - 1:
        arrow(slide, x + 1.52, 2.28, x + 1.62, 2.28, color=LINE, width=1.8)
    x += 1.62
node(slide, 0.55, 3.35, 6.0, 1.5, "回饋與修訂迴路", "", ORANGE)
add_lines(slide, 0.7, 3.78, 5.7, 1.0, [
    "· Preview 階段使用者可修訂：排除欄位、成本比、目標定義",
    "· 修訂 = 新 contract hash = 重新過閘門；不繞過任何 gate",
    "· 發布需再次明確確認；same-UID 移除 preview tag，不重跑分析",
], 12.5, MUTED)
node(slide, 7.05, 3.35, 5.7, 1.5, "全程 Provenance", "", PURPLE)
add_lines(slide, 7.2, 3.78, 5.4, 1.0, [
    "· 每層記錄 sha256：source / ontology candidate / plan / code / image",
    "· 模型只見 opaque refs（frame_ref / plan_ref / execution_ref）",
    "· 保留 artifacts：code、provenance、manifest、PNG（retention 管控）",
], 12.5, MUTED)
add_lines(slide, 0.55, 5.3, 12.2, 1.5, [
    "設計重點：資料流沒有捷徑——任何資料進模型前必須先過語意層取得角色；任何訓練必須有 ontology-pinned contract；",
    "任何圖進 Dashboard 必須經 opaque 綁定與 write-gate。LLM 負責理解意圖與呈現，deterministic gate 負責擋下不合規的分析。",
], 14, MUTED)

# ── 4 · Semantic layer & governance ─────────────────────────────────────────
slide = base_slide("語意層與治理：上傳即分類，plan 即攔截", "semantic governance")
roles = [
    ("identifier", "鍵值/權重/序號", "fnlwgt、*_id、全唯一大數值", RED),
    ("leakage_risk", "事後訊號", "Satisfaction/Reason/Rating/Survey", RED),
    ("sensitive", "受保護屬性", "Gender/Sex/Race/Ethnicity", ORANGE),
    ("target_candidate", "預測目標", "末位低基數類別欄 + 少數類比率", GREEN),
    ("temporal / feature", "可用特徵", "時間欄與一般欄位", CYAN),
    ("constant", "常數欄", "抽樣內單一值", MUTED),
]
x = 0.55
for name, meaning, examples, color in roles:
    rect(slide, x, 1.6, 2.02, 1.5, fill=PANEL, line=color)
    add_text(slide, x + 0.1, 1.72, 1.85, 0.3, name, 13, color, True)
    add_text(slide, x + 0.1, 2.06, 1.85, 0.3, meaning, 12, WHITE, True)
    add_lines(slide, x + 0.1, 2.4, 1.85, 0.62, [examples], 10.5, MUTED)
    x += 2.12
node(slide, 0.55, 3.45, 6.0, 1.7, "上傳即產出（best-effort，不擋上傳）", "", GREEN)
add_lines(slide, 0.7, 3.88, 5.7, 1.2, [
    "· candidate-ontology.json：通過 candidate-IR schema",
    "· analysis-hints.json：每欄角色 + missing/minority rate",
    "· quality policy：正類<5% 需申報策略｜缺失>40% 入限制｜min rows 20",
], 12.5, MUTED)
node(slide, 6.85, 3.45, 5.9, 1.7, "三層 hash 驗證（fail-closed）", "", RED)
add_lines(slide, 7.0, 3.88, 5.6, 1.2, [
    "· Planner：contract vs candidate 角色 + source sha256",
    "· Grafana Query：plan_sha256 + candidate snapshot 綁定",
    "· Sandbox：同樣驗證後才允許執行；任一層不通即停止",
], 12.5, MUTED)
add_lines(slide, 0.55, 5.5, 12.2, 1.3, [
    "拒絕碼即語意：LEAKAGE_FIELD_FORBIDDEN / SENSITIVE_FIELD_FORBIDDEN / FIELD_ROLE_FORBIDDEN /",
    "IMBALANCE_STRATEGY_REQUIRED / QUALITY_POLICY_VIOLATION / SPLIT_POLICY_VIOLATION——模型與使用者都能看懂為何被擋。",
], 14, MUTED)

# ── 5 · Structured executor ─────────────────────────────────────────────────
slide = base_slide("結構化 ML 執行器：execute_ml_contract", "structured execution")
add_lines(slide, 0.55, 1.5, 12.2, 0.8, [
    "輸入只有兩個 opaque refs：frame_ref（授權 frame）+ contract_ref（ontology-pinned plan）。",
    "Host 依契約組合確定性模板（ast 驗證）→ 既有授權/audit/provenance 機制執行。LLM 不生成訓練程式碼。",
], 14.5, MUTED)
node(slide, 0.55, 2.5, 3.9, 2.3, "ml_preprocessing", "訓練-only 前處理庫", GREEN)
add_lines(slide, 0.7, 3.0, 3.6, 1.7, [
    "· median+indicator / most-frequent",
    "· one-hot；高基數 fold 內 target encoding",
    "· VIF>10 共線剔除",
    "· nullable-dtype 安全布林 mask",
    "· fit_transform_train_test(train-only)",
], 12, MUTED)
node(slide, 4.7, 2.5, 3.9, 2.3, "ml_autoresearch", "調參 + 泛化護欄", BLUE)
add_lines(slide, 4.85, 3.0, 3.6, 1.7, [
    "· per-kind 固定搜索空間（budget ≤40）",
    "· RandomizedSearchCV + StratifiedKFold",
    "· untouched holdout 最後評估一次",
    "· gap / stability / PSI / objective 護欄",
    "· LightGBM 優先，fallback HistGB",
], 12, MUTED)
node(slide, 8.85, 2.5, 3.9, 2.3, "ml_presentation", "白話呈現契約", PURPLE)
add_lines(slide, 9.0, 3.0, 3.6, 1.7, [
    "· bounded manifest（purpose/結論必填）",
    "· ≤10 PNG：資料分布/每千件/混淆/ROC-PR…",
    "· raw rows / path / signed URL 拒收",
    "· caption + alt text 必附",
], 12, MUTED)
add_lines(slide, 0.55, 5.1, 12.2, 1.6, [
    "為什麼重要：自由生成碼時代的所有執行期失敗（pandas NA crash、API 誤用、字型重設）都源自同一根源。",
    "結構化執行把這類錯誤整類消除，結果可重現；自由 Python 保留給探索性分析，兩條路徑分離。",
    "模板失敗不進 LLM 自修迴圈——模板由 host 持有，修復即平台層修復。",
], 14, MUTED)

# ── 6 · Autotune + guards ───────────────────────────────────────────────────
slide = base_slide("自動調參與泛化護欄", "autotune & generalization")
node(slide, 0.55, 1.6, 5.9, 2.4, "調參協議", "", BLUE)
add_lines(slide, 0.7, 2.05, 5.6, 1.9, [
    "· 搜索空間由 kind 模板宣告，生成碼不得自創",
    "· LightGBM：樹數/學習率/葉數/最小樣本/取樣/正則/scale_pos_weight",
    "· RandomizedSearchCV，budget ≤40，seed 固定",
    "· CV 策略繼承 split policy（分層/時間/分組）",
    "· 調參只在訓練區間；holdout 全程隔離",
], 12.5, MUTED)
guards = [
    ("CV–holdout gap", "≤ 0.05，超過判 overfit 拒絕", RED),
    ("特徵穩定度", "折間 top-K Jaccard ≥ 0.6", ORANGE),
    ("PSI 漂移", "≤ 0.25，超過必須調查", ORANGE),
    ("Objective check", "未達申報門檻 = below_objective", GREEN),
]
y = 1.6
for name, rule, color in guards:
    rect(slide, 6.85, y, 5.9, 0.52, fill=PANEL, line=color)
    add_text(slide, 7.0, y + 0.1, 2.4, 0.3, name, 13, color, True)
    add_text(slide, 9.3, y + 0.1, 3.3, 0.3, rule, 12, WHITE)
    y += 0.62
add_lines(slide, 6.85, y + 0.05, 5.9, 0.6, [
    "verdict ∈ accepted / overfit / unstable / drift / below_objective——",
    "不是 accepted 的結果照樣呈現，但首屏必須標示未通過。",
], 12.5, MUTED)
node(slide, 0.55, 4.35, 12.2, 1.15, "為什麼這樣設計", "", CYAN)
add_lines(slide, 0.7, 4.8, 11.9, 0.7, [
    "泛化不是靠單一指標，而是四道獨立護欄互相補位：gap 抓過擬合、stability 抓不穩定特徵、PSI 抓分布漂移、objective 抓業務門檻。",
    "全部通過才標 accepted；任何一道不通，結果仍然透明呈現但明確標注不可直接部署。",
], 13.5, MUTED)
add_lines(slide, 0.55, 5.95, 12.2, 0.9, [
    "實測（Adult，40×5）：gap 0.0037 / stability 0.776 / PSI 0.0022 → accepted；",
    "實測（Telco，20×5，排除洩漏欄）：gap 0.008 / stability 0.806 / PSI 0.025 → accepted。",
], 13.5, MUTED)

# ── 7 · Cost threshold ──────────────────────────────────────────────────────
slide = base_slide("成本閾值最佳化：把 FN/FP 變成業務決策參數", "cost-based operating point")
add_lines(slide, 0.55, 1.5, 12.2, 0.8, [
    "漏判（FN）與誤攔（FP）是翹翹板：固定模型下降低一個必升高另一個。唯一正確的做法是讓業務申報成本比，",
    "由平台在校準機率上選出加權成本最低的操作點——accuracy 不是唯一目標，營運總成本才是。",
], 14.5, MUTED)
steps = [
    ("OOF 校準", "cross_val_predict 機率\n+ isotonic 校準", CYAN),
    ("閾值掃描", "訓練 CV 機率掃 49 點\nholdout 全程隔離", BLUE),
    ("成本選點", "min(FN×成本+FP×成本)\n支援 minimum_recall", GREEN),
    ("三方案", "cost/balanced/recall\n各附每千位 FN·FP", ORANGE),
]
x = 0.55
for title, sub, color in steps:
    node(slide, x, 2.5, 2.9, 1.2, title, sub, accent=color, ts=14, ss=11)
    if x < 9:
        arrow(slide, x + 2.9, 3.1, x + 3.05, 3.1)
    x += 3.05
node(slide, 0.55, 4.0, 12.2, 2.3, "Telco 實測：漏判成本 = 3 × 誤攔成本", "", GREEN)
add_lines(slide, 0.7, 4.45, 11.9, 1.8, [
    "· 選定門檻 0.301（成本最佳）：每千位漏判 42.6、誤攔 144.1、加權成本 271.9",
    "· 傳統 0.5 門檻：每千位漏判 91.7、誤攔 59.2、加權成本 334.3",
    "· 漏判 −54%，代價是誤攔增加——由業務成本比決定，不是模型決定",
    "· holdout Acc 0.819 / ROC-AUC 0.920 / PR-AUC 0.810；契約未申報成本時，狀態維持「營運門檻待確認」",
], 13.5, MUTED)

# ── 8 · Grafana presentation ────────────────────────────────────────────────
slide = base_slide("Grafana 主管版呈現：write-gate 強制的白話設計", "plain-language dashboard")
rows = [
    ("1 · 決策摘要", "模型驗證狀態 / 營運狀態分離顯示；每 1,000 筆判對判錯；比舊方法少錯多少；下一步建議", CYAN),
    ("2 · 分析過程", "六步白話流程（資料檢查→語意選欄→保留未見資料→比較設定→最後驗證）", GREEN),
    ("3 · 實際結果", "混淆矩陣、ROC/PR、門檻方案比較——每張圖附白話 caption 與 alt text", ORANGE),
    ("4 · 泛化與工程細節", "護欄健康卡、trial history、重要因素（非因果）、技術細節 <details> 收合", PURPLE),
]
y = 1.55
for title, body, color in rows:
    rect(slide, 0.55, y, 12.2, 0.86, fill=PANEL, line=color)
    add_text(slide, 0.75, y + 0.1, 3.0, 0.3, title, 15, color, True)
    add_text(slide, 3.8, y + 0.1, 8.8, 0.68, body, 12.5, MUTED)
    y += 0.96
node(slide, 0.55, 5.5, 6.0, 1.4, "白話翻譯規則（write-gate 強制）", "", CYAN)
add_lines(slide, 0.7, 5.92, 5.7, 0.95, [
    "· 每個百分比 → 每 1,000 筆實際次數",
    "· 必有：分析目的 / 結論 / 資料分布圖 / 收合技術細節",
    "· 模型驗證與營運使用是兩個獨立狀態",
], 12, MUTED)
node(slide, 6.85, 5.5, 5.9, 1.4, "安全邊界", "", RED)
add_lines(slide, 7.0, 5.92, 5.6, 0.95, [
    "· 只能 image/text panels；圖走 opaque bindings",
    "· raw rows / 實體路徑 / signed URL 拒收",
    "· 任一 ML-tag dashboard 不合最低標準即拒寫",
], 12, MUTED)

# ── 9 · Measured results ────────────────────────────────────────────────────
slide = base_slide("實測結果：兩個 HuggingFace 資料集", "measured results")
headers = ["資料集", "基準", "治理後調參模型", "每 1,000 換算", "護欄"]
widths = [2.6, 2.2, 2.6, 2.9, 1.9]
x = 0.55
for header, width in zip(headers, widths, strict=True):
    rect(slide, x, 1.55, width - 0.06, 0.45, fill=PANEL_2, line=LINE)
    add_text(slide, x + 0.08, 1.6, width - 0.2, 0.3, header, 12.5, CYAN, True)
    x += width
rows_data = [
    ("Adult（UCI，32,561）", "多數類 75.9%", "Acc 0.852 / ROC 0.923", "852 對 / 148 錯", "accepted"),
    ("Telco（4,225）無成本申報", "全留存 73.5%", "Acc 0.859 / ROC 0.920", "859 對 / 141 錯", "accepted"),
    ("Telco（4,225）漏判=3×誤攔", "全留存 73.5%", "Acc 0.819 / 門檻 0.301", "漏判 42.6（原 91.7）", "accepted"),
    ("Telco 含洩漏欄（對照組）", "全留存 73.5%", "Acc 0.967 ← 灌水", "治理閘門現已擋下", "blocked"),
]
y = 2.08
for row in rows_data:
    x = 0.55
    color = RED if "灌水" in row[2] or "blocked" in row[4] else GREEN
    for value, width in zip(row, widths, strict=True):
        rect(slide, x, y, width - 0.06, 0.62, fill=PANEL, line=LINE)
        add_text(slide, x + 0.08, y + 0.14, width - 0.2, 0.4, value, 11.5, WHITE if color != RED else RED, color == GREEN or color == RED)
        x += width
    y += 0.7
add_lines(slide, 0.55, y + 0.1, 12.2, 1.6, [
    "· Adult 對照公開範圍（0.78–0.85 / AUC 0.83–0.86）：位於上緣；Telco PR-AUC 0.825 高於文獻常見 0.60–0.68（資料集變體較豐富，非同賽道正式排名）",
    "· 治理前後對比：同一 Telco 資料集，含洩漏欄 0.967 → gate 後 0.859——分數變保守但可信；洩漏灌水現在由 deterministic gate 擋下，不依賴 LLM 判斷",
    "· 成本閾值實測：漏判成本 3× 時，漏判 91.7 → 42.6/千（−54%），加權總成本 334 → 272",
], 13, MUTED)

# ── 10 · Principles & roadmap ───────────────────────────────────────────────
slide = base_slide("設計原則與後續藍圖", "principles & roadmap")
node(slide, 0.55, 1.55, 5.9, 2.5, "已驗證的設計原則", "", CYAN)
add_lines(slide, 0.7, 2.0, 5.6, 2.0, [
    "· Advisory skill 引導判斷；deterministic gate 負責擋——兩層分離",
    "· 生成碼換成可信模板後，執行期失敗整類消失",
    "· 治理必須在 plan 前攔截（事後修訂不可靠）",
    "· 每個百分比翻譯成每 1,000 筆，主管才會用",
    "· 分數要配護欄才可信：gap/stability/PSI/objective",
], 12.5, MUTED)
node(slide, 6.85, 1.55, 5.9, 2.5, "藍圖（優先序）", "", ORANGE)
add_lines(slide, 7.0, 2.0, 5.6, 2.0, [
    "· Candidate 明確 promotion artifact + 語意內容 hash",
    "· 三層驗證收斂為 shared verifier",
    "· Sealed holdout + 評估預算（防反覆適應）",
    "· Optuna TPE / F relation-path 隱藏欄位",
    "· MCP tools/listChanged 自動刷新 · 敏感欄位核准流程",
], 12.5, MUTED)
node(slide, 0.55, 4.4, 12.2, 1.9, "一句話總結", "", GREEN)
add_lines(slide, 0.7, 4.9, 11.9, 1.3, [
    "這個平台把「跑一個模型」升級成「產出可信、可解釋、可被主管拍板的分析」：",
    "語意層決定什麼資料可以進模型，結構化執行決定模型怎麼跑，",
    "泛化護欄決定分數可不可信，成本閾值決定怎麼用，write-gate 決定主管看到的東西是否可讀且誠實。",
], 14, MUTED)

prs.save(OUT)
print(f"saved {OUT} · {len(prs.slides)} slides")
