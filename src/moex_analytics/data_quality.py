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
    con.execute("DELETE FROM data_quality_issues")
    issues = find_issues(con)
    now = datetime.now()
    for issue in issues:
        con.execute(
            """INSERT INTO data_quality_issues
               (secid,trade_date,issue_type,description,detected_at) VALUES (?,?,?,?,?)""",
            [issue["secid"], issue["trade_date"], issue["issue_type"], issue["description"], now],
        )
    return len(issues)
