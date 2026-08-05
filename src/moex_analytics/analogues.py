"""Explainable nearest historical states and robust outcome summaries."""

from __future__ import annotations

import json
import math
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from .config import load_settings

ANALOGUE_FEATURES = (
    "return_20",
    "return_60",
    "price_to_sma_50",
    "price_to_sma_200",
    "volatility_20",
    "volatility_60",
    "current_drawdown",
    "relative_strength_60",
    "range_52w_position",
)


def select_analogues(
    frame: pd.DataFrame, as_of_date=None, limit: int = 20, min_spacing: int = 20
) -> pd.DataFrame:
    """Standardize point-in-time features and greedily remove clustered dates."""
    usable = frame.dropna(subset=list(ANALOGUE_FEATURES)).sort_values("trade_date").reset_index(drop=True)
    if usable.empty:
        return usable.assign(distance=pd.Series(dtype=float), similarity=pd.Series(dtype=float))
    target_index = (
        len(usable) - 1 if as_of_date is None else usable.index[usable["trade_date"] <= as_of_date][-1]
    )
    history = usable.iloc[:target_index].copy()
    if history.empty:
        return history.assign(distance=pd.Series(dtype=float), similarity=pd.Series(dtype=float))
    values = history[list(ANALOGUE_FEATURES)].astype(float)
    means, stds = values.mean(), values.std().replace(0, 1)
    target_values = usable.loc[target_index, list(ANALOGUE_FEATURES)].astype(float)
    target = (target_values - means) / stds
    history["distance"] = np.sqrt((((values - means) / stds - target) ** 2).sum(axis=1))
    history["similarity"] = 1 / (1 + history["distance"])
    selected: list[int] = []
    for index in history.sort_values("distance").index:
        if all(abs(index - existing) >= min_spacing for existing in selected):
            selected.append(index)
        if len(selected) == limit:
            break
    return history.loc[selected].sort_values("distance").reset_index(drop=True)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total == 0:
        return None, None
    center = (successes + z * z / 2) / (total + z * z)
    half = z * math.sqrt(successes * (total - successes) / total + z * z / 4) / (total + z * z)
    return center - half, center + half


def bootstrap_median_interval(values, samples: int = 500, seed: int = 42):
    array = np.asarray(pd.Series(values).dropna(), dtype=float)
    if not len(array):
        return None, None
    rng = np.random.default_rng(seed)
    medians = np.median(rng.choice(array, size=(samples, len(array)), replace=True), axis=1)
    return tuple(np.quantile(medians, [0.025, 0.975]))


def sample_quality(total: int) -> str:
    if total < 10:
        return "недостаточно данных"
    if total < 20:
        return "очень слабая статистика"
    if total < 50:
        return "ограниченная статистика"
    return "приемлемая историческая база, но не гарантия"


def summarize_outcomes(values: pd.DataFrame) -> dict:
    returns = values["price_return"].dropna()
    total = len(returns)
    low, high = wilson_interval(int((returns > 0).sum()), total)
    median_low, median_high = bootstrap_median_interval(returns)
    years = pd.to_datetime(values.loc[returns.index, "condition_date"]).dt.year
    concentrated = bool(len(years) and years.value_counts(normalize=True).max() > 0.5)
    extreme = bool(total >= 3 and abs(returns.mean() - returns.sort_values().iloc[1:-1].mean()) > 0.02)
    return {
        "observations": total,
        "positive_frequency": float((returns > 0).mean()) if total else None,
        "positive_frequency_ci": [low, high],
        "mean": float(returns.mean()) if total else None,
        "median": float(returns.median()) if total else None,
        "median_bootstrap_ci": [median_low, median_high],
        "q25": float(returns.quantile(0.25)) if total else None,
        "q75": float(returns.quantile(0.75)) if total else None,
        "best": float(returns.max()) if total else None,
        "worst": float(returns.min()) if total else None,
        "mean_max_drawdown": float(values["max_drawdown"].mean()) if total else None,
        "median_max_drawdown": float(values["max_drawdown"].median()) if total else None,
        "quality": sample_quality(total),
        "period_concentration_warning": concentrated,
        "extreme_observation_warning": extreme,
    }


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["analytics"]
    version = cfg["calculation_version"]
    con.execute("DELETE FROM historical_analogue_results WHERE calculation_version=?", [version])
    now, total = datetime.now(), 0
    secids = [row[0] for row in con.execute("SELECT DISTINCT canonical_secid FROM daily_features").fetchall()]
    regimes = con.execute(
        "SELECT trade_date,regime FROM market_regimes WHERE calculation_version=?", [version]
    ).fetchdf()
    regimes["trade_date"] = pd.to_datetime(regimes["trade_date"])
    for secid in secids:
        raw = con.execute(
            "SELECT trade_date,features_json FROM daily_features WHERE canonical_secid=? "
            "AND calculation_version=? ORDER BY 1",
            [secid, version],
        ).fetchall()
        frame = pd.DataFrame([{"trade_date": date, **json.loads(payload)} for date, payload in raw])
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        frame = frame.merge(regimes, on="trade_date", how="left")
        found = select_analogues(frame, limit=cfg["analogue_count"], min_spacing=cfg["analogue_min_spacing"])
        if frame.empty:
            continue
        as_of = frame.iloc[-1]["trade_date"]
        for rank, row in enumerate(found.itertuples(index=False), 1):
            con.execute(
                "INSERT INTO historical_analogue_results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    as_of,
                    secid,
                    row.trade_date,
                    rank,
                    row.distance,
                    row.similarity,
                    row.regime,
                    version,
                    now,
                    cfg["source"],
                    cfg["minimum_history"],
                ],
            )
            total += 1
    return total
