# ML process and result presentation in Grafana

Status: proposal for discussion

Scope: Ask O11y analysis Result Preview → Grafana Preview → explicit publication

Related documents: [`sandbox-analysis-mcp.md`](./sandbox-analysis-mcp.md), [`ontology-ml-accuracy.md`](./ontology-ml-accuracy.md), [ADR 0001](../adr/0001-grafana-executes-datasource-queries.md).

## Goal

Make one ML run understandable at three levels:

1. **Decision** — Is the model acceptable, what improved, and what action is supported?
2. **Evidence** — How well does it perform, generalize, calibrate, and explain predictions?
3. **Process/provenance** — Which data, ontology snapshot, preprocessing, split, search budget, and parameters produced the result?

The dashboard is an immutable presentation of one analyzed run. It is not a model-training UI and never exposes raw frames, credentials, generated code, or physical artifact paths.

## Existing boundaries retained

- The same Ask O11y LLM selects the dashboarding Skill and authors complete Dashboard JSON.
- Sandbox emits bounded JSON summaries and PNG assets only.
- Hidden Artifact Bridge only resolves opaque asset bindings; it does not choose panels or generate dashboard JSON.
- Analysis dashboards use image/text panels. Native datasource targets are allowed only when Sandbox was not called.
- First write is a real `ask-o11y-preview` dashboard. Publication patches the same UID after explicit confirmation and does not rerun query, analysis, or model selection.

No new renderer service or fixed dashboard generator is introduced.

## Presentation contract

Sandbox emits one bounded `ml-presentation.json` plus optional PNG assets. The host validates it before exposing the summary to the planner.

```text
MLPresentationManifest
├─ identity
│  ├─ run_id, dataset_id, dataset_version
│  ├─ ontology_snapshot_id, ontology_sha256, contract_sha256
│  └─ created_at, image_digest, seed
├─ objective
│  ├─ target, task_kind, primary_metric
│  ├─ decision_costs / minimum objective
│  └─ positive_class and selected threshold
├─ data
│  ├─ row counts, feature count, excluded fields
│  ├─ split kind, train/CV/holdout sizes
│  └─ missingness / class balance summary
├─ process
│  ├─ execution_template, preprocessing_fit_scope
│  ├─ model families, search budget, completed trials
│  └─ best parameters
├─ results
│  ├─ baseline and selected-model metrics
│  ├─ deltas and uncertainty intervals
│  ├─ confusion matrix / threshold metrics
│  └─ subgroup metrics when allowed
├─ guards
│  ├─ CV-holdout gap, importance stability, PSI
│  ├─ objective checks
│  └─ verdict + rejection reasons
├─ explainability
│  ├─ top ontology properties with direction/effect
│  └─ local examples only when privacy policy permits
├─ artifacts
│  ├─ confusion_matrix.png
│  ├─ roc_pr_curves.png
│  ├─ calibration.png
│  ├─ trial_history.png
│  └─ feature_importance.png / shap_summary.png
└─ limitations[]
```

Bounds: top 5 trials, top 20 features, at most 8 PNG assets, each asset size subject to existing Sandbox limits. Raw rows and full trial histories remain retained opaque artifacts.

## Dashboard information architecture

One dashboard, four collapsible rows. The first viewport answers the decision question without scrolling.

### Row 1 — Decision summary

| Panel | Type | Content |
| --- | --- | --- |
| Model verdict | Text/status | `ACCEPTED`, `OVERFIT`, `UNSTABLE`, `DRIFT`, `BELOW OBJECTIVE`; always show text + color |
| Primary metric | Text/stat-shaped | Selected metric, objective threshold, and pass/fail |
| Baseline delta | Text/stat-shaped | Baseline → selected model and absolute percentage-point change |
| Supporting metrics | Text | PR-AUC, ROC-AUC, recall/precision at selected threshold |
| Recommended action | Text | Deploy candidate / revise objective / collect data / reject |

Status thresholds:

- CV-holdout gap: ≤0.03 green, 0.03–0.05 amber, >0.05 red
- Importance stability: ≥0.75 green, 0.60–0.75 amber, <0.60 red
- PSI: <0.10 green, 0.10–0.25 amber, >0.25 red
- Objective check: declared contract threshold, not a hardcoded dashboard value

### Row 2 — Analysis process

| Panel | Type | Content |
| --- | --- | --- |
| Pipeline | Markdown text | Ontology → projection → split → preprocessing → search → holdout → guards, each stage with pass/fail |
| Data contract | Markdown table | Dataset/version, target, rows, train/CV/holdout, class balance |
| Ontology selection | Markdown table | Approved target, included features, excluded fields + reasons, snapshot hash prefix |
| Autoresearch | Markdown table | Model families, trial budget/completed trials, CV strategy, objective |
| Best configuration | Markdown/code | Selected parameters, seed, execution template |

