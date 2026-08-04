"""DuckDB schema and persistence operations."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .config import PROJECT_ROOT, load_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    secid VARCHAR PRIMARY KEY, name VARCHAR, instrument_type VARCHAR,
    engine VARCHAR, market VARCHAR, board VARCHAR, history_from DATE,
    is_active BOOLEAN, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily_prices (
    trade_date DATE, secid VARCHAR, board VARCHAR, open DOUBLE, high DOUBLE,
    low DOUBLE, close DOUBLE, weighted_average_price DOUBLE, volume DOUBLE,
    value DOUBLE, number_of_trades BIGINT, source VARCHAR, loaded_at TIMESTAMP,
    PRIMARY KEY (trade_date, secid, board)
);
CREATE SEQUENCE IF NOT EXISTS load_log_id_seq START 1;
CREATE TABLE IF NOT EXISTS load_log (
    id BIGINT PRIMARY KEY DEFAULT nextval('load_log_id_seq'), secid VARCHAR,
    date_from DATE, date_to DATE, started_at TIMESTAMP, finished_at TIMESTAMP,
    rows_received BIGINT, rows_inserted BIGINT, status VARCHAR, error_message VARCHAR
);
CREATE SEQUENCE IF NOT EXISTS quality_issue_id_seq START 1;
CREATE TABLE IF NOT EXISTS data_quality_issues (
    id BIGINT PRIMARY KEY DEFAULT nextval('quality_issue_id_seq'), secid VARCHAR,
    trade_date DATE, issue_type VARCHAR, description VARCHAR, detected_at TIMESTAMP
);
CREATE SEQUENCE IF NOT EXISTS segment_id_seq START 1;
CREATE TABLE IF NOT EXISTS instrument_history_segments (
    id BIGINT PRIMARY KEY DEFAULT nextval('segment_id_seq'),
    canonical_secid VARCHAR, source_secid VARCHAR, engine VARCHAR, market VARCHAR,
    board VARCHAR, date_from DATE, date_to DATE, priority INTEGER, is_primary BOOLEAN,
    notes VARCHAR, discovered_at TIMESTAMP,
    UNIQUE(canonical_secid, source_secid, board)
);
CREATE TABLE IF NOT EXISTS canonical_daily_prices (
    trade_date DATE, canonical_secid VARCHAR, source_secid VARCHAR, board VARCHAR,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, weighted_average_price DOUBLE,
    volume DOUBLE, value DOUBLE, number_of_trades BIGINT, source_priority INTEGER,
    loaded_at TIMESTAMP, PRIMARY KEY(trade_date, canonical_secid)
);
CREATE TABLE IF NOT EXISTS dividends (
    canonical_secid VARCHAR, registry_close_date DATE, declared_date DATE,
    payment_date DATE, dividend_per_share DOUBLE, currency VARCHAR, source VARCHAR,
    loaded_at TIMESTAMP, notes VARCHAR,
    PRIMARY KEY(canonical_secid, registry_close_date)
);
CREATE TABLE IF NOT EXISTS daily_returns (
    trade_date DATE, canonical_secid VARCHAR, price_return DOUBLE, log_return DOUBLE,
    dividend_cash DOUBLE, dividend_return DOUBLE, total_return DOUBLE,
    total_return_index DOUBLE, calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(trade_date, canonical_secid, calculation_version)
);
CREATE TABLE IF NOT EXISTS trading_calendar (
    trade_date DATE, market VARCHAR, is_trading_day BOOLEAN, session_type VARCHAR,
    source VARCHAR, loaded_at TIMESTAMP, PRIMARY KEY(trade_date, market, session_type)
);
"""


def database_path() -> Path:
    return PROJECT_ROOT / load_settings()["paths"]["database"]


@contextmanager
def connection(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target))
    try:
        yield con
    finally:
        con.close()


def init_database(path: Path | None = None) -> None:
    with connection(path) as con:
        con.execute(SCHEMA)


