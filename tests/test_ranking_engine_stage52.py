from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from moex_analytics.ranking_engine import core
from moex_analytics.ranking_engine.core import (
    _metrics,
    _split_dates,
    ensure_schema,
    ranking_status,
    run_ranking_research,
)


def test_schema_and_empty_status() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert ranking_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE ranking_model_policies").fetchall()}
    assert {"policy_hash", "selection_sample", "selected", "immutable"} <= columns
    assert set(core._models()) == {"linear_ranking", "elasticnet_proxy"}


def test_temporal_boundaries_are_ordered_and_frozen() -> None:
    dates = pd.Series(pd.bdate_range("2018-01-01", periods=200))
    train_end, validation_end, holdout_start = _split_dates(dates)
    assert train_end < validation_end < holdout_start


def test_maturing_label_must_be_purged_from_prior_sample() -> None:
    boundary = pd.Timestamp("2024-06-30")
    frame = pd.DataFrame({"trade_date": pd.to_datetime(["2024-06-20", "2024-06-20"]),
                          "exit_date": pd.to_datetime(["2024-06-28", "2024-07-05"])})
    train = frame[(frame.trade_date <= boundary) & (frame.exit_date <= boundary)]
    assert len(train) == 1


def test_ranking_metrics_reward_correct_cross_sectional_order() -> None:
    rows = []
    for date in pd.bdate_range("2024-01-01", periods=25):
        for rank in range(1, 11):
            rows.append([date, rank / 10, rank / 10, rank / 100, .001])
    frame = pd.DataFrame(rows, columns=("trade_date", "predicted_score", "actual_rank",
                                       "actual_return", "imoex_return"))
    metrics = _metrics(frame)
    assert metrics["rank_ic"] > .99
    assert metrics["top_quintile_spread"] > 0
    assert metrics["top_k_hit_rate"] == 1


def test_split_rejects_short_history() -> None:
    with np.testing.assert_raises(ValueError):
        _split_dates(pd.Series(pd.bdate_range("2024-01-01", periods=20)))


def test_research_run_freezes_validation_policy_before_holdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
                "total_return_index DOUBLE,calculation_version VARCHAR)")
    con.execute("CREATE TABLE predictive_target_runs(run_id VARCHAR,cutoff DATE,input_hash VARCHAR,"
                "status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE predictive_target_observations(run_id VARCHAR,trade_date DATE,"
                "exit_date DATE,secid VARCHAR,horizon INTEGER,total_return DOUBLE,"
                "percentile_rank DOUBLE,excess_imoex DOUBLE)")
    dates = pd.bdate_range("2018-01-01", periods=300)
    levels: dict[str, list[float]] = {}
    for number, secid in enumerate(("AAA", "BBB", "CCC", "IMOEX")):
        values = 100 * np.cumprod(1 + .0002 * (number + 1) + np.sin(np.arange(300) / 9) * .001)
        levels[secid] = values.tolist()
        con.executemany("INSERT INTO daily_returns VALUES (?,?,?,?)",
                        [[date, secid, float(value), "actual-dividends-v1"]
                         for date, value in zip(dates, values, strict=True)])
    con.execute("INSERT INTO predictive_target_runs VALUES "
                "('target-run',?,'frozen-input','completed',current_timestamp)", [dates[-1]])
    labels = []
    for idx, date in enumerate(dates[:-5]):
        returns = {secid: levels[secid][idx + 5] / levels[secid][idx] - 1
                   for secid in ("AAA", "BBB", "CCC")}
        ranks = pd.Series(returns).rank(pct=True)
        benchmark = levels["IMOEX"][idx + 5] / levels["IMOEX"][idx] - 1
        for secid, value in returns.items():
            labels.append(["target-run", date, dates[idx + 5], secid, 5, value,
                           float(ranks[secid]), value - benchmark])
    con.executemany("INSERT INTO predictive_target_observations VALUES (?,?,?,?,?,?,?,?)", labels)
    monkeypatch.setattr(core, "_models", lambda: {"linear_ranking": Ridge(alpha=1.0)})
    result = run_ranking_research(con)
    assert result["status"] == "completed"
    assert result["predictions"] > 0
    violations = con.execute(
        "SELECT count(*) FROM ranking_oos_predictions p "
        "JOIN ranking_research_runs r USING(run_id) "
        "WHERE p.trade_date<r.holdout_start OR p.history_end<>r.validation_end"
    ).fetchone()[0]
    assert violations == 0
    assert con.execute("SELECT sum(selected::int),bool_and(selection_sample='validation_only') "
                       "FROM ranking_model_policies").fetchone() == (1, True)
    assert run_ranking_research(con)["cached"] is True
    turnover = con.execute("SELECT turnover FROM ranking_topk_backtests LIMIT 1").fetchone()[0]
    assert 0 <= turnover <= 1
