# Portfolio Research Platform

Stage 13 adds an instrument-agnostic research layer without changing the SBER Production Decision Engine.

## Architecture

`portfolio_instruments` is the canonical registry. Identity, lifecycle, historical-board and source-availability tables preserve point-in-time provenance. Adapter protocols isolate prices, fundamentals, dividends, valuation, events and derivatives. `ResearchPipeline` and `DecisionEvidenceProvider` are contracts; they do not grant production status.

Nine user instruments are configured in Git. SBER is a control. TATN, LSNG and IMOEX are research-only comparison series. Private quantities and prices live only in ignored `config/portfolio_positions.local.yaml`.

Price history is downloaded per discovered historical board, canonicalized to one row per date, then converted to total returns. Preferred-share rules remain issuer-specific. X5/FIVE is not spliced: the lifecycle record requires a verified conversion ratio and legal evidence.

Alpha calculations use purged walk-forward folds and are always `research_only`; current candidate labels are screening results, not trading recommendations or production promotion. Cross-instrument labels describe sign/stability screening only. The fundamental source matrix is an availability contract, not a claim of complete historical fundamentals.

Portfolio analytics includes volatility, downside volatility, drawdown, covariance/risk contributions and experimental equal-weight, inverse-volatility, risk-parity, HRP, minimum-variance and maximum-diversification allocations. Scenarios are historical/economic sensitivities with low-to-medium confidence, never forecasts. Lot rounding, costs and constraints are explicit.

Immutable live shadow rows are keyed by instrument/date/model version and store an input hash. New instruments always remain `research_only`, `no_trade_recommendation`, with production Decision Engine restricted to SBER.

## Verified 2026-08-07

- 13 registry rows: nine holdings, one SBER control, three comparison series.
- 55,913 canonical price rows and 61,078 return rows.
- 683 instrument-alpha screening rows and 54 cross-factor rows.
- 13 immutable shadow rows; idempotent rerun inserted zero.
- 203 tests passed; portfolio-research coverage 98%; Ruff passed.

The one-share local configuration used for smoke testing is intentionally ignored and must be replaced locally with real user positions.
