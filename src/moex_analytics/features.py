"""Point-in-time market factors calculated over trading-session windows."""

from __future__ import annotations

import json
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from .config import load_settings


def _drawdown(values: pd.Series, window: int) -> pd.Series:
    return values.rolling(window, min_periods=window).apply(
        lambda x: float(np.min(x / np.maximum.accumulate(x) - 1)), raw=True
    )


def calculate_feature_frame(prices: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return factors using only rows at or before each output date."""
    frame = prices.sort_values("trade_date").copy()
    close = frame["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    for window in (1, 5, 20, 60, 120, 250):
        frame[f"return_{window}"] = close.pct_change(window, fill_method=None)
    for window in (20, 50, 100, 200):
        sma = close.rolling(window, min_periods=window).mean()
        frame[f"sma_{window}"] = sma
        frame[f"price_to_sma_{window}"] = close / sma - 1
    for window in (20, 50, 200):
        frame[f"sma_{window}_slope"] = frame[f"sma_{window}"].pct_change(5, fill_method=None)
    frame["sma_50_above_200"] = (frame["sma_50"] > frame["sma_200"]).astype(float)
    above = close >= frame["sma_200"]
    groups = above.ne(above.shift()).cumsum()
    frame["days_vs_sma_200"] = above.groupby(groups).cumcount().add(1).where(above, -1)
    high_52 = close.rolling(250, min_periods=250).max()
    low_52 = close.rolling(250, min_periods=250).min()
    frame["range_52w_position"] = (close - low_52) / (high_52 - low_52)
    frame["distance_to_52w_high"] = close / high_52 - 1
    frame["distance_to_52w_low"] = close / low_52 - 1
    annual = float(load_settings()["analytics"]["annualization_sessions"])
    for window in (20, 60, 250):
        frame[f"volatility_{window}"] = returns.rolling(window, min_periods=window).std() * np.sqrt(annual)
    frame["downside_volatility_60"] = returns.where(returns < 0).rolling(60, min_periods=20).std() * np.sqrt(
        annual
    )
    frame["max_drawdown_60"] = _drawdown(close, 60)
    frame["max_drawdown_250"] = _drawdown(close, 250)
    frame["current_drawdown"] = close / close.cummax() - 1
    value = frame.get("value", frame.get("volume", 0) * close).fillna(0)
    for window in (20, 60):
        frame[f"turnover_mean_{window}"] = value.rolling(window, min_periods=window).mean()
        frame[f"turnover_median_{window}"] = value.rolling(window, min_periods=window).median()
    frame["turnover_to_mean_20"] = value / frame["turnover_mean_20"]
    frame["zero_volume_days_20"] = (
        (frame.get("volume", pd.Series(0, index=frame.index)).fillna(0) <= 0).rolling(20).sum()
    )
    if benchmark is not None:
        bench = benchmark[["trade_date", "close"]].sort_values("trade_date").copy()
        bench["benchmark_return"] = bench["close"].pct_change(fill_method=None)
        bench = bench.drop(columns="close")
        frame = frame.merge(bench, on="trade_date", how="left")
        for window in (20, 60, 120, 250):
            bench_level = (
                (1 + frame["benchmark_return"]).rolling(window, min_periods=window).apply(np.prod, raw=True)
            )
            frame[f"relative_strength_{window}"] = (1 + frame[f"return_{window}"]) / bench_level - 1
        for window in (60, 250):
            cov = returns.rolling(window, min_periods=window).cov(frame["benchmark_return"])
            var = frame["benchmark_return"].rolling(window, min_periods=window).var()
            frame[f"beta_{window}"] = cov / var
            frame[f"correlation_{window}"] = returns.rolling(window, min_periods=window).corr(
                frame["benchmark_return"]
            )
    return frame.replace([np.inf, -np.inf], np.nan)


def _add_dividend_features(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame, secid: str) -> None:
    dividends = con.execute(
        "SELECT registry_close_date, dividend_per_share FROM dividends WHERE canonical_secid=? ORDER BY 1",
        [secid],
    ).fetchdf()
    frame["dividends_12m"] = 0.0
    frame["dividend_payments_12m"] = 0.0
    frame["dividend_change"] = np.nan
    if dividends.empty:
        frame["trailing_dividend_yield"] = 0.0
        return
    dates = pd.to_datetime(dividends["registry_close_date"])
    amounts = dividends["dividend_per_share"].astype(float).to_numpy()
    previous = np.r_[np.nan, amounts[:-1]]
    for index, trade_date in frame["trade_date"].items():
        end = pd.Timestamp(trade_date)
        mask = (dates <= end) & (dates > end - pd.DateOffset(years=1))
        frame.at[index, "dividends_12m"] = amounts[mask].sum()
        frame.at[index, "dividend_payments_12m"] = int(mask.sum())
        known = np.flatnonzero(dates <= end)
        if len(known) and not np.isnan(previous[known[-1]]) and previous[known[-1]] != 0:
            frame.at[index, "dividend_change"] = amounts[known[-1]] / previous[known[-1]] - 1
    frame["trailing_dividend_yield"] = frame["dividends_12m"] / frame["close"]


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    settings = load_settings()["analytics"]
    version, source = settings["calculation_version"], settings["source"]
    benchmark = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='IMOEX' ORDER BY 1"
    ).fetchdf()
    con.execute("DELETE FROM daily_features WHERE calculation_version=?", [version])
    total = 0
    secids = [
        row[0]
        for row in con.execute("SELECT DISTINCT canonical_secid FROM canonical_daily_prices").fetchall()
    ]
    now = datetime.now()
    for secid in secids:
        prices = con.execute(
            "SELECT trade_date,close,volume,value FROM canonical_daily_prices "
            "WHERE canonical_secid=? ORDER BY 1",
            [secid],
        ).fetchdf()
        frame = calculate_feature_frame(prices, benchmark)
        _add_dividend_features(con, frame, secid)
        excluded = {"trade_date", "canonical_secid", "benchmark_return"}
        records = []
        for _, row in frame.iterrows():
            payload = {
                key: (None if pd.isna(value) else float(value))
                for key, value in row.items()
                if key not in excluded
            }
            records.append(
                [
                    row["trade_date"],
                    secid,
                    json.dumps(payload),
                    version,
                    now,
                    source,
                    settings["minimum_history"],
                ]
            )
        incoming = pd.DataFrame(
            records,
            columns=[
                "trade_date",
                "canonical_secid",
                "features_json",
                "calculation_version",
                "calculated_at",
                "source",
                "minimum_history",
            ],
        )
        con.register("incoming_features", incoming)
        con.execute("INSERT INTO daily_features SELECT * FROM incoming_features")
        con.unregister("incoming_features")
        total += len(records)
    return total
