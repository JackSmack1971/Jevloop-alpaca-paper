# Evaluation methodology

Load this for calibration, threshold tuning, performance claims, or strategy comparisons.

## Probability quality

- Evaluate the probability vector from the exact answer under study; never substitute another answer's confidence.
- Form labels strictly after the decision timestamp with a declared horizon and neutral return band.
  `requested_horizon_seconds` is the intended interval, while
  `actual_label_lag_seconds` is the observed forecast-to-label interval. A label is
  eligible at the horizon through the inclusive `max_label_lag_seconds` boundary;
  later labels are excluded rather than silently changing the evaluated horizon.
- Report proper scoring rules (multiclass Brier and log loss) alongside discrimination/accuracy; no one number establishes useful uncertainty.
- Compare against a declared reference. This package reports both uniform and **sample-climatology** Brier references and uses a difference improvement, while labeling the same-sample climatology baseline as descriptive.
- Retain top-label/equal-width ECE only as a diagnostic. Its result is sensitive to binning and top-label reduction; also report adaptive, classwise reliability using all class probabilities.
- Preserve serial dependence when estimating uncertainty. The command uses a moving-block bootstrap and reports sensitivity across multiple block lengths; a single block choice is not treated as ground truth.
- Report class counts/support. The output is always `DESCRIPTIVE_ONLY`; small or sparse classes add warnings rather than being converted into an arbitrary pass/fail readiness threshold.
- Calibration consumes only complete schema-v3 evidence. Forecast and label must have
  the same symbol and the same declared run, data source, provider route, provider
  model, strategy configuration, limits, and battery-schema identity. Multiple
  forecast cohorts are rejected by default; use the CLI cohort selectors to request
  one explicitly instead of pooling them.
- Real labels must be `dry-real` or `paper-real` observations from the approved
  `alpaca-market-data` pricing source. Their quote-provider timestamp must be present,
  finite, no later than the log timestamp, and no older than
  `--max-provider-age-seconds`. A fresh trade timestamp does not repair a stale quote.
- Simulation and mock rows are excluded both as forecasts and labels in the default
  real analysis. `--simulation-only` creates a separate simulation analysis; it never
  permits simulation-to-real or real-to-simulation pairing.
- Reports expose stable exclusion counters: `legacy_schema`, `source_mismatch`,
  `cohort_mismatch`, `stale_label`, `excessive_lag`, and `missing_timestamps`.
  These diagnose eligibility only: they do not establish representativeness, absence
  of selection bias, or validity of economic conclusions.

## Strategy/performance claims

Probability calibration is not economic edge. Any strategy claim should, at minimum:

1. preserve chronology and prevent label/feature leakage;
2. separate model/threshold selection from the final evaluation sample;
3. use purging/embargo or comparably justified time-series validation when overlapping labels/selection make ordinary IID CV invalid;
4. include spread, fees, slippage, turnover, latency, rejected/partial orders, and realistic executable sizing;
5. report the number of strategy/model/parameter trials and control selection/multiple-testing bias (for example DSR/PBO where appropriate);
6. compare against simple baselines and report negative results;
7. evaluate across regimes/assets/horizons before generalizing;
8. keep statistical evidence and economic materiality as separate gates.

A paper fill model, mock Jev output, or in-sample threshold search is never promoted to real-provider/live evidence.

## Research basis

- Murphy (1973/1974) motivates evaluating Brier/probability forecasts relative to sample-frequency/climatological reference behavior and separating reliability/resolution/uncertainty rather than reading a raw score in isolation.
- Nixon et al. (2019), *Measuring Calibration in Deep Learning*, documents substantial sensitivity and failure modes in common ECE formulations, motivating the additional classwise/adaptive view.
- Arian, Norouzi Mobarekeh & Seco (2024), *Knowledge-Based Systems* 305, compares financial out-of-sample methods under non-stationarity/autocorrelation/regime shifts and reports lower overfitting risk for CPCV in its controlled study.
- Bailey & López de Prado's Deflated Sharpe Ratio work motivates accounting for selection bias, non-normality, and the number of trials rather than reporting the best observed Sharpe as if it were a single preregistered test.

See `references/research-notes.md` for citations and product-documentation provenance.
