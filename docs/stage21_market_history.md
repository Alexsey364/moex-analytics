# Stage 21 — MOEX market history

## Source and semantics

Daily trading rows come from the official MOEX ISS `history` endpoints. Raw responses are
content-addressed by SHA-256. `market_history_jobs` and `market_history_requests` provide restart,
page-level audit, retry state and exact source URLs. A row in the breadth universe means that the
security actually has a trading record on that date; it does **not** mean index membership.

Only catalog records classified as `common_share` or `preferred_share`, with a Russian registration
number or RU ISIN, are eligible. Bonds, funds, ETFs and technical instruments are excluded. When a
security has several boards, the analytical chain selects the board with the greatest observed total
turnover and records every excluded board and reason in `equity_board_history`.

## Official market series

IMOEX, RTSI, RVI, RUSFAR, RGBI, maturity and sector indices are stored as independent official MOEX
series. MOEX CNY/RUB is an exchange-traded close. CBR USD/RUB, EUR/RUB and CNY/RUB are official
fixings and remain separate. The configured `USDRUB_TOM` history endpoint currently returns no rows;
the absence is retained and is not silently replaced by the CBR fixing.

## Point-in-time rules

Breadth and liquidity use only values from the same or earlier session. Market-state rolling
standardization excludes the current session (`... 1 PRECEDING`). These features are research inputs;
they do not alter the SBER production Decision Engine. Full history discovery/backfill is available
only through explicit Stage 21 CLI commands and is not run by quick daily update or normal dashboard
startup.

## Commands

```text
moex-analytics seed-market-history-jobs [--limit N]
moex-analytics backfill-market-history [--jobs N] [--pages-per-job N]
moex-analytics build-trading-statistics
moex-analytics backfill-official-market-series
moex-analytics market-history-status
```
