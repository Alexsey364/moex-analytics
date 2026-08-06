# Alpha Research Engine

Alpha Research Engine is an isolated research system for SBER factors. It does not write to, import, or call the production Decision Engine and does not promote a production model.

## Methodology

The registry inventories actual point-in-time feature-store fields and explicit unavailable paid-data placeholders. Importance is evaluated in expanding walk-forward folds with horizon-sized purge and embargo, train-only missing-value handling, robust scaling and correlation filtering. Reported diagnostics include Pearson IC, Rank IC, histogram mutual information, exact linear-model SHAP contributions, permutation importance, effect sign, regime sign changes, approximate p-values and moving-block bootstrap intervals.

Regimes are discovered from returns, volatility, momentum, drawdown and liquidity without predefined labels. NumPy implementations of Hidden Markov state decoding, diagonal Gaussian mixture, KMeans and spectral clustering test k=2..8. Only the most stable k per algorithm receives `selected=true`; daily assignments and transition matrices remain reproducible.

Interactions are lagged and rolling-centered before evaluation. Alpha decay is measured at 1, 3, 5, 10, 20, 40, 60, 120 and 250 sessions. Stability combines years/folds/regimes, sign consistency and coefficient/importance variation into a transparent 0–100 score. Factor Library classifications are research labels only: Production Candidate, Experimental, Rejected, Insufficient Sample and Requires Paid Data.

Market State emits explainable daily scores for trend, volatility, liquidity, breadth, rates, credit, risk appetite, momentum, mean reversion and rotation. Explanations list positive, negative and neutral contributors and are explicitly not forecasts.

## Commands

- `build-feature-registry`
- `calculate-feature-importance`
- `discover-market-regimes`
- `calculate-alpha-decay`
- `evaluate-feature-stability`
- `build-factor-library`
- `update-market-state`
- `research-status`
- `run-alpha-research`

The full workflow is idempotent for a fixed version and records every step in `alpha_research_journal`.

## Limitations

The engine uses simple research models and linear SHAP, not tree SHAP. P-values are approximate; moving-block bootstrap intervals are preferred. Production Candidate means eligible for further untouched live validation, never automatic production admission. Paid or absent sources are never replaced by synthetic data.