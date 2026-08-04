from datetime import date, datetime

from moex_analytics.calendar import (
    next_trading_day,
    previous_trading_day,
    rebuild_calendar,
    shift_trading_days,
    trading_days_between,
)
from moex_analytics.canonical import build_canonical
from moex_analytics.database import (
    connection,
    init_database,
    insert_daily_prices,
    upsert_segments,
)


def price(day, board, close):
    return {
        "trade_date": day,
        "secid": "TEST",
        "board": board,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "weighted_average_price": close,
        "volume": 1,
        "value": close,
        "number_of_trades": 1,
        "source": "fixture",
        "loaded_at": datetime.now(),
    }


def segment(board, priority, start, end):
    return {
        "canonical_secid": "TEST",
        "source_secid": "TEST",
        "engine": "stock",
        "market": "shares",
        "board": board,
        "date_from": start,
        "date_to": end,
        "priority": priority,
        "is_primary": priority == 100,
        "notes": "fixture",
    }


def test_stitch_priority_conflict_and_calendar(tmp_path):
    path = tmp_path / "db.duckdb"
    init_database(path)
    with connection(path) as con:
        upsert_segments(
            con,
            [
                segment("OLD", 90, "2024-01-01", "2024-01-03"),
                segment("NEW", 100, "2024-01-03", "2024-01-10"),
            ],
        )
        insert_daily_prices(
            con,
            [
                price("2024-01-02", "OLD", 10),
                price("2024-01-03", "OLD", 11),
                price("2024-01-03", "NEW", 12),
                price("2024-01-05", "NEW", 13),
            ],
        )
        rows, conflicts = build_canonical(con)
        assert (rows, conflicts) == (3, 1)
        assert con.execute(
            "SELECT board,close FROM canonical_daily_prices WHERE trade_date='2024-01-03'"
        ).fetchone() == ("NEW", 12)
        assert con.execute("SELECT count(*) FROM canonical_daily_prices").fetchone()[0] == 3
        assert rebuild_calendar(con) == 3
        assert next_trading_day(con, date(2024, 1, 2)) == date(2024, 1, 3)
        assert previous_trading_day(con, date(2024, 1, 5)) == date(2024, 1, 3)
        assert shift_trading_days(con, date(2024, 1, 2), 2) == date(2024, 1, 5)
        assert shift_trading_days(con, date(2024, 1, 5), -2) == date(2024, 1, 2)
        assert shift_trading_days(con, date(2024, 1, 3), 0) == date(2024, 1, 3)
        assert trading_days_between(con, date(2024, 1, 2), date(2024, 1, 5)) == 2


def test_segments_without_overlap_are_stitched(tmp_path):
    path = tmp_path / "db.duckdb"
    init_database(path)
    with connection(path) as con:
        upsert_segments(
            con,
            [
                segment("OLD", 90, "2024-01-01", "2024-01-02"),
                segment("NEW", 100, "2024-01-03", "2024-01-10"),
            ],
        )
        insert_daily_prices(
            con,
            [
                price("2024-01-02", "OLD", 10),
                price("2024-01-03", "NEW", 11),
            ],
        )
        assert build_canonical(con) == (2, 0)
