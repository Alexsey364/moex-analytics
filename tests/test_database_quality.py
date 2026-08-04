from datetime import datetime

from moex_analytics.data_quality import find_issues
from moex_analytics.database import (
    connection,
    init_database,
    insert_daily_prices,
    latest_date,
)


def row(**overrides):
    value = {
        "trade_date": "2024-01-10",
        "secid": "SBER",
        "board": "TQBR",
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "weighted_average_price": 101.0,
        "volume": 10.0,
        "value": 1010.0,
        "number_of_trades": 2,
        "source": "test",
        "loaded_at": datetime.now(),
    }
    value.update(overrides)
    return value


def test_schema_and_idempotent_insert(tmp_path):
    path = tmp_path / "test.duckdb"
    init_database(path)
    with connection(path) as con:
        tables = {item[0] for item in con.execute("SHOW TABLES").fetchall()}
        assert {"instruments", "daily_prices", "load_log", "data_quality_issues"} <= tables
        assert insert_daily_prices(con, [row()]) == 1
        assert insert_daily_prices(con, [row()]) == 0
        assert latest_date(con, "SBER", "TQBR").isoformat() == "2024-01-10"


def test_quality_detects_bad_range(tmp_path):
    path = tmp_path / "test.duckdb"
    init_database(path)
    with connection(path) as con:
        insert_daily_prices(
            con,
            [
                row(high=80.0, low=90.0),
                row(trade_date="2024-01-11", close=120.0),
            ],
        )
        kinds = {issue["issue_type"] for issue in find_issues(con)}
        assert "high_below_low" in kinds
        assert "close_outside_range" in kinds
