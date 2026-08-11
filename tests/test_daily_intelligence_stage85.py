from datetime import date, timedelta
from pathlib import Path

import duckdb

from moex_analytics.daily_intelligence.core import (
    CURRENT_WITH_SLOW_DATA,
    build_daily_snapshot,
    latest_daily_snapshot,
)
from moex_analytics.daily_intelligence.schema import DDL as DAILY_DDL
from moex_analytics.visual_memory.schema import DDL as VISUAL_DDL

PORTFOLIO = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)")
    for secid in PORTFOLIO:
        con.executemany(
            "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
            [(secid, date(2026, 7, 1) + timedelta(days=index), 100 + index) for index in range(41)],
        )
    con.execute("CREATE TABLE current_portfolio_ranking(run_id VARCHAR,cutoff DATE)")
    con.execute("INSERT INTO current_portfolio_ranking VALUES ('rank','2026-08-10')")
    con.execute("CREATE TABLE whole_market_state_runs(run_id VARCHAR,cutoff DATE,status VARCHAR)")
    con.execute("INSERT INTO whole_market_state_runs VALUES ('market','2026-08-10','completed')")
    con.execute("CREATE TABLE sector_rotation_runs(run_id VARCHAR,date_to DATE,status VARCHAR)")
    con.execute("INSERT INTO sector_rotation_runs VALUES ('sector','2026-08-10','completed')")
    con.execute("CREATE TABLE news_items(available_from TIMESTAMP)")
    con.execute("INSERT INTO news_items VALUES ('2026-08-10 12:00:00')")
    con.execute("CREATE TABLE fundamental_documents(publication_date DATE)")
    con.execute("INSERT INTO fundamental_documents VALUES ('2026-06-30')")
    con.execute("CREATE TABLE human_daily_reports(report_id VARCHAR,analysis_cutoff DATE)")
    con.execute("INSERT INTO human_daily_reports VALUES ('portfolio','2026-08-10')")
    con.execute("CREATE TABLE forecast_registry(cutoff DATE)")
    con.execute("INSERT INTO forecast_registry VALUES ('2026-08-10')")
    con.execute(VISUAL_DDL)
    con.execute(
        "INSERT INTO visual_memory_runs VALUES "
        "('visual',now(),'2026-08-07','scenario',135,'v',true,true,true,'completed','{}')"
    )
    return con


def test_unified_snapshot_hash_cutoffs_and_incremental_analog_context_are_immutable() -> None:
    con = _con()
    first = build_daily_snapshot(con, source_update_run="daily")
    second = build_daily_snapshot(con, source_update_run="daily")
    assert first["cutoff"] == date(2026, 8, 10)
    assert first["compatibility"] == CURRENT_WITH_SLOW_DATA
    assert first["analog_contexts"] == 27
    assert not first["idempotent"] and second["idempotent"]
    components = {row["component"]: row for row in latest_daily_snapshot(con)["components"]}
    assert components["analogs"]["cutoff"] == date(2026, 8, 7)
    assert components["fundamentals"]["status"] == "slow_current"
    assert (
        con.execute("SELECT count(distinct snapshot_id) FROM daily_intelligence_snapshots").fetchone()[0] == 1
    )
    assert con.execute("SELECT bool_and(immutable) FROM daily_intelligence_components").fetchone()[0]
    future = con.execute(
        """SELECT count(*) FROM daily_analog_contexts,json_each(current_path_json) point
        WHERE cast(json_extract(point.value,'$.relative_session') AS INTEGER)>0"""
    ).fetchone()[0]
    assert future == 0


def test_required_fast_component_mismatch_is_partial_not_silently_current() -> None:
    con = _con()
    con.execute("UPDATE current_portfolio_ranking SET cutoff='2026-08-07'")
    snapshot = build_daily_snapshot(con)
    assert snapshot["compatibility"] == "PARTIAL"


def test_latest_snapshot_is_safe_on_read_only_dashboard_connection(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.duckdb"
    writer = duckdb.connect(str(database))
    writer.execute(DAILY_DDL)
    writer.execute(
        """INSERT INTO daily_intelligence_snapshots (
        snapshot_id,cutoff,created_at,compatibility,component_hash,compatibility_hash,
        fast_current,fast_total,source_update_run,immutable
        ) VALUES ('snapshot','2026-08-10',now(),'PARTIAL','components','contract',6,9,NULL,TRUE)"""
    )
    writer.close()

    reader = duckdb.connect(str(database), read_only=True)
    snapshot = latest_daily_snapshot(reader)
    reader.close()

    assert snapshot["snapshot_id"]
    assert snapshot["cutoff"] == date(2026, 8, 10)
