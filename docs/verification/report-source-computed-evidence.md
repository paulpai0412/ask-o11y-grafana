# Trusted computed evidence in report sources

2026-09-08 — main-agent-only, user-approved source/test slice. No new methods, fixed analysis flow, service, dependency, deployment, image rebuild, commit, push or Goal change.

## Root cause and scope

`build_report_source()` in `sandbox-analysis-mcp/ml_presentation.py` previously read only shallow numeric/boolean values from five sections and silently stopped after 32 candidates. Classification's nested `results.baseline`/`results.selected` and regression's `baseline_metrics`/`selected_metrics` were omitted even when already computed by the trusted templates.

The adapter now:

- Preserves numeric/boolean summaries recursively in `results`, `decision`, `guards`, `selected_metrics` and `baseline_metrics`, including indexed interval values.
- Keeps legacy flat fact IDs; new nested/model-metric IDs carry their section/path so baseline and selected values remain distinct. Labels retain the source path.
- Rejects non-finite/unsupported values, malformed sections, normalized/truncated ID collisions, forbidden fields, nesting over eight path components and more than the host contract's 64 facts. It does not silently rename collisions or truncate excess evidence.
- Preserves existing scalar-only handling of `data`/`process`; nested input data and trial logs are not promoted to result evidence. Authored prose and nulls are not numeric facts.
- Leaves the source/manifest format, artifact bindings, model calculations, figure data and generic-Python trust rules unchanged.

No assertion was added that these facts satisfy every promised analysis. Semantic requirement-to-result verification and additional statistical methods remain separate work.

## Verification

New runnable check: `.venv/bin/python scripts/check-ml-report-source-evidence.py`.

- RED against the old adapter: `computed evidence dropped: selected_metrics_mae`.
- GREEN: independently calculated fixture errors, differing baseline/selected metrics, nested scores/intervals and false boolean guards survive source → canonical manifest → fact catalog without value changes or input mutation.
- Negative cases cover non-finite/huge numbers, malformed sections, collisions (including truncation), forbidden fields, depth and fact-count overflow. All 64 valid facts survive, rather than stopping at 32.
- Both generic execute and revise paths reject the genuine adapter's report-source format when submitted by generic Python. Ordinary JSON metric outputs remain unverified; the host source contains only verified input counts.
- The fixture is an offline mechanical test, not a real dataset/model result or a statistical-method validation.

Eight checks passed: new source-evidence regression, presentation manifest, canonical report manifest, report synthesis, Plotly presentation, no-hardcoded-report, Artifact Bridge synthesis, and Sandbox `--self-check`. Primary Python LSP diagnostics and `git diff --check` also passed.

Raw logs: `.scratch/report-source-evidence/` (including `red.log`). Server-loading tests used temporary artifact roots, not the live artifact store.

Final source SHA256: `7374a642a4fdf0a97d1b8a860dac7754c4b0997bf507805172f2b5e41aa76430`.
Regression SHA256: `fac4824d516a1240aa80e73f94145ae38936b17aa9d48451309fe95d6db7296f`.

## Deployment and acceptance limits

The running Sandbox image has not been rebuilt; these source changes are not installed. No live Grafana/browser verification or independent child review was performed. Prior unrelated dirty work and pending review/deployment gates remain intact.
