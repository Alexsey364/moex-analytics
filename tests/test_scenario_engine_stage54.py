from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.scenario_engine import core
from moex_analytics.scenario_engine.core import (
    _episodes,
    _matches,
    _prehistory,
    _summaries,
    classify_scenario,
    ensure_schema,
    medoid_date,
    scenario_status,
)


def test_fixed_scenario_rules() -> None:
    assert classify_scenario(np.array([-0.06, -0.02, 0.03])) == "dip_then_recover"
    assert classify_scenario(np.array([0.01, 0.02, 0.04])) == "growth_without_deep_drawdown"
    assert classify_scenario(np.array([-0.01, -0.02, -0.04])) == "continued_decline"
    assert classify_scenario(np.array([0.01, -0.01, 0.005])) == "sideways"
    assert classify_scenario(np.array([0.08, -0.08, -0.04])) == "volatile_mixed"


def test_medoid_is_an_actual_historical_episode() -> None:
    paths = {
        pd.Timestamp("2020-01-01"): np.array([0, 0.01, 0.02]),
        pd.Timestamp("2021-01-01"): np.array([0, 0.011, 0.021]),
        pd.Timestamp("2022-01-01"): np.array([0, -0.1, 0.2]),
    }
    selected = medoid_date(paths)
    assert selected in paths
    assert selected != pd.Timestamp("2022-01-01")
    assert medoid_date({}) is None


def test_schema_labels_frequency_and_paths_honestly() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert scenario_status(con) == {"latest": None}
    summary = {row[0] for row in con.execute("DESCRIBE scenario_tree_summaries").fetchall()}
    paths = {row[0] for row in con.execute("DESCRIBE scenario_representative_paths").fetchall()}
    assert {"historical_frequency", "applicability", "reason", "immutable"} <= summary
    assert {"medoid_analog_date", "actual_historical_path", "immutable"} <= paths


def test_real_episode_pipeline_uses_observed_paths_and_prehistory_only() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE historical_analogs_v3(run_id VARCHAR,analog_type VARCHAR,"
        "secid VARCHAR,method VARCHAR,analog_date DATE,path_window INTEGER,distance DOUBLE,"
        "similarity_score DOUBLE,regime_agreement BOOLEAN,event_state_agreement BOOLEAN,"
        "feature_coverage DOUBLE,independent BOOLEAN,why_similar_json JSON,"
        "why_different_json JSON)"
    )
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute(
        "CREATE TABLE analog_forward_trajectories(run_id VARCHAR,secid VARCHAR,"
        "method VARCHAR,analog_date DATE,forward_session INTEGER,forward_return DOUBLE,"
        "source_trade_date DATE)"
    )
    con.execute(
        "CREATE TABLE analog_event_profiles(run_id VARCHAR,secid VARCHAR,method VARCHAR,"
        "analog_date DATE,event_family VARCHAR,event_type VARCHAR)"
    )
    dates = pd.bdate_range("2017-01-01", periods=1000)
    for secid in ("AAA", "IMOEX"):
        con.executemany(
            "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
            [(date, secid, 100 + idx * 0.05) for idx, date in enumerate(dates)],
        )
    analog_dates = dates[300:306]
    for analog_number, analog_date in enumerate(analog_dates):
        for window in (20, 60, 120):
            con.execute(
                "INSERT INTO historical_analogs_v3 VALUES "
                "('analog','issuer','AAA','robust',?,?,?,?,true,true,.9,true,?,?)",
                [
                    analog_date,
                    window,
                    0.1 + analog_number / 100,
                    0.8,
                    '{"breadth_balance":1,"oil_change":1,"rvi_change":1,"rgbi_change":1}',
                    "{}",
                ],
            )
        for session in range(1, 251):
            con.execute(
                "INSERT INTO analog_forward_trajectories VALUES ('trajectory','AAA','robust',?,?,?,?)",
                [
                    analog_date,
                    session,
                    session / 1000 + analog_number / 100,
                    dates[300 + analog_number + session],
                ],
            )
        con.execute(
            "INSERT INTO analog_event_profiles VALUES ('event','AAA','robust',?,'earnings','regular')",
            [analog_date],
        )
    matches = _matches(con, "analog", "scenario")
    assert len(matches) == 6
    assert set(matches.applicability) == {"medium"}
    prehistory = _prehistory(con, matches, "scenario")
    assert not prehistory.empty
    assert (pd.to_datetime(prehistory.source_trade_date) <= pd.to_datetime(prehistory.analog_date)).all()
    episodes, episode_paths, path_dates = _episodes(con, "trajectory", "event", matches, "scenario")
    assert set(episodes.horizon) == {5, 20, 60, 120, 250}
    summaries, representatives = _summaries(episodes, episode_paths, path_dates, matches, "scenario")
    assert "ready" in set(summaries.status)
    assert representatives.actual_historical_path.all()
    assert set(pd.to_datetime(representatives.medoid_analog_date)) <= set(analog_dates)
    con.execute(
        "CREATE TABLE analog_search_runs_v3(run_id VARCHAR,cutoff DATE,status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute("CREATE TABLE analog_trajectory_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE event_analog_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE current_event_contexts(run_id VARCHAR,secid VARCHAR,novelty_status VARCHAR)")
    con.execute(
        "INSERT INTO analog_search_runs_v3 VALUES ('analog','2026-08-07','completed',current_timestamp)"
    )
    con.execute("INSERT INTO analog_trajectory_runs VALUES ('trajectory','completed',current_timestamp)")
    con.execute("INSERT INTO event_analog_runs VALUES ('event','completed',current_timestamp)")
    con.execute("INSERT INTO current_event_contexts VALUES ('event','AAA','familiar')")
    result = core.run_scenario_research(con)
    assert result["status"] == "completed"
    assert result["current_rows"] == 5
    assert (
        con.execute("SELECT bool_and(actual_historical_path) FROM scenario_representative_paths").fetchone()[
            0
        ]
        is True
    )
    assert core.run_scenario_research(con)["cached"] is True
