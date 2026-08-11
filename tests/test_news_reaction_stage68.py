from datetime import UTC, date, datetime, timedelta

import duckdb

from moex_analytics.news_intelligence.core import ensure_schema as ensure_news
from moex_analytics.news_intelligence.core import ingest_records
from moex_analytics.news_reaction.core import build_reaction_memory, reaction_status


def test_reactions_are_pit_eod_descriptive_and_idempotent():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,"
                "close DOUBLE,volume DOUBLE)")
    start = date(2026, 1, 1)
    for i in range(70):
        day = start + timedelta(days=i)
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?,?)", [day, "SBERP", 100+i, 10+i])
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?,?)", [day, "IMOEX", 200+i, 20+i])
    ensure_news(con)
    available = datetime(2026, 1, 2, 12, tzinfo=UTC)
    ingest_records(con, "official", [{"news_id": "n1", "published_at": available-timedelta(days=1),
        "headline": "Sber reports growth", "summary": "profit growth", "url": "https://x/1",
        "content_hash": "h", "language": "en"}], available)
    first = build_reaction_memory(con, date(2026, 3, 11))
    second = build_reaction_memory(con, date(2026, 3, 11))
    assert first["rows"] == second["rows"] == 4
    rows = con.execute("SELECT horizon,anchor_date,point_in_time_safe,intraday_status,interpretation "
                       "FROM news_reaction_memory ORDER BY horizon").fetchall()
    assert rows[0][1] >= available.date()
    assert all(row[2] and row[3] == "unavailable_no_intraday_source" for row in rows)
    assert rows[0][4] == "tone_aligned"
    assert reaction_status(con)["items"] == 1


def test_no_prices_is_explicitly_insufficient():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,"
                "close DOUBLE,volume DOUBLE)")
    assert build_reaction_memory(con)["status"] == "insufficient_price_history"
