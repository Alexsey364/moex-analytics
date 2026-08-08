# Visual Investment Assistant and Portfolio Editor

The BASIC dashboard translates stored Portfolio Intelligence evidence into accessible visual statuses.
Colour is always paired with a symbol and text. Statuses are research summaries, not broker orders or
guaranteed BUY/SELL recommendations.

## Conservative overrides

- Rejected alpha cannot produce a green status.
- Missing fundamentals reduce, rather than improve, a status.
- A position weight or historical risk contribution of 30% or more forces red.
- Insufficient data forces gray.
- Confidence is an evidence-quality score, not a probability of price growth.

## Contribution planning

Only green/light-green, liquid, buy-enabled positions with a stored official MOEX lot size are eligible.
The research planner caps immediate deployment at 30% of a contribution and rounds down to whole lots.
The remainder stays explicitly unallocated. This is deliberately conservative and does not optimize or
submit orders.

## Local portfolio persistence

The browser editor validates SECID against the local instrument registry, quantity and average price
before writing. It copies the previous private file into `data/local/portfolio_backups/`, writes a temporary
file, flushes it, and atomically replaces `config/portfolio_positions.local.yaml`. Both paths are ignored by
Git. Recalculation reuses existing market history (`update_data=False`).

Unknown SECIDs are rejected with a prompt to run official MOEX discovery; they are never silently added.
