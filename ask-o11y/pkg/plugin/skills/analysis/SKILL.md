---
name: analysis
description: Advisory guidance for understanding data, choosing an analysis, and explaining real results. Not a workflow or an execution gate.
---

# Analysis guidance

Apply only what the question needs. Descriptive summaries, statistical comparisons and predictive models are different tasks; do not force every question into ML. Choose target, inputs, preprocessing, split, sampling, metrics and plots yourself using observed schema and the business question. Ask the user about consequential business ambiguity, not routine technical choices.

Check units, missingness, ranges, duplicated observations, dates and group identifiers where relevant. A numeric identifier is not automatically a continuous measurement. Preserve original data and report consequential exclusions, transformations and sample sizes. No automatic ontology, profile or plan contract certifies your choices.

When estimating predictive performance, choose an appropriate time/group/stratified/random split based on how observations relate and how predictions will be used. Fit imputation, scaling, feature selection and tuning only on training data or training folds. Keep the final holdout out of selection. Compare with a simple baseline where useful; explain sample-size limits, instability, leakage risks and generalization limits. Poor scores or no improvement are valid conclusions, not reasons to invent success or keep trying until a score looks good. Do not infer causation from correlation or manufacture business thresholds/costs.

Use the Sandbox's installed pandas, NumPy, SciPy, scikit-learn, statsmodels and Plotly as appropriate. Inspect tool results for available capabilities rather than assuming any particular model/package exists. Emit original Plotly figures with emit(fig); keep titles, axes, units, legends and text legible. Reuse the returned opaque execution references for Dashboard binding rather than copying figure arrays into model context. Explain the answer and its limitations in everyday language; do not require a fixed number of charts, sections, inspections or fact citations.
