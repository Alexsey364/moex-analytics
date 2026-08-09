import numpy as np
import pandas as pd

from moex_analytics.training_quality.issuer_evidence import (
    EXPERIMENTS,
    HORIZONS,
    MARKET,
    SECIDS,
    _evaluate,
    _folds,
    _issuer,
)


def test_stage36_matrix_is_frozen_and_probability_remains_gated():
    assert HORIZONS == (20, 60, 120, 250)
    assert len(SECIDS) == 9
    assert set(EXPERIMENTS) == {
        "market_only", "market_sector", "market_fundamentals",
        "market_sector_fundamentals", "pooled_transfer_issuer_context",
    }


def test_stage36_walk_forward_evaluation_uses_only_prior_blocks():
    n = 1500
    signal = np.sin(np.arange(n) / 13)
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2010-01-01", periods=n, freq="D").date,
        "return_20": signal,
        "return_60": np.roll(signal, 2),
        "volatility_20": np.abs(signal) + 0.1,
        "log_turnover": 10 + signal,
        "target": signal + np.random.default_rng(36).normal(0, 0.05, n),
    })
    folds = _folds(frame)
    assert folds
    for train, test in folds:
        assert frame.loc[train, "trade_date"].max() < frame.loc[test, "trade_date"].min()
    result = _evaluate(frame, MARKET)
    assert result["rows"] > 500
    assert result["folds"] >= 3
    assert result["ba"] > result["baseline"]
    assert _issuer("SBERP") == "SBER"
