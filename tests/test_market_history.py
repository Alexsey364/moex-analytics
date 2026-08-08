from datetime import date

import duckdb

from moex_analytics.actual_backfill.schema import DDL
from moex_analytics.market_history import build_trading_statistics, eligible_universe


def _database():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.execute("""CREATE TABLE historical_equity_universe(
        secid VARCHAR, instrument_type VARCHAR, regnumber VARCHAR, isin VARCHAR)""")
    return con


def test_eligible_universe_excludes_funds_and_technical_rows():
    con = _database()
    con.executemany(
        "INSERT INTO historical_equity_universe VALUES (?,?,?,?)",
        [
            ("SBER", "common_share", "1", "RU0009029540"),
            ("SBERP", "preferred_share", "2", "RU0009029557"),
            ("FUND", "fund", "3", "RU0000000000"),
            ("BAD", "common_share", None, None),
        ],
    )
    assert eligible_universe(con) == ["SBER", "SBERP"]


def test_statistics_use_one_explicit_board_chain_and_are_point_in_time():
    con = _database()
    rows = []
    for day, close, value in ((date(2024, 1, 1), 100, 1000), (date(2024, 1, 2), 101, 1200)):
        rows.append(
            [
                day,
                "AAA",
                "TQBR",
                3,
                close,
                close,
                close,
                close,
                close,
                close,
                close,
                close,
                close,
                value,
                10,
                2,
                "SUR",
                "u",
                "h",
                day,
            ]
        )
        rows.append(
            [
                day,
                "AAA",
                "SMAL",
                3,
                close,
                close,
                close,
                close,
                close,
                close,
                close,
                close,
                close,
                10,
                1,
                1,
                "SUR",
                "u",
                "h",
                day,
            ]
        )
    con.executemany("INSERT INTO moex_equity_eod VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    result = build_trading_statistics(con)
    assert result == {
        "boards": 2,
        "liquidity_rows": 2,
        "breadth_days": 2,
        "market_state_days": 2,
    }
    selected = con.execute("SELECT boardid FROM equity_board_history WHERE selected_for_chain").fetchone()[0]
    assert selected == "TQBR"
    assert (
        con.execute("SELECT advancing FROM market_breadth_daily ORDER BY trade_date DESC").fetchone()[0] == 1
    )
