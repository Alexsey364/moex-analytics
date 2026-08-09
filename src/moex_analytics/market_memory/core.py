"""Leakage-safe historical market-state analog research."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from sklearn.decomposition import PCA

from moex_analytics.adaptive_learning.core import HORIZONS, INSTRUMENTS, _add_targets, _build_frame, _macro

from .schema import DDL

VERSION = "market-memory-v1"
METHODS = ("robust_euclidean", "mahalanobis", "knn", "pca")
STATE_FEATURES = (
    "ret_20",
    "vol_20",
    "drawdown_252",
    "relative_strength_20",
    "breadth_adv_ratio",
    "breadth_dispersion",
    "liquidity_turnover",
    "zc_slope",
    "ruonia",
    "usdrub",
)
MIN_TRAIN = 252
MIN_ANALOGS = 8


def ensure_schema(con) -> None:
    con.execute(DDL)


def _fit_transform(history: pd.DataFrame, current: pd.Series, method: str) -> tuple[np.ndarray, np.ndarray]:
    """Fit every transform on historical rows only; current never affects statistics."""
    history = history.astype(float)
    current = current.astype(float)
    median = history.median()
    filled = history.fillna(median)
    now = current.fillna(median)
    scale = (filled.quantile(0.75) - filled.quantile(0.25)).replace(0, 1.0).fillna(1.0)
    train = ((filled - median) / scale).to_numpy(float)
    point = ((now - median) / scale).to_numpy(float)
    if method == "pca":
        model = PCA(n_components=min(5, train.shape[1], max(1, train.shape[0] - 1)))
        return model.fit_transform(train), model.transform(point.reshape(1, -1))[0]
    return train, point


def _distances(history: pd.DataFrame, current: pd.Series, method: str) -> pd.Series:
    train, point = _fit_transform(history, current, method)
    delta = train - point
    if method == "mahalanobis":
        precision = LedoitWolf().fit(train).precision_
        values = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", delta, precision, delta), 0))
    else:
        values = np.sqrt(np.mean(delta**2, axis=1))
    return pd.Series(values, index=history.index)


def _independent_nearest(distances: pd.Series, separation: int, limit: int = 20) -> pd.Series:
    selected: list[pd.Timestamp] = []
    for date in distances.sort_values().index:
        if all(abs((date - prior).days) >= int(separation * 1.35) for prior in selected):
            selected.append(date)
        if len(selected) == limit:
            break
    return distances.loc[selected].sort_values()


def _outcomes(frame: pd.DataFrame, date: pd.Timestamp, horizon: int) -> tuple[float, float, float]:
    location = frame.index.get_loc(date)
    future = frame.iloc[location + 1 : location + horizon + 1]
    if len(future) < horizon:
        return np.nan, np.nan, np.nan
    start = float(frame.loc[date, "close"])
    path = future.close.astype(float) / start - 1
    return float(path.iloc[-1]), float(path.min()), float(path.max())


def _similarity(nearest: pd.Series, dimension: int) -> str:
    if nearest.empty:
        return "insufficient"
    threshold = float(nearest.median()) / max(np.sqrt(dimension), 1)
    if threshold <= 0.65:
        return "high"
    if threshold <= 1.25:
        return "medium"
    return "low"


def _evaluate_method(frame: pd.DataFrame, features: list[str], horizon: int, method: str) -> dict:
    eligible = frame.dropna(subset=["close"])
    cutoff_pos = len(eligible) - horizon - 1
    if cutoff_pos < MIN_TRAIN:
        return {"episodes": [], "status": "insufficient_sample", "reason": "short history"}
    cutoff = eligible.index[cutoff_pos]
    history = eligible.loc[:cutoff].iloc[:-horizon]
    usable = history[features].dropna(thresh=max(2, len(features) // 2))
    if len(usable) < MIN_TRAIN:
        return {"episodes": [], "status": "insufficient_sample", "reason": "sparse state history"}
    current = eligible.loc[cutoff, features]
    distances = _distances(usable, current, method)
    nearest = _independent_nearest(distances, max(20, horizon))
    episodes = []
    for rank, (date, distance) in enumerate(nearest.items(), 1):
        outcome, drawdown, mfe = _outcomes(eligible, date, horizon)
        if np.isfinite(outcome):
            episodes.append((date, float(distance), outcome, drawdown, mfe, rank))
    similarity = _similarity(nearest, len(features))
    if len(episodes) < MIN_ANALOGS:
        return {
            "episodes": episodes,
            "cutoff": cutoff,
            "similarity": similarity,
            "status": "insufficient_sample",
            "reason": "fewer than independent minimum analogs",
        }
    values = np.array([row[2] for row in episodes])
    baseline = float(history.close.pct_change(horizon).median())
    actual, _, _ = _outcomes(eligible, cutoff, horizon)
    analog_error = abs(float(np.median(values)) - actual)
    baseline_error = abs(baseline - actual)
    return {
        "episodes": episodes,
        "cutoff": cutoff,
        "similarity": similarity,
        "status": "experimental" if analog_error < baseline_error else "no_oos_value",
        "reason": "single frozen pseudo-OOS cutoff; research-only",
        "values": values,
        "actual": actual,
        "oos_mae": analog_error,
        "baseline_mae": baseline_error,
        "value_add": baseline_error - analog_error,
    }


def run_market_memory(con) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    state = con.execute("SELECT count(*),max(trade_date) FROM canonical_daily_prices").fetchone()
    dataset = hashlib.sha256(repr((state, VERSION)).encode()).hexdigest()[:20]
    run_id = hashlib.sha256(f"{dataset}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO market_memory_runs VALUES (?,?,current_timestamp,'running',?,?,NULL,0,?)",
        [run_id, dataset, json.dumps(INSTRUMENTS), json.dumps(HORIZONS), "research only"],
    )
    macro = _macro(con)
    analog_count = 0
    for instrument in INSTRUMENTS:
        raw = _build_frame(con, instrument, macro)
        for horizon in HORIZONS:
            frame = _add_targets(raw, horizon)
            features = [
                name for name in STATE_FEATURES if name in frame and frame[name].notna().sum() >= MIN_TRAIN
            ]
            for method in METHODS:
                result = _evaluate_method(frame, features, horizon, method)
                cutoff = result.get("cutoff", frame.index.max())
                similarity = result.get("similarity", "insufficient")
                episodes = result["episodes"]
                for date, distance, outcome, drawdown, mfe, rank in episodes:
                    con.execute(
                        """INSERT INTO market_analog_episodes VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,TRUE,TRUE,current_timestamp)""",
                        [
                            run_id,
                            instrument,
                            horizon,
                            method,
                            cutoff,
                            date,
                            distance,
                            similarity,
                            outcome,
                            drawdown,
                            mfe,
                            rank,
                        ],
                    )
                    analog_count += 1
                values = result.get("values", np.array([]))
                quantiles = np.quantile(values, [0.1, 0.25, 0.5, 0.75, 0.9]) if len(values) else [None] * 5
                con.execute(
                    "INSERT INTO market_analog_scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        run_id,
                        instrument,
                        horizon,
                        method,
                        cutoff,
                        len(episodes),
                        similarity,
                        quantiles[2],
                        quantiles[0],
                        quantiles[1],
                        quantiles[3],
                        quantiles[4],
                        float(np.mean(values > 0)) if len(values) else None,
                        float(np.median([row[3] for row in episodes])) if episodes else None,
                        float(np.median([row[4] for row in episodes])) if episodes else None,
                        result.get("oos_mae"),
                        result.get("baseline_mae"),
                        result.get("value_add"),
                        result["status"],
                        result["reason"],
                    ],
                )
    runtime = time.perf_counter() - started
    con.execute(
        "UPDATE market_memory_runs SET status='completed',runtime_seconds=?,analogs=? WHERE run_id=?",
        [runtime, analog_count, run_id],
    )
    return {"run_id": run_id, "analogs": analog_count, "runtime_seconds": runtime, "production_change": False}


def market_memory_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,runtime_seconds,analogs FROM market_memory_runs
        ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not latest:
        return {"latest": None, "statuses": []}
    statuses = con.execute(
        "SELECT status,count(*) FROM market_analog_scorecards WHERE run_id=? GROUP BY 1 ORDER BY 1",
        [latest[0]],
    ).fetchall()
    return {"latest": latest, "statuses": statuses}
