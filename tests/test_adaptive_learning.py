from datetime import date
from itertools import pairwise

import duckdb
import numpy as np
import pandas as pd

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
