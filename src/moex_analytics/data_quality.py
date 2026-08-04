"""Explicit, non-destructive data-quality checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import duckdb


def find_issues(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    checks = [
        (
            "duplicate",
            "Duplicate unique key",
            """SELECT secid,trade_date FROM daily_prices
         GROUP BY secid,trade_date,board HAVING count(*) > 1""",
        ),
        (
            "negative_price",
            "Negative price",
            """SELECT secid,trade_date FROM daily_prices
         WHERE open<0 OR high<0 OR low<0 OR close<0 OR weighted_average_price<0""",
        ),
        (
            "negative_volume",
            "Negative volume or value",
            """SELECT secid,trade_date FROM daily_prices
         WHERE volume<0 OR value<0""",
        ),
        (
            "high_below_low",
            "High is below low",
            """SELECT secid,trade_date FROM daily_prices
         WHERE high < low""",
        ),
        (
            "open_outside_range",
            "Open is outside low-high",
            """SELECT secid,trade_date
         FROM daily_prices WHERE open IS NOT NULL AND low IS NOT NULL AND high IS NOT NULL
         AND (open < low OR open > high)""",
        ),
        (
            "close_outside_range",
            "Close is outside low-high",
            """SELECT secid,trade_date
         FROM daily_prices WHERE close IS NOT NULL AND low IS NOT NULL AND high IS NOT NULL
         AND (close < low OR close > high)""",
        ),
        (
            "missing_required",
            "Missing required value",
            """SELECT secid,trade_date
         FROM daily_prices WHERE trade_date IS NULL OR secid IS NULL OR board IS NULL
         OR close IS NULL""",
        ),
        (
            "nonpositive_dividend",
            "Dividend is zero or negative",
            """SELECT canonical_secid,registry_close_date FROM dividends
               WHERE dividend_per_share <= 0""",
        ),
        (
            "orphan_dividend",
            "Dividend has no matching instrument",
            """SELECT d.canonical_secid,d.registry_close_date FROM dividends d
               LEFT JOIN instruments i ON d.canonical_secid=i.secid WHERE i.secid IS NULL""",
        ),
        (
            "dividend_outside_history",
            "Dividend date is outside canonical history",
            """SELECT d.canonical_secid,d.registry_close_date FROM dividends d
               WHERE NOT EXISTS (
                 SELECT 1 FROM canonical_daily_prices p
                 WHERE p.canonical_secid=d.canonical_secid
                   AND p.trade_date=d.registry_close_date
               )""",
        ),
        (
            "segment_overlap",
            "Confirmed history segments overlap",
            """SELECT a.canonical_secid,greatest(a.date_from,b.date_from)
               FROM instrument_history_segments a
               JOIN instrument_history_segments b
                 ON a.canonical_secid=b.canonical_secid AND a.id<b.id
                AND a.date_from<=b.date_to AND b.date_from<=a.date_to""",
        ),
        (
            "segment_gap",
            "Calendar gap between confirmed segments; review trading dates",
            """SELECT canonical_secid,next_from FROM (
                 SELECT canonical_secid,date_to,
                        lead(date_from) OVER (PARTITION BY canonical_secid ORDER BY date_from) next_from
                 FROM instrument_history_segments
               ) WHERE next_from > date_to + INTERVAL 4 DAY""",
        ),
        (
            "board_boundary_jump",
            "Price jump above 25% at or near a board boundary; manual review",
            """SELECT canonical_secid,trade_date FROM (
                 SELECT *,lag(close) OVER (PARTITION BY canonical_secid ORDER BY trade_date) prev_close,
                        lag(board) OVER (PARTITION BY canonical_secid ORDER BY trade_date) prev_board
                 FROM canonical_daily_prices
               ) WHERE board<>prev_board AND prev_close>0 AND abs(close/prev_close-1)>0.25""",
        ),
        (
            "possible_scale_change",
            "Price changed by factor above 10; possible scale change",
            """SELECT canonical_secid,trade_date FROM (
                 SELECT *,lag(close) OVER (PARTITION BY canonical_secid ORDER BY trade_date) prev_close
                 FROM canonical_daily_prices
               ) WHERE close>0 AND prev_close>0 AND (close/prev_close>10 OR prev_close/close>10)""",
        ),
        (
            "volume_jump",
            "Volume changed by factor above 100",
            """SELECT canonical_secid,trade_date FROM (
                 SELECT *,lag(volume) OVER (PARTITION BY canonical_secid ORDER BY trade_date) prev_volume
                 FROM canonical_daily_prices
               ) WHERE volume>0 AND prev_volume>0
                 AND (volume/prev_volume>100 OR prev_volume/volume>100)""",
        ),
        (
            "missing_calendar_date",
            "Canonical price missing on an observed market trading date",
            """SELECT bounds.canonical_secid,c.trade_date
               FROM (SELECT canonical_secid,min(trade_date) lo,max(trade_date) hi
                     FROM canonical_daily_prices GROUP BY canonical_secid) bounds
               JOIN trading_calendar c ON c.trade_date BETWEEN bounds.lo AND bounds.hi
               WHERE NOT EXISTS (SELECT 1 FROM canonical_daily_prices p
                 WHERE p.canonical_secid=bounds.canonical_secid AND p.trade_date=c.trade_date)""",
        ),
        (
            "invalid_total_return_index",
            "Total return index is nonpositive or non-finite",
            """SELECT canonical_secid,trade_date FROM daily_returns
               WHERE total_return_index<=0 OR NOT isfinite(total_return_index)""",
        ),
    ]
    issues: list[dict[str, Any]] = []
    for issue_type, description, query in checks:
        for secid, trade_date in con.execute(query).fetchall():
            issues.append(
                {
                    "secid": secid,
                    "trade_date": trade_date,
                    "issue_type": issue_type,
                    "description": description,
                }
            )
    return issues


def record_issues(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("DELETE FROM data_quality_issues WHERE issue_type <> 'canonical_board_conflict'")
    issues = find_issues(con)
    now = datetime.now()
    for issue in issues:
        con.execute(
            """INSERT INTO data_quality_issues
               (secid,trade_date,issue_type,description,detected_at) VALUES (?,?,?,?,?)""",
            [issue["secid"], issue["trade_date"], issue["issue_type"], issue["description"], now],
        )
    return len(issues)
