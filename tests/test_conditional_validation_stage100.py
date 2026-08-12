import duckdb
import numpy as np
import pandas as pd

from moex_analytics.conditional_validation.core import (
    _apply_probability,
    _fit_probability,
    _forecast_one,
    _phase,
)
from moex_analytics.conditional_validation.schema import ensure_schema


def _toy_state(rows=160):
    index = pd.bdate_range("2020-01-01", periods=rows)
    close = pd.Series(100 + np.arange(rows) * 0.1 + np.sin(np.arange(rows) / 5), index=index)
    frame = pd.DataFrame({"close": close, "return_20": close.pct_change(20)}, index=index)
    regimes = pd.DataFrame(
        {
            "market_trend": "sideways",
            "volatility_regime": "normal",
            "rates_regime": "stable_normal",
            "stock_state": "sideways",
        },
        index=index,
    )
    distances = pd.DataFrame({"price": np.zeros(120)}, index=index[:120])
    return frame, regimes, distances


def test_replay_candidate_outcomes_are_known_before_evaluation():
    frame, regimes, distances = _toy_state()
    result = _forecast_one(
        frame,
        distances,
        regimes,
        position=120,
        horizon=20,
        variant="price_only",
        family_weights={"price": 1.0},
        threshold=45.0,
    )
    assert result["status"] == "ready"
    assert result["history_end"] == frame.index[100]
    assert result["history_end"] < frame.index[120]


def test_train_calibration_frozen_test_boundaries_do_not_overlap():
    labels = [_phase(position, 100, 0.6, 0.2) for position in (59, 60, 79, 80)]
    assert labels == ["train", "calibration", "calibration", "frozen_test"]


def test_probability_mapping_is_fitted_then_applied_without_test_labels():
    scores = pd.Series(np.linspace(0.1, 0.9, 30))
    actual = pd.Series(np.r_[np.full(15, -0.1), np.full(15, 0.1)])
    method, coefficient, intercept = _fit_probability(scores, actual, seed=42)
    calibrated = _apply_probability(pd.Series([0.2, 0.8]), method, coefficient, intercept)
    assert method == "logistic_calibration"
    assert 0 <= calibrated[0] < calibrated[1] <= 1


def test_schema_separates_calibration_test_ranges_and_stress():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    replay = {
        row[1]
        for row in con.execute("PRAGMA table_info('conditional_replay_forecasts')").fetchall()
    }
    current = {
        row[1]
        for row in con.execute("PRAGMA table_info('conditional_calibrated_forecasts')").fetchall()
    }
    mappings = {
        row[1]
        for row in con.execute("PRAGMA table_info('conditional_calibration_mappings')").fetchall()
    }
    assert {"phase", "history_end", "evaluation_regime"} <= replay
    assert {"expected60_low", "plausible80_low", "stress_low", "range_status"} <= current
    assert {"frozen_before_test", "probability_method"} <= mappings
