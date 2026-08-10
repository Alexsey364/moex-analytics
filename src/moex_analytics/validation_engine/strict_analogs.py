"""Frozen-library state/path analog selection and holdout replay."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

METHODS = ("state_only", "path_only", "state_path", "state_regime", "state_event",
           "state_issuer", "full_analog")
KS = (5, 10, 20, 30, 50)
MIN_EPISODES = 5


def feature_frame(prices: pd.Series) -> pd.DataFrame:
    returns = prices.pct_change(fill_method=None)
    frame = pd.DataFrame(index=prices.index)
    frame["ret20"] = prices.pct_change(20, fill_method=None)
    frame["vol20"] = returns.rolling(20).std()
    frame["drawdown60"] = prices / prices.rolling(60).max() - 1
    for lag in range(20):
        frame[f"path_{lag}"] = returns.shift(lag)
    return frame


def fit_transform_policy(features: pd.DataFrame, fit_end) -> dict:
    fit = features.loc[:fit_end].dropna()
    columns = ["ret20", "vol20", "drawdown60"]
    median = fit[columns].median()
    scale = (fit[columns].quantile(0.75) - fit[columns].quantile(0.25)).replace(0, 1)
    transformed = ((fit[columns] - median) / scale).fillna(0)
    regime = KMeans(n_clusters=2, random_state=42, n_init=10).fit(transformed)
    payload = {
        "fit_end": str(pd.Timestamp(fit_end).date()), "columns": columns,
        "median": median.to_dict(), "scale": scale.to_dict(),
        "regime_centers": regime.cluster_centers_.tolist(), "path_window": 20,
        "episode_separation": 20,
    }
    payload["hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["regime_model"] = regime
    return payload


def _state_distance(history: pd.DataFrame, current: pd.Series, policy: dict) -> pd.Series:
    columns = policy["columns"]
    median = pd.Series(policy["median"])
    scale = pd.Series(policy["scale"])
    left = ((history[columns] - median) / scale).fillna(0)
    right = ((current[columns] - median) / scale).fillna(0)
    return np.sqrt(((left - right) ** 2).mean(axis=1))


def _path_distance(history: pd.DataFrame, current: pd.Series) -> pd.Series:
    columns = [name for name in history if name.startswith("path_")]
    left = history[columns].to_numpy(float)
    right = current[columns].to_numpy(float)
    denominator = np.linalg.norm(left, axis=1) * max(np.linalg.norm(right), 1e-12)
    values = 1 - (left @ right) / np.maximum(denominator, 1e-12)
    return pd.Series(values, index=history.index)


def independent_dates(distance: pd.Series, k: int, separation: int = 20) -> list[pd.Timestamp]:
    ranked = distance.replace([np.inf, -np.inf], np.nan).dropna().sort_values(kind="mergesort")
    chronological = {date: position for position, date in enumerate(sorted(ranked.index))}
    selected = []
    for date in ranked.index:
        if all(abs(chronological[date] - chronological[prior]) >= separation for prior in selected):
            selected.append(date)
        if len(selected) >= k:
            break
    return selected


def analog_outcomes(prices: pd.Series, features: pd.DataFrame, cutoff, horizon: int, k: int,
                    method: str, policy: dict, library_end, event_dates: set) -> np.ndarray:
    cutoff = pd.Timestamp(cutoff)
    library_end = pd.Timestamp(library_end)
    if cutoff not in features.index:
        return np.array([])
    valid_dates = []
    positions = {date: position for position, date in enumerate(prices.index)}
    library_end_position = positions.get(library_end)
    if library_end_position is None:
        library_end_position = int(prices.index.searchsorted(library_end, side="right") - 1)
    for date in features.dropna().index:
        position = positions.get(date)
        if position is not None and position + horizon <= library_end_position and date < cutoff:
            valid_dates.append(date)
    history = features.loc[valid_dates]
    if len(history) < MIN_EPISODES:
        return np.array([])
    current = features.loc[cutoff]
    state = _state_distance(history, current, policy)
    path = _path_distance(history, current)
    if method == "path_only":
        distance = path
    elif method == "state_path":
        distance = state.rank(pct=True) + path.rank(pct=True)
    elif method == "state_issuer":
        distance = state
    elif method in {"state_regime", "full_analog"}:
        columns = policy["columns"]
        median = pd.Series(policy["median"])
        scale = pd.Series(policy["scale"])
        train = ((history[columns] - median) / scale).fillna(0)
        point = (((current[columns] - median) / scale).fillna(0)).to_frame().T
        labels = policy["regime_model"].predict(train)
        target = policy["regime_model"].predict(point)[0]
        distance = (state.rank(pct=True) + path.rank(pct=True)) if method == "full_analog" else state
        distance = distance.loc[labels == target]
    else:
        distance = state
    if method in {"state_event", "full_analog"}:
        target_event = cutoff in event_dates
        distance = distance.loc[[date for date in distance.index if (date in event_dates) == target_event]]
    selected = independent_dates(distance, k)
    outcomes = []
    for date in selected:
        position = positions[date]
        outcomes.append(float(prices.iloc[position + horizon] / prices.iloc[position] - 1))
    return np.asarray(outcomes, dtype=float)


def prediction_record(outcomes: np.ndarray) -> dict | None:
    if len(outcomes) < MIN_EPISODES:
        return None
    return {
        "predicted": float(np.median(outcomes)), "q10": float(np.quantile(outcomes, 0.10)),
        "q25": float(np.quantile(outcomes, 0.25)), "q75": float(np.quantile(outcomes, 0.75)),
        "q90": float(np.quantile(outcomes, 0.90)), "n": len(outcomes),
    }


def load_prices(con: Any, secid: str) -> pd.Series:
    frame = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? AND close>0 "
        "ORDER BY trade_date", [secid]
    ).df()
    return pd.Series(frame.close.to_numpy(float), index=pd.to_datetime(frame.trade_date))
