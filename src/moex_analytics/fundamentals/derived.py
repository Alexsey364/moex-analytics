"""Derived point-in-time annual metrics and market multiples."""

from __future__ import annotations

import duckdb

from . import CALCULATION_VERSION

SHARES = 21_586_948_000.0


def build(con: duckdb.DuckDBPyConnection, version: str = CALCULATION_VERSION) -> dict:
    con.execute("DELETE FROM fundamental_features WHERE secid='SBER' AND calculation_version=?", [version])
    releases = con.execute(
        """SELECT period_end,max(publication_date) publication_date,
        max(normalized_value) FILTER(metric_id='net_profit') net_profit,
        max(normalized_value) FILTER(metric_id='total_equity') equity
        FROM fundamental_metric_values WHERE quality_status='validated' AND accounting_standard='RAS'
        GROUP BY period_end HAVING net_profit IS NOT NULL AND equity IS NOT NULL ORDER BY period_end"""
    ).fetchall()
    written = 0
    for period_end, published, profit, equity in releases:
        trade = con.execute(
            """SELECT min(trade_date) FROM canonical_daily_prices
            WHERE canonical_secid='SBER' AND trade_date>=?""",
            [published],
        ).fetchone()[0]
        if not trade:
            continue
        price = con.execute(
            "SELECT close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND trade_date=?", [trade]
        ).fetchone()[0]
        values = {
            "net_profit_annual": (profit, "RUB"),
            "eps": (profit / SHARES, "RUB/share"),
            "bvps": (equity / SHARES, "RUB/share"),
            "roe": (profit / equity, "ratio"),
            "pe": (price / (profit / SHARES), "multiple"),
            "pb": (price / (equity / SHARES), "multiple"),
            "earnings_yield": ((profit / SHARES) / price, "ratio"),
        }
        for metric, (value, unit) in values.items():
            con.execute(
                """INSERT INTO fundamental_features VALUES
                (?,'SBER',?,?,?,?,?,'CBR validated RAS + MOEX ISS shares',?,current_timestamp)""",
                [trade, metric, value, unit, period_end, published, version],
            )
            written += 1
    return {"releases": len(releases), "rows": written}


def current_facts(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        """SELECT report_period_end,publication_date,
        max(value) FILTER(metric_id='net_profit_annual'),max(value) FILTER(metric_id='eps'),
        max(value) FILTER(metric_id='bvps'),max(value) FILTER(metric_id='roe')
        FROM fundamental_features WHERE secid='SBER'
        GROUP BY report_period_end,publication_date ORDER BY report_period_end DESC LIMIT 1"""
    ).fetchone()
    if not row:
        return {}
    return dict(zip(("period_end", "publication_date", "net_profit", "eps", "bvps", "roe"), row, strict=True))
