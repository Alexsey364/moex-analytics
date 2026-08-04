from datetime import date, datetime

from moex_analytics.cli import resolve_start
from moex_analytics.database import connection, init_database, insert_daily_prices


def test_incremental_start_is_next_calendar_day(tmp_path):
    path = tmp_path / "test.duckdb"
    init_database(path)
    instrument = {"secid": "SBER", "board": "TQBR", "history_from": "2013-03-25"}
    with connection(path) as con:
        insert_daily_prices(
            con,
            [
                {
                    "trade_date": "2024-01-31",
                    "secid": "SBER",
                    "board": "TQBR",
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "weighted_average_price": 1,
                    "volume": 1,
                    "value": 1,
                    "number_of_trades": 1,
                    "source": "test",
                    "loaded_at": datetime.now(),
                }
            ],
        )
        assert resolve_start(con, instrument, None) == date(2024, 2, 1)


def test_incremental_start_uses_history_from_when_empty(tmp_path):
    path = tmp_path / "test.duckdb"
    init_database(path)
    instrument = {"secid": "SBER", "board": "TQBR", "history_from": "2013-03-25"}
    with connection(path) as con:
        assert resolve_start(con, instrument, None) == date(2013, 3, 25)
