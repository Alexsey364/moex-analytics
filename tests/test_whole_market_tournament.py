import duckdb
import numpy as np

from moex_analytics.whole_market_tournament.core import (
    bh_adjust,
    paired_evidence,
    run_whole_market_tournament,
)


def test_bootstrap_and_permutation_are_deterministic() -> None:
    deltas = np.linspace(0.01, 0.05, 100)
    first = paired_evidence(deltas, 200)
    assert first == paired_evidence(deltas, 200)
    assert first[0] > 0
    assert first[2] < 0.05


def test_bh_adjustment_is_monotone_and_preserves_missing() -> None:
    adjusted = bh_adjust([0.01, 0.04, None, 0.02])
    assert adjusted[2] is None
    assert adjusted[0] <= adjusted[3] <= adjusted[1]


def test_short_or_non_finite_evidence_is_not_promotable() -> None:
    low, high, p_value = paired_evidence(np.ones(19))
    assert np.isnan(low)
    assert np.isnan(high)
    assert np.isnan(p_value)
    assert all(np.isnan(value) for value in paired_evidence(np.array([np.nan] * 30)))


def test_tournament_requires_unimplemented_fold_gate_for_shadow_status() -> None:
    con = duckdb.connect(":memory:")
    for table in (
        "market_forecast_runs",
        "sector_rotation_runs",
        "conditioned_stock_runs",
        "market_analog_fusion_runs",
        "whole_market_state_runs",
    ):
        con.execute(f"CREATE TABLE {table}(run_id VARCHAR,created_at TIMESTAMP)")
        con.execute(f"INSERT INTO {table} VALUES ('source','2026-01-01')")
    con.execute(
        """CREATE TABLE market_forecast_scorecards(run_id VARCHAR,horizon INTEGER,model VARCHAR,
        observations BIGINT,balanced_accuracy DOUBLE,baseline_balanced_accuracy DOUBLE,
        improvement_vs_baseline DOUBLE,sample VARCHAR)"""
    )
    con.execute(
        "INSERT INTO market_forecast_scorecards VALUES "
        "('source',5,'m',100,.4,.333,.067,'frozen_holdout')"
    )
    con.execute(
        """CREATE TABLE sector_rotation_scorecards(run_id VARCHAR,horizon INTEGER,
        observations BIGINT,rank_ic DOUBLE,baseline_rank_ic DOUBLE,sample VARCHAR)"""
    )
    con.execute("INSERT INTO sector_rotation_scorecards VALUES ('source',5,100,.05,0,'frozen_holdout')")
    con.execute(
        """CREATE TABLE conditioned_stock_scorecards(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
        feature_block VARCHAR,observations BIGINT,model_mae DOUBLE,baseline_mae DOUBLE,improvement DOUBLE)"""
    )
    con.execute("INSERT INTO conditioned_stock_scorecards VALUES ('source','X5',5,'market',100,.09,.1,.01)")
    con.execute(
        """CREATE TABLE market_analog_fusion_scorecards(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
        observations BIGINT,fused_mae DOUBLE,analog_mae DOUBLE,improvement DOUBLE)"""
    )
    con.execute("INSERT INTO market_analog_fusion_scorecards VALUES ('source','X5',5,100,.09,.1,.01)")
    con.execute(
        """CREATE TABLE market_analog_fusion_oos(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
        cutoff DATE,analog_error DOUBLE,fused_error DOUBLE)"""
    )
    con.execute(
        """CREATE TABLE whole_market_state_daily(run_id VARCHAR,trade_date DATE,
        market_state_label VARCHAR)"""
    )
    rows = []
    states = []
    for index in range(100):
        day = f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}"
        rows.append(("source", "X5", 5, day, 0.03, 0.01))
        states.append(("source", day, "stress" if index < 50 else "trend_up"))
    con.executemany("INSERT INTO market_analog_fusion_oos VALUES (?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO whole_market_state_daily VALUES (?,?,?)", states)
    result = run_whole_market_tournament(con)
    assert result["shadow_candidates"] == 1
    assert result["probability_gate_changed"] is False
    assert run_whole_market_tournament(con)["idempotent"] is True
    candidate = con.execute(
        "SELECT status FROM whole_market_tournament_entries WHERE scope='fusion'"
    ).fetchone()[0]
    assert candidate == "shadow_candidate"
