from datetime import date

import duckdb

import moex_analytics.daily_briefing.core as briefing
from moex_analytics.daily_intelligence.schema import DDL as DAILY_DDL


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(DAILY_DDL)
    con.execute(
        "CREATE TABLE whole_market_state_daily(trade_date DATE,available_from TIMESTAMP,"
        "market_state_label VARCHAR,return_20 DOUBLE,drawdown DOUBLE,realized_vol20 DOUBLE,"
        "breadth_json JSON,rates_json JSON,commodities_json JSON)"
    )
    con.execute(
        "CREATE TABLE daily_decision_changes(snapshot_id VARCHAR,secid VARCHAR,"
        "change_state VARCHAR,material BOOLEAN,reasons_json JSON)"
    )
    con.execute(
        "CREATE TABLE portfolio_review_runs(run_id VARCHAR,verdict_run_id VARCHAR,cutoff DATE,"
        "status VARCHAR,created_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE portfolio_final_verdicts(run_id VARCHAR,instrument VARCHAR,"
        "portfolio_action VARCHAR,risk_status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE portfolio_horizon_verdicts(run_id VARCHAR,instrument VARCHAR,"
        "horizon INTEGER,directional_state VARCHAR)"
    )
    con.execute(
        "CREATE TABLE portfolio_review_allocations(run_id VARCHAR,amount INTEGER,"
        "allocation_json JSON,cash_reserve DOUBLE,status VARCHAR,reason VARCHAR)"
    )
    con.execute(
        "CREATE TABLE portfolio_scenario_runs(run_id VARCHAR,cutoff DATE,status VARCHAR,created_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE portfolio_scenario_branches(run_id VARCHAR,label VARCHAR,episodes INTEGER,"
        "total_episodes INTEGER,median_imoex_return DOUBLE,median_drawdown DOUBLE,"
        "historical_frequency_text VARCHAR)"
    )
    con.execute(
        "CREATE TABLE state_similarity_runs(run_id VARCHAR,cutoff DATE,status VARCHAR,created_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE state_similarity_matches(run_id VARCHAR,analog_type VARCHAR,"
        "analog_date DATE,similarity DOUBLE)"
    )
    con.execute(
        "CREATE TABLE news_stories(headline VARCHAR,event_type VARCHAR,reliability VARCHAR,"
        "status VARCHAR,first_report_at TIMESTAMP,last_update_at TIMESTAMP)"
    )
    con.execute("CREATE TABLE forecast_registry(forecast_id VARCHAR)")
    con.execute("CREATE TABLE forecast_outcomes(forecast_id VARCHAR,outcome_status VARCHAR)")
    return con


def _insert_day(con: duckdb.DuckDBPyConnection, cutoff: date, suffix: str, state: str) -> None:
    con.execute(
        """INSERT INTO daily_intelligence_snapshots
        (snapshot_id,cutoff,created_at,compatibility,fast_current,fast_total,
        production_unchanged,probability_gate_unchanged,immutable)
        VALUES (?,?,?,'PARTIAL',6,9,true,true,true)""",
        [f"snapshot-{suffix}", cutoff, f"{cutoff} 20:00:00"],
    )
    con.execute(
        "INSERT INTO whole_market_state_daily VALUES (?,?,?,.02,-.1,.25,'{}','{}','{}')",
        [cutoff, f"{cutoff} 19:00:00", state],
    )
    con.execute(
        "INSERT INTO daily_decision_changes VALUES (?,?,?,true,'[\"risk changed\"]')",
        [f"snapshot-{suffix}", "AAA", "DETERIORATED"],
    )
    con.execute(
        "INSERT INTO portfolio_review_runs VALUES (?,?,?,'completed',?)",
        [f"review-{suffix}", f"verdict-{suffix}", cutoff, f"{cutoff} 19:00:00"],
    )
    con.execute(
        "INSERT INTO portfolio_final_verdicts VALUES (?,?,?,?)",
        [f"verdict-{suffix}", "AAA", "🟡 ждать", "умеренный"],
    )
    con.executemany(
        "INSERT INTO portfolio_horizon_verdicts VALUES (?,?,?,?)",
        [(f"verdict-{suffix}", "AAA", horizon, "neutral") for horizon in (20, 60, 120, 250)],
    )
    con.execute(
        "INSERT INTO portfolio_review_allocations VALUES (?,100000,'{}',100000,'reserve','edge не доказан')",
        [f"review-{suffix}"],
    )
    con.execute(
        "INSERT INTO portfolio_scenario_runs VALUES (?,?,'completed',?)",
        [f"scenario-{suffix}", cutoff, f"{cutoff} 19:00:00"],
    )
    con.execute(
        "INSERT INTO portfolio_scenario_branches VALUES (?, 'Боковик',5,10,.02,-.05,'5 эпизодов из 10')",
        [f"scenario-{suffix}"],
    )
    con.execute(
        "INSERT INTO state_similarity_runs VALUES (?,?,'completed',?)",
        [f"state-{suffix}", cutoff, f"{cutoff} 19:00:00"],
    )
    con.execute(
        "INSERT INTO state_similarity_matches VALUES (?,'state','2020-01-01',.8)",
        [f"state-{suffix}"],
    )


def test_briefing_is_immutable_exported_and_compares_saved_days(tmp_path, monkeypatch) -> None:
    con = _con()
    monkeypatch.setattr(briefing, "EXPORT_DIR", tmp_path)
    _insert_day(con, date(2026, 8, 7), "one", "normal")
    first = briefing.build_daily_briefing(con)
    _insert_day(con, date(2026, 8, 10), "two", "stress")
    second = briefing.build_daily_briefing(con)
    assert first["cutoff"] == date(2026, 8, 7)
    assert second["cutoff"] == date(2026, 8, 10)
    assert second["previous_briefing_id"] == first["briefing_id"]
    assert briefing.build_daily_briefing(con)["idempotent"]
    assert (tmp_path / f"2026-08-10_{second['briefing_id']}.md").exists()
    assert (tmp_path / f"2026-08-10_{second['briefing_id']}.html").exists()
    comparison = con.execute(
        "SELECT market_change,status_changes FROM daily_briefing_comparisons WHERE briefing_id=?",
        [second["briefing_id"]],
    ).fetchone()
    assert comparison == ("normal → stress", 1)
    assert con.execute("SELECT bool_and(immutable) FROM daily_investor_briefings").fetchone()[0]


def test_briefing_does_not_exist_without_same_cutoff_portfolio_verdict(tmp_path, monkeypatch) -> None:
    con = _con()
    monkeypatch.setattr(briefing, "EXPORT_DIR", tmp_path)
    con.execute(
        """INSERT INTO daily_intelligence_snapshots
        (snapshot_id,cutoff,created_at,compatibility,fast_current,fast_total,immutable)
        VALUES ('empty','2026-08-10',now(),'INCOMPATIBLE',0,9,true)"""
    )
    try:
        briefing.build_daily_briefing(con)
    except ValueError as exc:
        assert "same-cutoff portfolio verdict" in str(exc)
    else:
        raise AssertionError("briefing must not fabricate missing verdicts")
