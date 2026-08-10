import duckdb
import pandas as pd

from moex_analytics.long_horizon_ranking import core


def test_group_metrics_costs_persistence_and_clustered_ci():
    rows = []
    for date in pd.bdate_range("2020-01-01", periods=60):
        for rank in range(20):
            rows.append([date, f"S{rank}", rank / 19, rank / 100])
    frame = pd.DataFrame(rows, columns=("trade_date", "secid", "predicted_rank", "actual_return"))
    metric = core._metrics(frame, 60)
    assert metric["rank_ic"] > .99
    assert metric["spread"] > 0
    assert metric["persistence"] == 1
    assert metric["turnover"] == 0
    assert metric["ci_low"] > 0


def test_full_validation_is_frozen_idempotent_and_corrected(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE ranking_research_runs(run_id VARCHAR,cutoff DATE,status VARCHAR," 
                "finished_at TIMESTAMP)")
    con.execute("CREATE TABLE ranking_oos_predictions(run_id VARCHAR,trade_date DATE,secid VARCHAR," 
                "horizon INTEGER,predicted_rank DOUBLE,actual_return DOUBLE)")
    con.execute("INSERT INTO ranking_research_runs VALUES "
                "('rank','2026-08-07','completed',current_timestamp)")
    rows = []
    for date in pd.bdate_range("2019-01-01", periods=80):
        for rank in range(20):
            rows.append(["rank", date, f"S{rank}", 60, rank / 19, rank / 100])
    con.executemany("INSERT INTO ranking_oos_predictions VALUES (?,?,?,?,?,?)", rows)
    monkeypatch.setattr(core, "HORIZONS", (60,))
    monkeypatch.setattr(core, "PERIODS", (("2019-2021", "2019-01-01", "2021-12-31"),))
    result = core.run_long_horizon_validation(con)
    assert result["status"] == "completed"
    stored = con.execute("SELECT status,corrected_pvalue,top_bottom_spread_after_costs "
                         "FROM long_horizon_ranking_validation WHERE context_type='all'").fetchone()
    assert stored[0] == "ROBUST_RELATIVE_EDGE"
    assert 0 <= stored[1] <= 1
    assert stored[2] > 0
    assert core.run_long_horizon_validation(con)["cached"] is True
