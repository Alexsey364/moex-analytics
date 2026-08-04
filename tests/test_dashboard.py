from datetime import date, datetime

import pytest

from moex_analytics.dashboard.data_access import (
    DatabaseUnavailable,
    database_summary,
    dividends,
    instrument_summary,
    prices,
    quality_issues,
)
from moex_analytics.dashboard.formatting import format_date, format_number, format_percent
from moex_analytics.dashboard.state import run_update_steps
from moex_analytics.database import connection, init_database


@pytest.fixture
def dashboard_db(tmp_path):
    path = tmp_path / "dashboard.duckdb"
    init_database(path)
    with connection(path) as con:
        con.execute(
            """INSERT INTO instruments VALUES
               ('TEST','Test','share','stock','shares','MAIN','2024-01-01',true,current_timestamp)"""
        )
        for index in range(21):
            day = date.fromordinal(date(2024, 1, 1).toordinal() + index)
            close = 100 + index
            con.execute(
                """INSERT INTO canonical_daily_prices VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    day,
                    "TEST",
                    "TEST",
                    "MAIN",
                    close,
                    close,
                    close,
                    close,
                    close,
                    1,
                    close,
                    1,
                    100,
                    datetime.now(),
                ],
            )
        con.execute(
            """INSERT INTO dividends VALUES
               ('TEST','2024-01-10',NULL,NULL,5,'RUB','fixture',current_timestamp,'test')"""
        )
        con.execute(
            """INSERT INTO data_quality_issues
               (secid,trade_date,issue_type,description,detected_at)
               VALUES ('TEST','2024-01-10','fixture','Problem',current_timestamp)"""
        )
    return path


def test_summary_and_changes(dashboard_db):
    summary = database_summary(dashboard_db)
    assert summary["instruments"] == 1
    assert summary["canonical_rows"] == 21
    row = instrument_summary(dashboard_db).iloc[0]
    assert row.last_price == 120
    assert row.change_1d == pytest.approx(120 / 119 - 1)
    assert row.change_20d == pytest.approx(120 / 100 - 1)


def test_filters_dividends_and_issues(dashboard_db):
    assert len(prices("TEST", date(2024, 1, 10), date(2024, 1, 12), dashboard_db)) == 3
    assert len(dividends("TEST", dashboard_db)) == 1
    assert len(quality_issues("TEST", "fixture", path=dashboard_db)) == 1


def test_missing_and_empty_database(tmp_path):
    with pytest.raises(DatabaseUnavailable):
        database_summary(tmp_path / "missing.duckdb")
    path = tmp_path / "empty.duckdb"
    init_database(path)
    assert database_summary(path)["canonical_rows"] == 0


def test_russian_formatting():
    assert format_date(date(2024, 1, 2)) == "02.01.2024"
    assert format_number(12345.6, 1) == "12 345,6"
    assert format_percent(0.1234, 2) == "12,34%"


def test_update_order_and_stop_on_error():
    calls = []
    result = run_update_steps(
        [
            ("one", lambda: calls.append("one")),
            ("two", lambda: calls.append("two")),
        ]
    )
    assert result.completed == ["one", "two"]

    def fail():
        raise RuntimeError("stop")

    result = run_update_steps([("one", lambda: 1), ("bad", fail), ("never", lambda: 3)])
    assert result.completed == ["one"]
    assert result.error_step == "bad"
    assert "never" not in result.outputs