def upsert_instruments(con: duckdb.DuckDBPyConnection, items: Sequence[dict[str, Any]]) -> None:
    now = datetime.now()
    for item in items:
        con.execute(
            """INSERT INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(secid) DO UPDATE SET name=excluded.name,
               instrument_type=excluded.instrument_type, engine=excluded.engine,
               market=excluded.market, board=excluded.board,
               history_from=excluded.history_from, is_active=excluded.is_active,
               updated_at=excluded.updated_at""",
            [
                item["secid"],
                item["name"],
                item["instrument_type"],
                item["engine"],
                item["market"],
                item["board"],
                item["history_from"],
                item.get("is_active", True),
                now,
            ],
        )


def insert_daily_prices(con: duckdb.DuckDBPyConnection, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    before = con.execute("SELECT count(*) FROM daily_prices").fetchone()[0]
    columns = (
        "trade_date",
        "secid",
        "board",
        "open",
        "high",
        "low",
        "close",
        "weighted_average_price",
        "volume",
        "value",
        "number_of_trades",
        "source",
        "loaded_at",
    )
    frame = pd.DataFrame([{column: row.get(column) for column in columns} for row in rows])
    con.register("incoming_daily_prices", frame)
    try:
        con.execute(
            """INSERT INTO daily_prices SELECT * FROM incoming_daily_prices
               ON CONFLICT(trade_date, secid, board) DO NOTHING"""
        )
    finally:
        con.unregister("incoming_daily_prices")
    after = con.execute("SELECT count(*) FROM daily_prices").fetchone()[0]
    return int(after - before)


def latest_date(con: duckdb.DuckDBPyConnection, secid: str, board: str) -> date | None:
    return con.execute(
        "SELECT max(trade_date) FROM daily_prices WHERE secid=? AND board=?",
        [secid, board],
    ).fetchone()[0]


def row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return dict(
        con.execute("SELECT secid, count(*) FROM daily_prices GROUP BY secid ORDER BY secid").fetchall()
    )


def start_load(con: duckdb.DuckDBPyConnection, secid: str, date_from: date, date_to: date) -> int:
    return con.execute(
        """INSERT INTO load_log(secid,date_from,date_to,started_at,status)
           VALUES (?,?,?,current_timestamp,'running') RETURNING id""",
        [secid, date_from, date_to],
    ).fetchone()[0]


def finish_load(
    con: duckdb.DuckDBPyConnection,
    load_id: int,
    received: int,
    inserted: int,
    status: str,
    error: str | None = None,
) -> None:
    con.execute(
        """UPDATE load_log SET finished_at=current_timestamp, rows_received=?,
           rows_inserted=?, status=?, error_message=? WHERE id=?""",
        [received, inserted, status, error, load_id],
    )


def upsert_segments(con: duckdb.DuckDBPyConnection, segments: Sequence[dict[str, Any]]) -> None:
    for item in segments:
        con.execute(
            """INSERT INTO instrument_history_segments
               (canonical_secid,source_secid,engine,market,board,date_from,date_to,
                priority,is_primary,notes,discovered_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)
               ON CONFLICT(canonical_secid,source_secid,board) DO UPDATE SET
               date_from=excluded.date_from,date_to=excluded.date_to,
               priority=excluded.priority,is_primary=excluded.is_primary,
               notes=excluded.notes,discovered_at=excluded.discovered_at""",
            [
                item[key]
                for key in (
                    "canonical_secid",
                    "source_secid",
                    "engine",
                    "market",
                    "board",
                    "date_from",
                    "date_to",
                    "priority",
                    "is_primary",
                    "notes",
                )
            ],
        )


def insert_dividends(con: duckdb.DuckDBPyConnection, rows: Sequence[dict[str, Any]]) -> int:
    before = con.execute("SELECT count(*) FROM dividends").fetchone()[0]
    for row in rows:
        con.execute(
            """INSERT INTO dividends VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(canonical_secid,registry_close_date) DO UPDATE SET
               dividend_per_share=excluded.dividend_per_share,
               currency=excluded.currency,source=excluded.source,
               loaded_at=excluded.loaded_at,notes=excluded.notes""",
            [
                row.get(key)
                for key in (
                    "canonical_secid",
                    "registry_close_date",
                    "declared_date",
                    "payment_date",
                    "dividend_per_share",
                    "currency",
                    "source",
                    "loaded_at",
                    "notes",
                )
            ],
        )
    after = con.execute("SELECT count(*) FROM dividends").fetchone()[0]
    return int(after - before)
