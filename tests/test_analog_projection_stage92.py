from datetime import date, timedelta

import duckdb
import pytest

from moex_analytics.analog_projection import build_analog_projections


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE scenario_research_runs(run_id VARCHAR,trajectory_run_id VARCHAR,"
        "cutoff DATE,status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute("INSERT INTO scenario_research_runs VALUES ('s','t','2026-01-30','completed',now())")
    con.execute(
        "CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)"
    )
    con.execute("INSERT INTO canonical_daily_prices VALUES ('SBERP','2026-01-30',285.0)")
    con.execute(
        """CREATE TABLE scenario_multiscale_matches(run_id VARCHAR,secid VARCHAR,method VARCHAR,
        analog_date DATE,similarity_score DOUBLE,independent BOOLEAN,combined_distance DOUBLE)"""
    )
    con.execute(
        """CREATE TABLE analog_forward_trajectories(run_id VARCHAR,secid VARCHAR,method VARCHAR,
        path_window INTEGER,analog_date DATE,forward_session INTEGER,source_trade_date DATE,
        forward_return DOUBLE)"""
    )
    for number in range(6):
        analog = date(2010 + number, 1, 10)
        con.execute(
            "INSERT INTO scenario_multiscale_matches "
            "VALUES ('s','SBERP','path_cosine',?,?,true,?)",
            [analog, 0.9 - number / 100, number],
        )
        for session in range(1, 251):
            con.execute(
                "INSERT INTO analog_forward_trajectories VALUES ('t','SBERP','path_cosine',20,?,?,?,?)",
                [analog, session, analog + timedelta(days=session), session * (number - 2) / 10000],
            )
    return con


def test_projection_starts_at_current_price_and_uses_only_real_paths() -> None:
    con = _con()
    result = build_analog_projections(con)
    assert result["production_changes"] == 0 and not result["probability_gate_changed"]
    starts = con.execute(
        "SELECT count(*),min(projected_price),max(projected_price) "
        "FROM analog_projected_paths WHERE secid='SBERP' AND relative_session=0"
    ).fetchone()
    assert starts == (6, 285.0, 285.0)
    observed = con.execute(
        """SELECT projected_price,current_price*(1+historical_return)
        FROM analog_projected_paths WHERE relative_session=20 LIMIT 1"""
    ).fetchone()
    assert observed[0] == pytest.approx(observed[1])
    assert build_analog_projections(con)["idempotent"] is True


def test_bands_horizon_table_and_medoid_are_consistent() -> None:
    con = _con()
    result = build_analog_projections(con)
    row = con.execute(
        """SELECT h.current_price,h.central_price,h.q25_price,h.q75_price,h.analog_count,
        b.median_price,h.medoid_analog_date FROM analog_projection_horizons h
        JOIN analog_projection_bands b USING(run_id,secid) WHERE h.run_id=? AND h.secid='SBERP'
        AND h.horizon=20 AND b.relative_session=20""",
        [result["run_id"]],
    ).fetchone()
    assert row[0] == 285.0 and row[1] == pytest.approx(row[5])
    assert row[2] <= row[1] <= row[3] and row[4] == 6 and row[6] is not None
    assert con.execute(
        "SELECT count(DISTINCT analog_date) FROM analog_projected_paths WHERE is_medoid"
    ).fetchone()[0] == 1


def test_missing_history_is_explicit_and_never_fabricated() -> None:
    con = _con()
    build_analog_projections(con)
    x5 = con.execute(
        "SELECT count(*),count(*) FILTER(WHERE status='insufficient_history') "
        "FROM analog_projection_horizons WHERE secid='X5'"
    ).fetchone()
    assert x5 == (9, 9)
    assert con.execute(
        "SELECT count(*) FROM analog_projected_paths WHERE secid='X5'"
    ).fetchone()[0] == 0
