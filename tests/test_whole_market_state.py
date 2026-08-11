from datetime import date, timedelta

import duckdb

from moex_analytics.whole_market_state import build_whole_market_state


def _database() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE canonical_daily_prices(
        trade_date DATE, canonical_secid VARCHAR, source_secid VARCHAR, board VARCHAR,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, weighted_average_price DOUBLE,
        volume DOUBLE, value DOUBLE, number_of_trades BIGINT, source_priority INTEGER,
        loaded_at TIMESTAMP)""")
    start = date(2024, 1, 1)
    rows = []
    for index in range(300):
        day = start + timedelta(days=index)
        close = 3000 + index
        rows.append(
            (
                day,
                "IMOEX",
                "IMOEX",
                "SNDX",
                close - 2,
                close + 5,
                close - 5,
                close,
                close,
                1,
                1000,
                10,
                1,
                day,
            )
        )
    con.executemany("INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con


def test_snapshot_is_named_immutable_and_idempotent() -> None:
    con = _database()
    first = build_whole_market_state(con)
    second = build_whole_market_state(con)
    assert first["observations"] == 300
    assert second["idempotent"] is True
    assert con.execute("SELECT count(*) FROM whole_market_state_daily").fetchone()[0] == 300
    columns = {row[0] for row in con.execute("DESCRIBE whole_market_state_daily").fetchall()}
    assert {"return_250", "distance_sma200", "regime_json", "immutable"} <= columns
    saved = con.execute(
        "SELECT return_20 FROM whole_market_state_daily ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()[0]
    assert saved > 0


def test_cutoff_prevents_future_rows_and_does_not_touch_legacy() -> None:
    con = _database()
    con.execute("CREATE TABLE market_state_daily(trade_date DATE PRIMARY KEY, state_label VARCHAR)")
    cutoff = date(2024, 7, 1)
    result = build_whole_market_state(con, cutoff)
    assert result["date_to"] == str(cutoff)
    assert con.execute("SELECT count(*) FROM market_state_daily").fetchone()[0] == 0
    assert con.execute("SELECT max(trade_date) FROM whole_market_state_daily").fetchone()[0] == cutoff
