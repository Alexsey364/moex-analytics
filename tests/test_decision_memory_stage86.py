import duckdb

from moex_analytics.daily_intelligence.schema import DDL as DAILY_DDL
from moex_analytics.decision_memory.core import _change, capture_decision_snapshot, latest_changes


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(DAILY_DDL)
    con.execute(
        "CREATE TABLE human_daily_reports(report_id VARCHAR,analysis_cutoff DATE,"
        "created_at TIMESTAMP,market_regime VARCHAR)"
    )
    con.execute(
        "CREATE TABLE human_instrument_synthesis(report_id VARCHAR,secid VARCHAR,"
        "action_group VARCHAR,risk_view VARCHAR,portfolio_view VARCHAR,timing_view VARCHAR,"
        "top_positive VARCHAR,top_negative VARCHAR)"
    )
    con.execute(
        "CREATE TABLE human_horizon_views(report_id VARCHAR,secid VARCHAR,horizon INTEGER,status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE current_portfolio_ranking(secid VARCHAR,cutoff DATE,horizon INTEGER,tie_group INTEGER)"
    )
    con.execute("CREATE TABLE news_stories(status VARCHAR,first_report_at TIMESTAMP,entities_json VARCHAR)")
    con.execute(
        "INSERT INTO daily_intelligence_snapshots "
        "(snapshot_id,cutoff,created_at,compatibility,fast_current,fast_total,"
        "production_unchanged,probability_gate_unchanged,immutable) VALUES "
        "('s1','2026-08-07','2026-08-07 20:00:00','PARTIAL',5,9,true,true,true),"
        "('s2','2026-08-10','2026-08-10 20:00:00','PARTIAL',6,9,true,true,true)"
    )
    con.execute(
        "INSERT INTO daily_analog_contexts VALUES "
        "('s1','AAA','full_state','2026-08-07','2026-08-07','v','[]','current','ok',true),"
        "('s2','AAA','full_state','2026-08-10','2026-08-07','v','[]','historical_source_older','old',true)"
    )
    con.execute(
        "INSERT INTO human_daily_reports VALUES "
        "('r1','2026-08-07','2026-08-07 19:00:00','обычный'),"
        "('r2','2026-08-10','2026-08-10 19:00:00','стрессовый')"
    )
    con.execute(
        "INSERT INTO human_instrument_synthesis VALUES "
        "('r1','AAA','wait','Риск умеренный','Допустимый вес','Подождать','rank','risk'),"
        "('r2','AAA','do_not_increase','Повышенный риск','Не увеличивать','Не увеличивать','rank','risk')"  # noqa: RUF001
    )
    con.execute("INSERT INTO human_horizon_views VALUES ('r1','AAA',20,'neutral'),('r2','AAA',20,'cautious')")
    con.execute(
        "INSERT INTO current_portfolio_ranking VALUES ('AAA','2026-08-07',20,2),('AAA','2026-08-10',20,4)"
    )
    return con


def test_decision_memory_uses_only_same_cutoff_saved_reports_and_explains_material_change() -> None:
    con = _con()
    first = capture_decision_snapshot(con, "s1")
    second = capture_decision_snapshot(con, "s2")
    assert first["states"] == second["states"] == 1
    assert second["material_changes"] == 1
    change = latest_changes(con)[0]
    assert change["change"] == "DETERIORATED"
    assert {"status", "rank_group", "risk_state", "market_state", "portfolio_action"} <= set(change["blocks"])
    assert all("→" in reason for reason in change["reasons"])
    assert capture_decision_snapshot(con, "s2")["idempotent"]
    assert con.execute("SELECT bool_and(immutable) FROM daily_decision_states").fetchone()[0]


def test_micro_changes_are_suppressed_by_materiality_policy() -> None:
    previous = {
        "status": "wait",
        "rank_group": "group_2",
        "risk_state": "Риск умеренный",
        "market_state": "обычный",
        "sector_state": "same",
        "analog_state": "current",
        "news_state": "active_0",
        "portfolio_action": "Допустимый вес",
    }
    current = previous | {"news_state": "active_1"}
    state, material, blocks, reasons = _change(previous, current)
    assert state == "UNCHANGED" and not material
    assert blocks == ["news_state"] and reasons == []
