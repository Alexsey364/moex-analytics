import math

import pytest

from moex_analytics.database import connection, init_database
from moex_analytics.returns import CALCULATION_VERSION, calculate_all, calculate_rows


def test_price_log_dividend_and_total_return():
    prices = [
        {"trade_date": "2024-01-01", "canonical_secid": "TEST", "close": 100.0},
        {"trade_date": "2024-01-02", "canonical_secid": "TEST", "close": 110.0},
        {"trade_date": "2024-01-03", "canonical_secid": "TEST", "close": 110.0},
    ]
    rows = calculate_rows(prices, {"2024-01-02": 5.0})
    assert rows[1]["price_return"] == pytest.approx(0.1)
    assert rows[1]["log_return"] == pytest.approx(math.log(1.1))
    assert rows[1]["dividend_return"] == pytest.approx(0.05)
    assert rows[1]["total_return"] == pytest.approx(0.15)
    assert rows[1]["total_return_index"] == pytest.approx(1.15)
    assert rows[2]["dividend_cash"] == 0
    assert rows[2]["total_return_index"] == pytest.approx(1.15)


def test_missing_price_does_not_create_return():
    rows = calculate_rows(
        [
            {"trade_date": "2024-01-01", "canonical_secid": "TEST", "close": 100.0},
            {"trade_date": "2024-01-02", "canonical_secid": "TEST", "close": None},
        ]
    )
    assert rows[1]["price_return"] is None
    assert rows[1]["log_return"] is None


def test_calculate_all_is_repeatable(tmp_path):
    path = tmp_path / "db.duckdb"
    init_database(path)
    with connection(path) as con:
        con.execute(
            """INSERT INTO canonical_daily_prices VALUES
               ('2024-01-01','TEST','TEST','MAIN',1,1,1,100,100,1,100,1,100,current_timestamp),
               ('2024-01-02','TEST','TEST','MAIN',1,1,1,110,110,1,110,1,100,current_timestamp)"""
        )
        assert calculate_all(con) == 2
        assert calculate_all(con) == 2
        assert (
            con.execute(
                "SELECT count(*) FROM daily_returns WHERE calculation_version=?",
                [CALCULATION_VERSION],
            ).fetchone()[0]
            == 2
        )
