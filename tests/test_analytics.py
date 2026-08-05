from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.analogues import (
    ANALOGUE_FEATURES,
    bootstrap_median_interval,
    sample_quality,
    select_analogues,
    summarize_outcomes,
    wilson_interval,
)
from moex_analytics.database import SCHEMA
from moex_analytics.features import calculate_all as calculate_features
from moex_analytics.features import calculate_feature_frame
from moex_analytics.forward_returns import calculate_all as calculate_forward
from moex_analytics.forward_returns import calculate_forward_frame
from moex_analytics.market_regime import calculate_all as calculate_regimes
from moex_analytics.market_regime import classify
from moex_analytics.scoring import calculate_all as calculate_scores
from moex_analytics.scoring import final_status, score_blocks, validation_period


def price_frame(count=300):
    dates = pd.date_range("2020-01-01", periods=count, freq="B")
    close = pd.Series(np.linspace(100, 200, count))
    return pd.DataFrame({"trade_date": dates, "close": close, "volume": 1000, "value": close * 1000})


def test_features_use_trading_rows_and_have_risk_momentum():
    prices = price_frame()
    result = calculate_feature_frame(prices, prices)
    last = result.iloc[-1]
    assert last["return_20"] == prices.close.iloc[-1] / prices.close.iloc[-21] - 1
    assert last["sma_200"] == pytest.approx(prices.close.tail(200).mean())
    assert last["relative_strength_60"] == pytest.approx(0, abs=1e-12)
    assert last["beta_60"] == pytest.approx(1)
    assert last["correlation_60"] == pytest.approx(1)
    changed = prices.copy()
    changed.loc[changed.index[-1], "close"] = 1
    assert calculate_feature_frame(changed).iloc[-1]["current_drawdown"] < -0.9


def test_forward_returns_and_unknown_tail():
    frame = price_frame(10)
    frame["total_return_index"] = frame["close"] / 100
    result = calculate_forward_frame(frame, (5,))
    assert result.iloc[0]["exit_date"] == frame.iloc[5]["trade_date"]
    assert result.iloc[0]["price_return"] == pytest.approx(frame.close.iloc[5] / 100 - 1)
    assert result.tail(5)["price_return"].isna().all()


def test_regimes_cover_explainable_rules():
    thresholds = {
        "stress_drawdown": -0.2,
        "stress_volatility_20": 0.35,
        "recovery_return_20": 0.08,
        "strong_trend_return_60": 0.1,
        "moderate_trend_return_60": 0.03,
        "flat_sma_distance": 0.03,
    }
    base = {
        "current_drawdown": -0.02,
        "volatility_20": 0.1,
        "return_20": 0.05,
        "return_60": 0.15,
        "price_to_sma_50": 0.1,
        "price_to_sma_200": 0.2,
        "sma_200_slope": 0.02,
    }
    assert classify(base, thresholds)[0] == "устойчивый восходящий тренд"
    assert classify({**base, "current_drawdown": -0.3}, thresholds)[0] == "стрессовый режим"
    assert classify({**base, "return_60": 0}, thresholds)[0] == "боковой рынок"
    assert classify({}, thresholds)[0] == "недостаточно данных"


def test_analogues_spacing_and_statistics():
    frame = price_frame(100)
    for index, name in enumerate(ANALOGUE_FEATURES):
        frame[name] = np.sin(np.arange(100) / (index + 2))
    found = select_analogues(frame, limit=10, min_spacing=20)
    positions = [frame.index[frame.trade_date == value][0] for value in found.trade_date]
    assert all(abs(a - b) >= 20 for i, a in enumerate(positions) for b in positions[i + 1 :])
    assert "price_return" not in ANALOGUE_FEATURES
    outcomes = pd.DataFrame(
        {
            "condition_date": pd.date_range("2000", periods=20, freq="YS"),
            "price_return": np.linspace(-0.1, 0.2, 20),
            "max_drawdown": -0.05,
        }
    )
    stats = summarize_outcomes(outcomes)
    assert stats["observations"] == 20
    assert wilson_interval(5, 10)[0] < 0.5 < wilson_interval(5, 10)[1]
    assert bootstrap_median_interval([1, 2, 3])[0] is not None
    assert sample_quality(9) == "недостаточно данных"


def test_scoring_status_and_time_splits():
    features = {
        "price_to_sma_50": 0.2,
        "price_to_sma_200": 0.3,
        "return_20": 0.1,
        "return_60": 0.2,
        "relative_strength_60": 0.2,
        "volatility_60": 0.1,
        "current_drawdown": -0.02,
        "turnover_to_mean_20": 1.2,
    }
    assert score_blocks(features, "устойчивый восходящий тренд", 0.7)["trend"] > 0
    thresholds = {
        "statistically_favorable": 0.45,
        "moderately_favorable": 0.15,
        "moderately_unfavorable": -0.15,
        "statistically_unfavorable": -0.45,
    }
    assert final_status(0.5, 20, thresholds) == "статистически благоприятные условия"
    assert final_status(0, 5, thresholds) == "недостаточно данных"
    config = {"development_end": "2015-12-31", "validation_end": "2020-12-31"}
    assert validation_period(date(2010, 1, 1), config) == "development"
    assert validation_period(date(2018, 1, 1), config) == "validation"
    assert validation_period(date(2022, 1, 1), config) == "out-of-sample"


def test_full_calculation_pipeline():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    start = date(2018, 1, 1)
    rows = []
    returns = []
    for secid, multiplier in (("IMOEX", 1.0), ("SBER", 1.2)):
        index = 1.0
        for number in range(320):
            trade_date = start + timedelta(days=number)
            close = (100 + number * 0.2) * multiplier
            rows.append(
                (
                    trade_date,
                    secid,
                    secid,
                    "TQBR",
                    close,
                    close,
                    close,
                    close,
                    close,
                    1000.0,
                    close * 1000,
                    100,
                    100,
                    pd.Timestamp.now(),
                )
            )
            index *= 1.001
            returns.append(
                (
                    trade_date,
                    secid,
                    0.001,
                    0.001,
                    0,
                    0,
                    0.001,
                    index,
                    "actual-dividends-v1",
                    pd.Timestamp.now(),
                )
            )
    con.executemany("INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO daily_returns VALUES (?,?,?,?,?,?,?,?,?,?)", returns)
    assert calculate_features(con) == 640
    assert calculate_regimes(con) == 320
    assert calculate_forward(con) == 3200
    from moex_analytics.analogues import calculate_all as calculate_analogues

    assert calculate_analogues(con) > 0
    assert calculate_scores(con) == 640
    assert con.execute("SELECT count(*) FROM forward_returns WHERE exit_date IS NULL").fetchone()[0] > 0
