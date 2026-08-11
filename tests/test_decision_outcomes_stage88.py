from datetime import date

import duckdb
import pandas as pd

from moex_analytics.decision_memory.schema import DDL as DECISION_DDL
from moex_analytics.decision_outcomes import update_decision_outcomes


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(DECISION_DDL)
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)")
    dates = pd.bdate_range(date(2020, 1, 1), periods=300)
    for secid, multiplier in (("AAA", 1.001), ("IMOEX", 1.0005)):
        value = 100.0
        rows = []
        for day in dates:
            rows.append((secid, day.date(), value))
            value *= multiplier
        con.executemany("INSERT INTO canonical_daily_prices VALUES (?,?,?)", rows)
    con.execute(
        "CREATE TABLE human_daily_reports(report_id VARCHAR,analysis_cutoff DATE,created_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE human_instrument_synthesis(report_id VARCHAR,secid VARCHAR,"
        "action_group VARCHAR,portfolio_view VARCHAR)"
    )
    con.execute("INSERT INTO human_daily_reports VALUES ('r1',?,'2020-01-01 20:00:00')", [dates[0].date()])
    con.execute(
        "INSERT INTO human_instrument_synthesis VALUES "
        "('r1','AAA','do_not_increase','Не увеличивать из-за концентрации')"  # noqa: RUF001
    )
    con.execute(
        "INSERT INTO daily_decision_states "
        "(snapshot_id,cutoff,secid,status,portfolio_action,source_report_id,immutable) "
        "VALUES ('s1',?,'AAA','wait','Допустимый вес','r1',true)",
        [dates[0].date()],
    )
    return con


def test_live_and_historical_rule_replay_are_separate_and_idempotent() -> None:
    con = _con()
    first = update_decision_outcomes(con)
    second = update_decision_outcomes(con)
    assert first["live_records"] == 1 and first["research_records"] == 1
    assert first["matured_new"] == 10
    assert second["matured_new"] == 0
    assert set(
        row[0] for row in con.execute("SELECT DISTINCT source_type FROM decision_outcome_records").fetchall()
    ) == {"live_daily_snapshot", "historical_rule_replay"}
    assert con.execute("SELECT count(*) FROM decision_realized_outcomes").fetchone()[0] == 10
    live_objectives = {
        row[0]
        for row in con.execute(
            """SELECT DISTINCT objective_metric FROM decision_realized_outcomes o
            JOIN decision_outcome_records r USING(decision_id)
            WHERE source_type='live_daily_snapshot'"""
        ).fetchall()
    }
    assert live_objectives == {"path_stability_not_directional_correctness"}
    assert con.execute("SELECT bool_and(immutable) FROM decision_realized_outcomes").fetchone()[0]
    assert con.execute("SELECT count(*) FROM canonical_live_decisions").fetchone()[0] == 1


def test_second_snapshot_for_same_session_does_not_duplicate_live_decision() -> None:
    con = _con()
    update_decision_outcomes(con)
    con.execute(
        """INSERT INTO daily_decision_states
        (snapshot_id,cutoff,secid,status,portfolio_action,source_report_id,immutable)
        SELECT 's2',cutoff,secid,'consider','Можно рассматривать','r1',true
        FROM daily_decision_states WHERE snapshot_id='s1'"""
    )

    result = update_decision_outcomes(con)

    assert result["live_records"] == 1
    assert result["inserted_live"] == 0
    assert con.execute("SELECT count(*) FROM canonical_live_decisions").fetchone()[0] == 1
    assert con.execute(
        "SELECT first_snapshot_id FROM canonical_live_decisions"
    ).fetchone()[0] == "s1"
    assert con.execute(
        "SELECT count(*) FROM decision_outcome_records WHERE source_type='live_daily_snapshot'"
    ).fetchone()[0] == 1


def test_insufficient_data_has_no_directional_performance_judgement() -> None:
    con = _con()
    con.execute("UPDATE daily_decision_states SET status='insufficient_data'")
    update_decision_outcomes(con)
    objective = con.execute(
        """SELECT DISTINCT objective_metric FROM decision_realized_outcomes o
        JOIN decision_outcome_records r USING(decision_id)
        WHERE source_type='live_daily_snapshot'"""
    ).fetchone()[0]
    assert objective == "coverage_only_no_performance_judgement"