This row displays verified manifest values only; the LLM may explain them but may not invent missing process facts.

### Row 3 — Predictive performance

| Panel | Type | Content |
| --- | --- | --- |
| Baseline comparison | PNG + caption | Metric comparison with confidence intervals when available |
| Confusion matrix | PNG + caption | Counts and row-normalized rates at selected threshold |
| ROC + PR | PNG + caption | Both curves; PR random baseline equals positive rate |
| Calibration | PNG + caption | Reliability curve and Brier score |
| Threshold/cost curve | PNG + caption | Precision/recall/cost trade-off; required for manufacturing defect/failure tasks |

A metric panel must always state split type and evaluation population. No chart may present CV performance as final holdout performance.

### Row 4 — Generalization, explanation, and limitations

| Panel | Type | Content |
| --- | --- | --- |
| Generalization guards | Markdown table | gap/stability/PSI/objective checks with thresholds and verdict |
| Trial history | PNG + caption | Top trials + convergence; does not expose all raw trials |
| Feature explanation | PNG + caption | Grouped back to ontology property, not one-hot columns |
| Segment/subgroup checks | PNG/table | Machine/lot/time/sensitive subgroup metrics when policy permits |
| Limitations | Markdown | Observational-not-causal, data range, unknown measurement quality, external-validity gaps |
| Provenance | Markdown | Run/contract/snapshot/image hashes, seed, model/library versions |

## Adult first-slice layout

The first acceptance implementation can use the completed Adult run:

- Verdict: `accepted`
- Accuracy: 0.8346 → **0.8701** (+3.55 pp)
- PR-AUC: 0.8250 → 0.8221
- ROC-AUC: 0.9243 → 0.9236
- Guards: gap 0.00374, stability 0.7758, PSI 0.00221
- Ontology: target `income`; 13 features; `fnlwgt` excluded as sampling weight/identifier
- Search: 40 trials × 5 folds; best LightGBM parameters

Required assets for the first slice: baseline comparison, confusion matrix, ROC+PR curves, trial history, grouped importance. Calibration and threshold/cost curve may follow in the next slice because the existing retained result did not emit them.

## Fresh-session acceptance result (2026-08-24)

A clean UI-equivalent session was used: new session → new upload → one natural-language request → Analysis Preview → confirmation → Planner/Grafana Query/Sandbox → Grafana Preview. The user prompt contained no tool names, Dashboard JSON, UID, output index, or binding instructions.

Preview evidence:

- URL: `http://localhost:3000/d/adult-income-ml-preview/d38d7e3`
- UID: `adult-income-ml-preview`
- Stored version: 4
- Tags include `ask-o11y-preview`; the dashboard was not formally published.
- Nine text/image panels, six authorized PNG assets, zero datasource targets.
- Technical details use a collapsed HTML `<details>` block.
- A separate plain-language process panel explains the six analysis stages.

This autonomous run selected a governance-aware feature subset and ROC-oriented tuning, so its result differs from the earlier accuracy-only benchmark:

- holdout rows: 6,513
- accuracy: 0.852
- ROC-AUC: 0.923
- PR-AUC: 0.821
- per 1,000: 852 correct, 148 incorrect, 55 missed positives, 93 false positives
- versus majority baseline: about 93 fewer errors per 1,000 (38.7% error reduction)

The first visual run exposed two defects that were fixed with regression checks: exact upload evidence did not close discovery capabilities, and `plt.style.use('default')` reset the CJK font. The host now adds upload discovery/inspect/ontology/planner capabilities from an exact UI upload id, and Sandbox capture reapplies Noto CJK after every Matplotlib style change.

## Second-dataset acceptance — Telco churn (2026-08-24)

HF `aai510-group1/telco-customer-churn` (train split, 4,225 rows × 39 cols; post-outcome leakage columns Churn Score/Reason/Category, Customer Status, and geo/ID columns removed before upload). Fresh session, natural language only.

The autonomous run excluded `Satisfaction Score` as a suspected post-outcome leakage signal and geo/aggregate fields, tuned on train-only CV, and selected threshold 0.58 on training data.

| Metric | Baseline (all-stay) | Tuned model |
| --- | ---: | ---: |
| Holdout accuracy | 0.735 | **0.822** |
| ROC-AUC | 0.500 | 0.903 |
| PR-AUC | 0.265 | 0.781 |
| Recall (churners caught) | 0% | 76.3% |
| Errors per 1,000 | 265 | **178** |
| Churners caught per 1,000 | 0 | 202 of 265 |

Error reduction: −87.6 per 1,000 (−33% relative). Verdict: 模型驗證通過；營運門檻與成本需確認後再部署.

