import itertools

import duckdb
import numpy as np
import pandas as pd

import moex_analytics.market_memory.core as memory


def _frame(rows=900):
    rng = np.random.default_rng(25)
    index = pd.bdate_range("2018-01-01", periods=rows)
    returns = rng.normal(0.0003, 0.01, rows)
    close = pd.Series(100 * np.cumprod(1 + returns), index=index)
    return pd.DataFrame(
        {"close": close, "ret_20": close.pct_change(20), "vol_20": close.pct_change().rolling(20).std()},
        index=index,
    )


def test_transform_is_fit_on_history_only():
    history = pd.DataFrame({"a": [0.0, 1.0, 2.0], "b": [1.0, 2.0, 3.0]})
    current = pd.Series({"a": 1000.0, "b": 1000.0})
    train, _ = memory._fit_transform(history, current, "robust_euclidean")
    assert np.allclose(np.median(train, axis=0), 0.0)


def test_all_distance_spaces_are_finite_and_outcome_requires_full_horizon():
    history = pd.DataFrame({"a": np.arange(100), "b": np.sin(np.arange(100))})
    current = pd.Series({"a": 50.0, "b": 0.0})
    for method in memory.METHODS:
        distances = memory._distances(history, current, method)
        assert len(distances) == 100
        assert np.isfinite(distances).all()
    frame = _frame(100)
    assert all(np.isnan(value) for value in memory._outcomes(frame, frame.index[-2], 5))
    assert memory._similarity(pd.Series(dtype=float), 2) == "insufficient"


def test_short_history_is_not_presented_as_evidence():
    result = memory._evaluate_method(_frame(200), ["ret_20", "vol_20"], 20, "robust_euclidean")
    assert result["status"] == "insufficient_sample"


def test_independent_episodes_are_temporally_separated():
    dates = pd.bdate_range("2020-01-01", periods=200)
    selected = memory._independent_nearest(pd.Series(np.arange(200), index=dates), 20)
    ordered = sorted(selected.index)
    assert all((right - left).days >= 27 for left, right in itertools.pairwise(ordered))


def test_market_memory_run_is_immutable_and_research_only(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2025-01-01')")
    frame = _frame()
    monkeypatch.setattr(memory, "INSTRUMENTS", ("SBERP",))
    monkeypatch.setattr(memory, "HORIZONS", (20,))
    monkeypatch.setattr(memory, "METHODS", ("robust_euclidean",))
    monkeypatch.setattr(memory, "STATE_FEATURES", ("ret_20", "vol_20"))
    monkeypatch.setattr(memory, "_macro", lambda _con: pd.DataFrame())
    monkeypatch.setattr(memory, "_build_frame", lambda *_args: frame)
    monkeypatch.setattr(memory, "_add_targets", lambda raw, _horizon: raw)
    result = memory.run_market_memory(con)
    assert result["production_change"] is False
    assert con.execute("SELECT bool_and(immutable) FROM market_analog_episodes").fetchone()[0]
    assert memory.market_memory_status(con)["latest"][1] == "completed"


def test_empty_market_memory_status():
    con = duckdb.connect(":memory:")
    assert memory.market_memory_status(con) == {"latest": None, "statuses": []}
