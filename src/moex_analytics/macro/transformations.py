"""Vectorized macro and intermarket transformations."""

from __future__ import annotations

import numpy as np
import pandas as pd


def market_transform(values: pd.Series, prefix: str) -> pd.DataFrame:
    result = pd.DataFrame(index=values.index)
    for window in (1, 5, 20, 60, 120):
        result[f"{prefix}_return_{window}"] = values.pct_change(window, fill_method=None)
    returns = values.pct_change(fill_method=None)
    result[f"{prefix}_volatility_20"] = returns.rolling(20).std() * np.sqrt(252)
    for window in (20, 60, 250):
        sma = values.rolling(window).mean()
        result[f"{prefix}_distance_sma_{window}"] = values / sma - 1
    return result


def rate_transform(key_rate: pd.Series, inflation: pd.Series, ruonia: pd.Series) -> pd.DataFrame:
    result = pd.DataFrame(index=key_rate.index)
    result["key_rate"] = key_rate
    for months, sessions in ((1, 21), (3, 63), (6, 126), (12, 252)):
        result[f"key_rate_change_{months}m"] = key_rate - key_rate.shift(sessions)
    result["real_rate"] = key_rate - inflation
    result["ruonia_spread"] = ruonia - key_rate
    return result


def relative_features(asset: pd.Series, sector: pd.Series, benchmark: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame(index=asset.index)
    for window in (20, 60, 120, 250):
        frame[f"asset_sector_rs_{window}"] = asset.pct_change(window) - sector.pct_change(window)
        frame[f"sector_market_rs_{window}"] = sector.pct_change(window) - benchmark.pct_change(window)
    ar = asset.pct_change()
    sr = sector.pct_change()
    frame["asset_sector_beta_60"] = ar.rolling(60).cov(sr) / sr.rolling(60).var()
    frame["asset_sector_correlation_60"] = ar.rolling(60).corr(sr)
    return frame
