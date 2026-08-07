# External analytics comparison

## Equal-data comparison policy

The comparison universe is X5, SBERP, LKOH, LSNGP, MTSS, TRNFP, TATNP, PHOR and MOEX. External programs were never given the primary DuckDB or local position file. Public adjusted/total-return series may be exported in a future locked fixture; this stage compares formulas through synthetic/unit fixtures and the local common-date panel.

The local engine implements total return, annualized volatility, downside volatility, drawdown, correlations, marginal/component risk, inverse-volatility, risk parity, HRP, minimum variance, maximum diversification, transaction costs and lot rounding. okama reproduced the corresponding risk/frontier behavior in 407 passing tests. No meaningful numerical superiority exists for deterministic metrics when dates, returns and conventions match; discrepancies are definition/data-alignment questions.

No external forecasting result passed all gates: temporal validation, purge/no leakage, identical sample, baseline, and OOS. Therefore the column “better than our system” is `not established`, not false precision.

Useful next components: optional okama comparator, provider/provenance contracts inspired by OpenBB and russian-markets-lab, and a separately validated event-driven backtest adapter. Weak/rejected components: synthetic fallback, published ensemble scores without reproduction, and archived/copy-restricted implementations.

Current limitation: user weights were deliberately excluded from Git, so portfolio-level numerical risk and optimal weights are produced only from `config/portfolio_positions.local.yaml`. This is a privacy safeguard, not missing analytics.
