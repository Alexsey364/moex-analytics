"""Short-lived, parameterized read access to DuckDB."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from ..database import database_path


class DatabaseUnavailable(RuntimeError):
    pass


@contextmanager
def read_connection(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    target = path or database_path()
    if not target.exists():
        raise DatabaseUnavailable(f"База данных не найдена: {target}")
    try:
        con = duckdb.connect(str(target), read_only=True)
    except duckdb.Error as exc:
        raise DatabaseUnavailable(f"Не удалось открыть базу: {exc}") from exc
    try:
        yield con
    finally:
        con.close()


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [name]).fetchone()[0]
    )


@st.cache_data(ttl=60, show_spinner=False)
def database_summary(path: Path | None = None) -> dict:
    with read_connection(path) as con:
        required = ["instruments", "canonical_daily_prices", "dividends", "data_quality_issues", "load_log"]
        if not all(table_exists(con, name) for name in required):
            return {"ready": False, "instruments": 0, "canonical_rows": 0}
        row = con.execute(
            """SELECT
               (SELECT count(*) FROM instruments),
               (SELECT count(*) FROM canonical_daily_prices),
               (SELECT min(trade_date) FROM canonical_daily_prices),
               (SELECT max(trade_date) FROM canonical_daily_prices),
               (SELECT count(*) FROM dividends),
               (SELECT count(*) FROM data_quality_issues),
               (SELECT max(finished_at) FROM load_log)"""
        ).fetchone()
        keys = ("instruments", "canonical_rows", "date_from", "date_to", "dividends", "issues", "last_load")
        return {"ready": True, **dict(zip(keys, row, strict=True))}


@st.cache_data(ttl=60, show_spinner=False)
def current_quality_summary(path: Path | None = None) -> dict:
    with read_connection(path) as con:
        if not table_exists(con, "current_quality_runs"):
            return {"status": "unavailable", "critical": 0, "warnings": 0}
        row = con.execute("SELECT as_of,expected_market_date,status,critical,warnings FROM "
                          "current_quality_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        fresh = con.execute("SELECT count(*) FILTER(WHERE status='fresh'),count(*) FROM "
                            "dataset_freshness_current WHERE run_id=(SELECT run_id FROM "
                            "current_quality_runs ORDER BY created_at DESC LIMIT 1)").fetchone()
        portfolio = con.execute("SELECT count(*) FILTER(WHERE price_data='fresh'),count(*) FROM "
                                "portfolio_quality_current WHERE run_id=(SELECT run_id FROM "
                                "current_quality_runs ORDER BY created_at DESC LIMIT 1)").fetchone()
        return {"today": row[0], "market_cutoff": row[1], "status": row[2], "critical": row[3],
                "warnings": row[4], "fresh_families": fresh[0], "families": fresh[1],
                "fresh_portfolio": portfolio[0], "portfolio": portfolio[1]}


@st.cache_data(ttl=60, show_spinner=False)
def instrument_summary(path: Path | None = None) -> pd.DataFrame:
    with read_connection(path) as con:
        if not table_exists(con, "canonical_daily_prices"):
            return pd.DataFrame()
        return con.execute(
            """WITH ranked AS (
                 SELECT *,row_number() OVER(PARTITION BY canonical_secid ORDER BY trade_date DESC) rn
                 FROM canonical_daily_prices
               ), stats AS (
                 SELECT canonical_secid,min(trade_date) first_date,max(trade_date) last_date,
                        count(*) trading_days,max(CASE WHEN rn=1 THEN close END) last_price,
                        max(CASE WHEN rn=2 THEN close END) prev_price,
                        max(CASE WHEN rn=21 THEN close END) price_20
                 FROM ranked GROUP BY canonical_secid
               )
               SELECT s.canonical_secid ticker,i.name,s.first_date,s.last_date,s.trading_days,
                      s.last_price,s.last_price/s.prev_price-1 change_1d,
                      s.last_price/s.price_20-1 change_20d,
                      (SELECT count(*) FROM dividends d WHERE d.canonical_secid=s.canonical_secid) dividends,
                      (SELECT count(*) FROM data_quality_issues q WHERE q.secid=s.canonical_secid) issues
               FROM stats s LEFT JOIN instruments i ON i.secid=s.canonical_secid
               ORDER BY ticker"""
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def prices(
    secid: str, start: date | None = None, end: date | None = None, path: Path | None = None
) -> pd.DataFrame:
    clauses, params = ["canonical_secid=?"], [secid]
    if start:
        clauses.append("trade_date>=?")
        params.append(start)
    if end:
        clauses.append("trade_date<=?")
        params.append(end)
    with read_connection(path) as con:
        if not table_exists(con, "canonical_daily_prices"):
            return pd.DataFrame()
        return con.execute(
            f"""SELECT trade_date,open,high,low,close,volume,board
                FROM canonical_daily_prices WHERE {" AND ".join(clauses)}
                ORDER BY trade_date""",
            params,
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def returns(secid: str, start: date | None = None, path: Path | None = None) -> pd.DataFrame:
    with read_connection(path) as con:
        if not table_exists(con, "daily_returns"):
            return pd.DataFrame()
        return con.execute(
            """SELECT trade_date,price_return,total_return,total_return_index
               FROM daily_returns WHERE canonical_secid=?
               AND (? IS NULL OR trade_date>=?) ORDER BY trade_date""",
            [secid, start, start],
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def dividends(secid: str, path: Path | None = None) -> pd.DataFrame:
    with read_connection(path) as con:
        if not table_exists(con, "dividends"):
            return pd.DataFrame()
        return con.execute(
            """SELECT registry_close_date,dividend_per_share,currency,source,notes
               FROM dividends WHERE canonical_secid=? ORDER BY registry_close_date DESC""",
            [secid],
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def segments(secid: str, path: Path | None = None) -> pd.DataFrame:
    with read_connection(path) as con:
        return con.execute(
            """SELECT board,date_from,date_to,priority,is_primary,notes
               FROM instrument_history_segments WHERE canonical_secid=?
               ORDER BY date_from""",
            [secid],
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def quality_issues(
    secid: str | None = None,
    issue_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    limit: int = 2000,
) -> pd.DataFrame:
    with read_connection(path) as con:
        if not table_exists(con, "data_quality_issues"):
            return pd.DataFrame()
        return con.execute(
            """SELECT trade_date,secid,issue_type,description,detected_at
               FROM data_quality_issues WHERE (? IS NULL OR secid=?)
               AND (? IS NULL OR issue_type=?)
               AND (? IS NULL OR trade_date>=?) AND (? IS NULL OR trade_date<=?)
               ORDER BY detected_at DESC LIMIT ?""",
            [secid, secid, issue_type, issue_type, start, start, end, end, limit],
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def board_conflicts(secid: str, path: Path | None = None) -> pd.DataFrame:
    with read_connection(path) as con:
        return con.execute(
            """SELECT p.trade_date,p.secid,p.board,p.close,p.volume,s.priority,
                      c.board= p.board selected
               FROM daily_prices p JOIN instrument_history_segments s
                 ON p.secid=s.source_secid AND p.board=s.board
               JOIN canonical_daily_prices c ON c.trade_date=p.trade_date
                 AND c.canonical_secid=s.canonical_secid
               WHERE s.canonical_secid=? AND EXISTS (
                 SELECT 1 FROM daily_prices p2 JOIN instrument_history_segments s2
                   ON p2.secid=s2.source_secid AND p2.board=s2.board
                 WHERE p2.trade_date=p.trade_date AND s2.canonical_secid=s.canonical_secid
                   AND p2.board<>p.board)
               ORDER BY p.trade_date DESC,p.board""",
            [secid],
        ).df()


@st.cache_data(ttl=60, show_spinner=False)
def database_tables(path: Path | None = None) -> pd.DataFrame:
    with read_connection(path) as con:
        names = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
        return pd.DataFrame(
            [
                {"table": name, "rows": con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]}
                for name in names
            ]
        )
