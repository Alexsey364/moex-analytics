"""Trading-calendar helpers based on observed MOEX sessions."""

from __future__ import annotations

from datetime import date

import duckdb


def rebuild_calendar(con: duckdb.DuckDBPyConnection, market: str = "stock") -> int:
    con.execute("DELETE FROM trading_calendar WHERE market=?", [market])
    con.execute(
        """INSERT INTO trading_calendar
           SELECT DISTINCT trade_date,?,true,'main','canonical_daily_prices',
                  current_timestamp FROM canonical_daily_prices""",
        [market],
    )
    return con.execute("SELECT count(*) FROM trading_calendar WHERE market=?", [market]).fetchone()[0]


def next_trading_day(con: duckdb.DuckDBPyConnection, value: date, market: str = "stock") -> date | None:
    return con.execute(
        """SELECT min(trade_date) FROM trading_calendar
           WHERE market=? AND is_trading_day AND trade_date>?""",
        [market, value],
    ).fetchone()[0]


def previous_trading_day(con: duckdb.DuckDBPyConnection, value: date, market: str = "stock") -> date | None:
    return con.execute(
        """SELECT max(trade_date) FROM trading_calendar
           WHERE market=? AND is_trading_day AND trade_date<?""",
        [market, value],
    ).fetchone()[0]


def shift_trading_days(
    con: duckdb.DuckDBPyConnection, value: date, offset: int, market: str = "stock"
) -> date | None:
    if offset == 0:
        return value
    operator, order = (">", "ASC") if offset > 0 else ("<", "DESC")
    rows = con.execute(
        f"""SELECT trade_date FROM trading_calendar
            WHERE market=? AND is_trading_day AND trade_date {operator} ?
            ORDER BY trade_date {order} LIMIT ?""",
        [market, value, abs(offset)],
    ).fetchall()
    return rows[-1][0] if len(rows) == abs(offset) else None


def trading_days_between(
    con: duckdb.DuckDBPyConnection, start: date, end: date, market: str = "stock"
) -> int:
    return con.execute(
        """SELECT count(*) FROM trading_calendar WHERE market=? AND is_trading_day
           AND trade_date>? AND trade_date<=?""",
        [market, start, end],
    ).fetchone()[0]