The runtime ML write-gate was shipped in the same change: any dashboard tagged with an ML tag must pass the minimum plain-language gate (preview tag, decision summary, purpose/conclusion, data-distribution panel, collapsed technical details, opaque bindings) inside Artifact Bridge before dispatch, or the write fails closed. The Telco Preview passed this gate at write time.

## Safety and privacy

- No raw rows, rare individual examples, or physical paths in Dashboard JSON.
- Subgroup panels require minimum group size and sensitive-field approval.
- Asset bindings remain opaque and authorization checked at request time.
- Captions include target definition, split kind, sample size, and uncertainty.
- Color is never the only status indicator; use text/icon + color. PNGs retain Noto CJK fonts and readable contrast.
- Published dashboards remain tied to the exact run/contract/snapshot hashes shown in Preview.

## Failure behavior

- Missing mandatory manifest facts → Result Preview only; no Grafana Preview.
- Missing optional PNG → omit that panel and state the missing evidence; never fabricate a placeholder result.
- Verdict other than `accepted` → dashboard may still be created for diagnosis, but the first row must state `NOT APPROVED FOR DEPLOYMENT`.
- Expired or unauthorized asset binding → publication fails closed.

## Acceptance criteria

1. Dashboard first viewport shows verdict, primary metric, baseline delta, and action.
2. Process row reconstructs the exact ontology/split/preprocessing/search path from signed facts.
3. Final holdout metrics are visibly distinguished from CV metrics.
4. All guards display value, threshold, and pass/fail.
5. At least one performance asset and one explanation asset render through opaque bindings.
6. Dashboard contains no raw frame, code, credentials, physical path, or signed URL before Artifact Bridge resolution.
7. Preview/publish uses the same UID and publication does not rerun analysis.
8. The rendered dashboard is readable in Traditional Chinese and does not rely on color alone.

## Approved presentation direction

1. Use **one four-row dashboard**. Non-technical decision content is open by default; Engineering details are collapsed.
2. Manufacturing classification requires a threshold/cost view before operational status can be `READY TO DEPLOY`. Without it, the maximum status is `MODEL VALIDATED — OPERATING DECISION PENDING`.
3. Machine/lot/shift/supplier fields trigger mandatory subgroup performance checks. Person/operator fields additionally require sensitive-field approval and minimum group sizes.
4. Published dashboards freeze their manifest and PNG assets under the published run version. A new model creates a new dashboard version; historical evidence is never silently replaced.

## Plain-language translation rules

Every percentage must be translated into an operational count. The first viewport answers only:

1. Can this model be used yet?
2. How much better or worse is it than the baseline?
3. What happens per 1,000 items?
4. What should the user do next?

Required translations:

| Technical value | First-view wording |
| --- | --- |
| Accuracy 0.8701 | `每 1,000 筆約 870 筆判對、130 筆判錯` |
| Baseline 0.8346 → 0.8701 | `每 1,000 筆比舊模型少錯約 36 筆；錯誤量降低約 21.5%` |
| CV-holdout gap 0.00374 | `換成未見資料，每 1,000 筆的表現差約 4 筆` |
| Importance stability 0.7758 | `換不同資料分組，前十大關鍵因素約八成仍相同` |
| PSI 0.00221 | `本次訓練與測試資料結構非常接近；不代表未來月份不會改變` |
| PR-AUC/ROC-AUC | Hidden under Engineering details unless the caption translates the actual positive-class decision consequence |

Do not place raw ROC-AUC, PR-AUC, SHAP, p-values, hashes, or model hyperparameters in the first viewport. Do not use gauges, which hide the baseline and error composition. Prefer labeled horizontal bars, per-1,000 pictograms, three operating scenarios, and direct annotations.

The verdict is split into two independent statements:

- **Model evidence** — accepted/rejected by holdout and generalization guards.
- **Operational readiness** — ready/pending based on cost, threshold, subgroup, and approval checks.

For the Adult first slice, the first banner is:

```text
模型驗證：通過
營運使用：尚待確認誤判與漏判成本
新資料每 1,000 筆約判對 870 筆、判錯 130 筆
比舊模型每 1,000 筆少錯約 36 筆
```

## Visual priority

Required first-slice visuals, in order:

1. **Per-1,000 outcome bar** — correct vs incorrect, with counts printed on the bars.
2. **Before/after error comparison** — baseline error 16.54% vs selected-model error 12.99%, annotated `少 3.55 percentage points`.
3. **Three operating scenarios** — conservative/balanced/capacity-protecting, showing missed positives, false alarms, and cost. Until real cost assumptions exist, label the panel `營運門檻尚未核准`.
4. **Why the model decides** — top ontology properties with actual value, normal range, risk direction, and magnitude; never raw SHAP values alone.
5. **Generalization health cards** — each card shows a question, actual value, threshold, and one-sentence consequence.
6. Technical ROC/PR, calibration, trials, hashes, and parameters remain in collapsed Engineering details.
