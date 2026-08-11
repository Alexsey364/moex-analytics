import duckdb
import numpy as np
import pandas as pd

from moex_analytics.regime_conditioning.core import classify_regimes, regime_compatibility
from moex_analytics.regime_conditioning.schema import ensure_schema


def _frame(rows=180):
    index = pd.bdate_range("2020-01-01", periods=rows)
    trend = np.linspace(-0.08, 0.12, rows)
    return pd.DataFrame(
        {
            "market_return_60": trend,
            "market_drawdown": np.minimum(trend, 0),
            "market_volatility_20": np.linspace(0.12, 0.32, rows),
            "return_20": trend / 2,
            "return_60": trend,
            "drawdown": np.minimum(trend / 2, 0),
            "sma20_distance": trend / 3,
            "rate": np.r_[np.full(100, 10.0), np.linspace(10, 12, rows - 100)],
        },
        index=index,
    )


def test_regime_is_deterministic_and_prefix_has_no_future_knowledge():
    frame = _frame()
    full = classify_regimes(frame, ("rate",))
    prefix = classify_regimes(frame.iloc[:120], ("rate",))
    pd.testing.assert_frame_equal(full.iloc[:120], prefix)
    pd.testing.assert_frame_equal(full, classify_regimes(frame, ("rate",)))


def test_compatibility_supports_adjacent_but_severely_penalizes_crisis():
    current = {
        "market_trend": "weak_bull",
        "volatility_regime": "normal",
        "rates_regime": "stable_high",
        "stock_state": "uptrend",
    }
    adjacent = current | {"volatility_regime": "elevated"}
    crisis = current | {"market_trend": "crisis", "volatility_regime": "extreme"}
    assert regime_compatibility(current, current) == 1
    assert 0.7 <= regime_compatibility(current, adjacent) < 1
    assert regime_compatibility(current, crisis) <= 0.25


def test_stage97_schema_separates_center_alternative_and_transition_risk():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    analog_columns = {
        row[1] for row in con.execute("PRAGMA table_info('regime_conditioned_analogs')").fetchall()
    }
    transition_columns = {
        row[1] for row in con.execute("PRAGMA table_info('conditional_regime_transitions')").fetchall()
    }
    assert {"scenario_role", "eligible_for_center", "regime_compatibility"} <= analog_columns
    assert {"transition_frequency", "crisis_frequency", "matched_states"} <= transition_columns
