from datetime import UTC, date, datetime

import duckdb

from moex_analytics.historical_events.core import (
    build_foundation,
    build_timeline,
    classify_pit,
    ensure_schema,
    event_status,
    validate_events,
)


def _database():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE trading_calendar(trade_date DATE PRIMARY KEY)")
    con.executemany("INSERT INTO trading_calendar VALUES (?)", [(date(2024, 1, d),) for d in range(1, 11)])
    con.execute("""CREATE TABLE event_calendar(event_id VARCHAR,event_type VARCHAR,country VARCHAR,
      related_instrument VARCHAR,scheduled_date DATE,actual_release_at TIMESTAMPTZ,source VARCHAR,
      status VARCHAR,importance VARCHAR,loaded_at TIMESTAMP,notes VARCHAR)""")
    con.execute("""CREATE TABLE sber_events(event_id VARCHAR,event_type VARCHAR,event_subtype VARCHAR,
      title VARCHAR,description VARCHAR,scheduled_at TIMESTAMPTZ,occurred_at TIMESTAMPTZ,
      published_at TIMESTAMPTZ,available_from TIMESTAMPTZ,related_entity VARCHAR,
      expected_status VARCHAR,severity DOUBLE,source_id VARCHAR,source_url VARCHAR,
      official_status VARCHAR,point_in_time_safe BOOLEAN,validation_status VARCHAR,notes VARCHAR)""")
    return con


def test_missing_availability_never_becomes_validated():
    assert classify_pit(None, True, False) == ("manual_review", "missing_available_from")
    assert classify_pit(datetime.now(UTC), True, True)[0] == "manual_review"


def test_scheduled_and_surprise_timeline_are_point_in_time_safe():
    con = _database()
    ensure_schema(con)
    scheduled = datetime(2024, 1, 8, tzinfo=UTC)
    known = datetime(2024, 1, 3, tzinfo=UTC)
    con.execute(
        """INSERT INTO historical_events(event_id,event_family,event_type,event_start,
      available_from,expected_or_scheduled,surprise_event,validation_status,pit_status)
      VALUES ('scheduled','central_bank','meeting',?,?,TRUE,FALSE,'validated','pit_safe')""",
        [scheduled, known],
    )
    surprise = datetime(2024, 1, 5, tzinfo=UTC)
    con.execute(
        """INSERT INTO historical_events(event_id,event_family,event_type,event_start,
      available_from,expected_or_scheduled,surprise_event,validation_status,pit_status)
      VALUES ('surprise','systemic','shock',?,?,FALSE,TRUE,'validated','pit_safe')""",
        [surprise, surprise],
    )
    assert build_timeline(con) > 0
    scheduled_rows = con.execute(
        "SELECT trade_date,days_until_scheduled_event FROM historical_event_timeline "
        "WHERE event_id='scheduled' ORDER BY trade_date"
    ).fetchall()
    assert scheduled_rows[0] == (date(2024, 1, 3), 5)
    assert (
        con.execute(
            "SELECT count(*) FROM historical_event_timeline "
            "WHERE event_id='scheduled' AND trade_date<'2024-01-03'"
        ).fetchone()[0]
        == 0
    )
    assert (
        con.execute(
            "SELECT count(*) FROM historical_event_timeline "
            "WHERE event_id='surprise' AND days_until_scheduled_event IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_foundation_reuses_sources_and_flags_unknown_publication():
    con = _database()
    con.execute(
        "INSERT INTO event_calendar VALUES "
        "('rate','key_rate_decision','RU',NULL,'2024-01-05','2024-01-05 10:30:00+00',"
        "'CBR','released','high',current_timestamp,'official')"
    )
    con.execute(
        "INSERT INTO event_calendar VALUES "
        "('div','dividend_registry_close','RU','SBERP','2024-01-09',NULL,'MOEX ISS',"
        "'known','medium',current_timestamp,'no announcement time')"
    )
    result = build_foundation(con)
    assert result["source_rows"] == 2
    assert event_status(con)["events"] == 2
    assert (
        con.execute(
            "SELECT validation_status FROM historical_events WHERE event_id='calendar:div'"
        ).fetchone()[0]
        == "manual_review"
    )
    assert validate_events(con)["critical"] == 1


def test_duplicate_content_is_reported_without_silent_merge():
    con = _database()
    ensure_schema(con)
    columns = (
        "event_id,event_family,event_type,event_start,available_from,expected_or_scheduled,"
        "surprise_event,content_hash,validation_status,pit_status"
    )
    con.execute(
        f"INSERT INTO historical_events({columns}) VALUES "
        "('a','market','x',current_timestamp,current_timestamp,FALSE,TRUE,"
        "'same','validated','pit_safe')"
    )
    con.execute(
        f"INSERT INTO historical_events({columns}) VALUES "
        "('b','market','x',current_timestamp,current_timestamp,FALSE,TRUE,"
        "'same','validated','pit_safe')"
    )
    result = validate_events(con)
    assert result["issues"] == 1
    assert (
        con.execute("SELECT issue_type FROM historical_event_quality_issues").fetchone()[0]
        == "duplicate_source_copy"
    )
