# Data methodology

daily_prices preserves each downloaded board row. instrument_history_segments stores
confirmed links. canonical_daily_prices selects one row per security and date while
retaining source SECID and board.

Higher priority wins. Current primary boards use 100; former main boards use lower
values. Volumes are never summed. Sessions and special modes are not mixed.
Overlaps create canonical_board_conflict issues even when a deterministic row is
selected. Boundary jumps and scale changes require manual review.

The calendar comes from observed canonical MOEX dates, not weekdays. Returns are
stored separately from prices. Missing prices produce missing returns. Actual
dividends are descriptive; future backtests need point-in-time announcement data.

Limitations: histories start on different dates, boards overlap, registry dates may
be non-trading days, and ISS dividends lack declaration and payment dates.
