from datetime import date
from itertools import pairwise

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge

import moex_analytics.adaptive_learning.core as adaptive
from moex_analytics.adaptive_learning.core import _add_targets, ensure_schema, temporal_folds
from moex_analytics.portfolio_research.daily_governance import run_daily_update


def test_walk_forward_has_embargo_and_no_lookahead():
    for horizon in (5, 20, 60, 120):
        folds = temporal_folds(2400, horizon)
        assert folds
        for fold in folds:
            assert fold["embargo"] >= horizon
            assert fold["train"].max() + horizon < fold["validation"].min()
            assert fold["validation"].max() + horizon < fold["test"].min()
            assert np.all(np.diff(fold["train"]) == 1)


def test_targets_are_forward_only_and_neutral_is_separate():
    dates = pd.date_range("2020-01-01", periods=300, freq="D")
    close = np.linspace(100, 130, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "vol_20": 0.2,
            "moex_imoex": np.linspace(1000, 1200, len(dates)),
        },
        index=dates,
    )
    result = _add_targets(frame, 20)
    assert result.iloc[0].forward_return == close[20] / close[0] - 1
    assert len(result) == 280
    assert set(result.direction.unique()) <= {-1, 0, 1}


def test_registry_is_immutable_and_cannot_auto_promote():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE adaptive_model_registry").fetchall()}
    assert {"immutable", "automatic_promotion", "training_end"} <= columns
    con.execute(
        """INSERT INTO adaptive_model_registry VALUES
        ('x','r','m','v','per_instrument','SBERP',20,'[]',current_date,
        '{}','{}','{}','{}','candidate',current_timestamp,TRUE,FALSE)"""
    )
    assert con.execute(
        "SELECT immutable AND NOT automatic_promotion FROM adaptive_model_registry"
    ).fetchone()[0]


def test_quick_daily_does_not_retrain(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES (?)", [date.today()])
    result = run_daily_update(con, now=pd.Timestamp(date.today()))
    assert all(step["dataset"] != "adaptive_learning" for step in result["steps"])
    assert result["http_requests"] == 0


def test_leave_one_period_shape_is_stable():
    folds = temporal_folds(1800, 60)
    test_sets = [set(fold["test"]) for fold in folds]
    assert all(left.isdisjoint(right) for left, right in pairwise(test_sets))


def test_challenger_fit_save_and_probability_gate(monkeypatch):
    """Exercise the complete fold persistence and result governance path."""
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute(
        """CREATE TABLE forecast_registry(
        forecast_id VARCHAR,secid VARCHAR,horizon_sessions INTEGER)"""
    )
    con.execute(
        """CREATE TABLE forecast_outcomes(
        forecast_id VARCHAR,outcome_status VARCHAR,direction_correct BOOLEAN)"""
    )
    monkeypatch.setattr(
        adaptive,
        "_specs",
        lambda seed=22: {"logistic_l2": (LogisticRegression(max_iter=200), Ridge(alpha=1.0))},
    )
    dates = pd.bdate_range("2020-01-01", periods=900)
    trend = np.linspace(100, 180, len(dates))
    close = trend + np.sin(np.arange(len(dates)) / 4) * 5
    frame = pd.DataFrame(
        {
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "moex_imoex": np.linspace(2500, 3500, len(dates)),
            "regime": np.where(np.arange(len(dates)) % 3, "normal", "stress"),
            "ret_1": pd.Series(close, index=dates).pct_change().fillna(0),
            "vol_5": pd.Series(close, index=dates).pct_change().rolling(5).std().fillna(0),
            "vol_20": pd.Series(close, index=dates).pct_change().rolling(20).std().fillna(0),
        },
        index=dates,
    )
    fitted = adaptive._fit_instrument(
        con, "run", "SBERP", 5, frame, ["ret_1", "vol_5"]
    )
    assert len(fitted) == 1
    result, baseline_y, baseline_p = fitted[0]
    saved = adaptive._save_result(
        con,
        "run",
        "SBERP",
        5,
        "per_instrument",
        result,
        baseline_y,
        baseline_p,
        ["ret_1", "vol_5"],
    )
    assert saved["probability_allowed"] is False
    assert con.execute("SELECT count(*) FROM adaptive_fold_predictions").fetchone()[0] > 0
    assert con.execute("SELECT automatic_promotion FROM adaptive_model_registry").fetchone()[0] is False


def test_point_in_time_feature_frame_and_cross_sectional_ranking(monkeypatch):
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute(
        """CREATE TABLE canonical_daily_prices(
        trade_date DATE,canonical_secid VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,
        close DOUBLE,volume DOUBLE,value DOUBLE,number_of_trades DOUBLE)"""
    )
    con.execute(
        """CREATE TABLE market_breadth_daily(
        trade_date DATE,advancing INTEGER,declining INTEGER,tradable_count INTEGER,
        advancing_turnover DOUBLE,declining_turnover DOUBLE,total_turnover DOUBLE,
        return_dispersion DOUBLE)"""
    )
    con.execute(
        """CREATE TABLE macro_observations(
        observation_date DATE,series_id VARCHAR,value DOUBLE,available_from TIMESTAMP)"""
    )
    dates = pd.bdate_range("2021-01-01", periods=700)
    for idx, day in enumerate(dates):
        price = 100 + idx * 0.1 + np.sin(idx / 4) * 5
        con.execute(
            "INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?,?,?,?,?)",
            [day.date(), "SBERP", price, price * 1.01, price * 0.99, price, 1000 + idx, 1e6 + idx, 100 + idx],
        )
        con.execute(
            "INSERT INTO market_breadth_daily VALUES (?,?,?,?,?,?,?,?)",
            [day.date(), 20, 10, 35, 2e6, 1e6, 4e6, 0.01],
        )
        for series, value in (("moex_imoex", 3000 + idx), ("moex_rvi", 20 + idx / 100)):
            con.execute(
                "INSERT INTO macro_observations VALUES (?,?,?,?)",
                [day.date(), series, value, pd.Timestamp(day)],
            )
    macro = adaptive._macro(con)
    frame = adaptive._build_frame(con, "SBERP", macro)
    assert {"relative_20", "regime", "momentum_x_volatility"} <= set(frame.columns)
    frame = frame.fillna(0)
    ranking_frames = {
        secid: frame.assign(close=frame.close * multiplier)
        for secid, multiplier in (("SBERP", 1.0), ("LKOH", 1.1), ("MTSS", 0.9))
    }
    adaptive._ranking(con, "rank-run", 5, ranking_frames)
    assert con.execute("SELECT count(*) FROM adaptive_ranking_results").fetchone()[0] == 2
    monkeypatch.setattr(
        adaptive,
        "_specs",
        lambda seed=22: {
            "logistic_l2": (LogisticRegression(max_iter=200), Ridge(alpha=1.0)),
            "extra_trees": (LogisticRegression(max_iter=200), Ridge(alpha=1.0)),
        },
    )
    pooled = adaptive._fit_pooled_loo(
        con,
        "pooled-run",
        "SBERP",
        5,
        ranking_frames,
        ["ret_1", "vol_20"],
    )
    assert {result.model for result, _, _ in pooled} == {
        "pooled_logistic_l2",
        "pooled_extra_trees",
    }
    ablation_features = [
        "ret_1",
        "breadth_balance",
        "turnover_log",
        "rusfar_change",
        "cny_change",
        "vol_20",
    ]
    adaptive._ablate_families(
        con, "ablation-run", "SBERP", 5, frame, ablation_features
    )
    assert con.execute("SELECT count(*) FROM adaptive_feature_ablation").fetchone()[0] == 6
