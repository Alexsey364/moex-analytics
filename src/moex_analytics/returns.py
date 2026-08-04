"""Price and dividend return calculations without changing raw OHLC."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import duckdb

CALCULATION_VERSION = "actual-dividends-v1"


def calculate_rows(
    prices: Sequence[dict[str, Any]], dividends: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Calculate actual-dividend historical returns.

    Dividend cash is applied once on registry close date. This mode is descriptive,
    not point-in-time: ISS does not provide the declaration date.
    """
    dividends = dividends or {}
    result: list[dict[str, Any]] = []
    index = 1.0
    previous: float | None = None
    for row in prices:
        close = row.get("close")
        cash = float(dividends.get(str(row["trade_date"]), 0.0))
        price_return = None if previous in (None, 0) or close is None else close / previous - 1
        log_return = None if previous in (None, 0) or close in (None, 0) else math.log(close / previous)
        dividend_return = None if previous in (None, 0) else cash / previous
        total_return = None if price_return is None else price_return + (dividend_return or 0.0)
        if total_return is not None:
            index *= 1 + total_return
        result.append(
            {
                "trade_date": row["trade_date"],
                "canonical_secid": row["canonical_secid"],
                "price_return": price_return,
                "log_return": log_return,
                "dividend_cash": cash,
                "dividend_return": dividend_return,
                "total_return": total_return,
                "total_return_index": index,
                "calculation_version": CALCULATION_VERSION,
                "calculated_at": datetime.now(),
            }
        )
        if close is not None:
            previous = close
    return result


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("DELETE FROM daily_returns WHERE calculation_version=?", [CALCULATION_VERSION])
    secids = [
        row[0]
        for row in con.execute("SELECT DISTINCT canonical_secid FROM canonical_daily_prices").fetchall()
    ]
    total = 0
    for secid in secids:
        prices = [
            {"trade_date": row[0], "canonical_secid": secid, "close": row[1]}
            for row in con.execute(
                """SELECT trade_date,close FROM canonical_daily_prices
                   WHERE canonical_secid=? ORDER BY trade_date""",
                [secid],
            ).fetchall()
        ]
        dividends = {
            str(row[0]): row[1]
            for row in con.execute(
                """SELECT registry_close_date,dividend_per_share FROM dividends
               WHERE canonical_secid=?""",
                [secid],
            ).fetchall()
        }
        for row in calculate_rows(prices, dividends):
            con.execute(
                """INSERT INTO daily_returns VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    row[key]
                    for key in (
                        "trade_date",
                        "canonical_secid",
                        "price_return",
                        "log_return",
                        "dividend_cash",
                        "dividend_return",
                        "total_return",
                        "total_return_index",
                        "calculation_version",
                        "calculated_at",
                    )
                ],
            )
            total += 1
    return total
