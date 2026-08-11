"""Stage 87 decision-state analogs without model-output or future leakage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS

from .schema import ensure_schema

VERSION = "predictive-state-similarity-v2"
ANALOG_TYPES = ("path", "state", "combined")
FEATURES = (
    "market_return_20",
    "market_drawdown",
    "market_volatility",
    "stock_return_20",
    "stock_volatility",
    "stock_drawdown",
)
MIN_HISTORY = 252
MATCHES = 10


def _panel(con: duckdb.DuckDBPyConnection, secid: str, cutoff: Any) -> pd.DataFrame:
    market = con.execute(
        """SELECT trade_date,imoex_close,return_20 market_return_20,drawdown market_drawdown,
        realized_vol20 market_volatility FROM whole_market_state_daily
        WHERE run_id=(SELECT run_id FROM whole_market_state_runs WHERE cutoff<=?
        AND status='completed' ORDER BY cutoff DESC LIMIT 1) AND trade_date<=? ORDER BY trade_date""",
        [cutoff, cutoff],
    ).df()
    prices = con.execute(
        """SELECT trade_date,close FROM canonical_daily_prices
        WHERE canonical_secid=? AND trade_date<=? AND close>0 ORDER BY trade_date""",
        [secid, cutoff],
    ).df()
    prices["stock_return_20"] = prices.close.pct_change(20)
    prices["stock_volatility"] = prices.close.pct_change().rolling(20).std() * np.sqrt(252)
    prices["stock_drawdown"] = prices.close / prices.close.rolling(252, min_periods=60).max() - 1
    frame = prices.merge(market, on="trade_date", how="inner").set_index("trade_date").sort_index()
    return frame.dropna(subset=list(FEATURES))


def _state_distances(frame: pd.DataFrame, position: int, train_end: int) -> pd.Series:
    history = frame.iloc[:train_end]
    median = history[list(FEATURES)].median()
    scale = (history[list(FEATURES)].quantile(0.75) - history[list(FEATURES)].quantile(0.25)).replace(0, 1)
    train = (history[list(FEATURES)] - median) / scale
    point = (frame.iloc[position][list(FEATURES)] - median) / scale
    return np.sqrt(((train - point) ** 2).mean(axis=1))


def _path_distances(frame: pd.DataFrame, position: int, train_end: int, window: int = 20) -> pd.Series:
    current = frame.close.iloc[position - window : position + 1].to_numpy(float)
    current = current / current[-1]
    values: dict[Any, float] = {}
    for candidate in range(window, train_end):
        path = frame.close.iloc[candidate - window : candidate + 1].to_numpy(float)
        path = path / path[-1]
        values[frame.index[candidate]] = float(np.sqrt(np.mean((path - current) ** 2)))
    return pd.Series(values, dtype=float)


def _combined(path: pd.Series, state: pd.Series) -> pd.Series:
    common = path.index.intersection(state.index)
    if common.empty:
        return pd.Series(dtype=float)
    return path.loc[common].rank(pct=True) / 2 + state.loc[common].rank(pct=True) / 2


def _independent(distances: pd.Series, limit: int = MATCHES) -> pd.Series:
    chosen = []
    for candidate in distances.sort_values().index:
        if all(abs((pd.Timestamp(candidate) - pd.Timestamp(prior)).days) >= 30 for prior in chosen):
            chosen.append(candidate)
        if len(chosen) == limit:
            break
    return distances.loc[chosen]


def _outcome(frame: pd.DataFrame, analog_date: Any, horizon: int) -> dict[str, Any] | None:
    location = frame.index.get_loc(analog_date)
    future = frame.iloc[location + 1 : location + horizon + 1]
    if len(future) < horizon:
        return None
    start = float(frame.loc[analog_date, "close"])
    path = future.close.to_numpy(float) / start - 1
    market_start = float(frame.loc[analog_date, "imoex_close"])
    market_return = float(future.imoex_close.iloc[-1] / market_start - 1)
    relative = float(path[-1] - market_return)
    return {
        "return": float(path[-1]),
        "relative": relative,
        "drawdown": float(path.min()),
        "mfe": float(path.max()),
        "volatility": float(np.std(np.diff(np.r_[0.0, path])) * np.sqrt(252)),
        "observed_until": future.index[-1],
    }


def _distances(frame: pd.DataFrame, position: int, train_end: int) -> dict[str, pd.Series]:
    state = _state_distances(frame, position, train_end)
    path = _path_distances(frame, position, train_end)
    return {"path": path, "state": state, "combined": _combined(path, state)}


def _validate(frame: pd.DataFrame, horizon: int, analog_type: str) -> dict[str, Any]:
    errors: list[float] = []
    baseline_errors: list[float] = []
    downsides: list[float] = []
    useful: list[float] = []
    last_position = len(frame) - horizon - 1
    positions = list(range(MIN_HISTORY + horizon, last_position, max(20, horizon)))[-12:]
    for position in positions:
        train_end = position - horizon
        candidates = _independent(_distances(frame, position, train_end)[analog_type])
        outcomes = [_outcome(frame, date, horizon) for date in candidates.index]
        outcomes = [row for row in outcomes if row is not None]
        actual = _outcome(frame, frame.index[position], horizon)
        if len(outcomes) < 5 or actual is None:
            continue
        prediction = float(np.median([row["return"] for row in outcomes]))
        historical = frame.close.iloc[:train_end].pct_change(horizon).dropna()
        baseline = float(historical.median())
        errors.append(abs(prediction - actual["return"]))
        baseline_errors.append(abs(baseline - actual["return"]))
        downsides.append(float(np.median([row["drawdown"] for row in outcomes])))
        useful.append(float(np.sign(prediction) == np.sign(actual["return"])))
    mae = float(np.mean(errors)) if errors else None
    baseline_mae = float(np.mean(baseline_errors)) if errors else None
    improvement = baseline_mae - mae if mae is not None and baseline_mae is not None else None
    return {
        "observations": len(errors),
        "mae": mae,
        "baseline_mae": baseline_mae,
        "improvement": improvement,
        "downside": float(np.median(downsides)) if downsides else None,
        "usefulness": float(np.mean(useful)) if useful else None,
        "status": "experimental"
        if len(errors) >= 5 and improvement is not None and improvement > 0
        else "insufficient_or_rejected",
    }


def run_state_similarity(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    signature = con.execute(
        "SELECT count(*),max(trade_date),sum(close) FROM canonical_daily_prices "
        "WHERE canonical_secid IN (SELECT unnest(?))",
        [list(SECIDS)],
    ).fetchone()
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}|{signature}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM state_similarity_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    match_rows, outcome_rows, validation_rows = [], [], []
    for secid in SECIDS:
        frame = _panel(con, secid, cutoff)
        if len(frame) < MIN_HISTORY + max(HORIZONS):
            continue
        position = len(frame) - 1
        train_end = max(0, position - max(HORIZONS))
        for analog_type, values in _distances(frame, position, train_end).items():
            nearest = _independent(values)
            max_distance = float(nearest.max()) if len(nearest) else 1.0
            for rank, (analog_date, distance) in enumerate(nearest.sort_values().items(), 1):
                factors = {
                    feature: {
                        "current": float(frame.iloc[position][feature]),
                        "historical": float(frame.loc[analog_date, feature]),
                    }
                    for feature in FEATURES
                }
                match_rows.append(
                    [
                        run_id,
                        secid,
                        analog_type,
                        analog_date,
                        rank,
                        float(distance),
                        max(0.0, 1 - float(distance) / max(max_distance, 1e-12)),
                        json.dumps(factors),
                        frame.index[train_end - 1],
                        True,
                        True,
                    ]
                )
                for horizon in HORIZONS:
                    outcome = _outcome(frame, analog_date, horizon)
                    if outcome and outcome["observed_until"] <= pd.Timestamp(cutoff):
                        outcome_rows.append(
                            [
                                run_id,
                                secid,
                                analog_type,
                                analog_date,
                                horizon,
                                outcome["return"],
                                outcome["relative"],
                                outcome["drawdown"],
                                outcome["mfe"],
                                outcome["volatility"],
                                outcome["observed_until"],
                                True,
                            ]
                        )
            for horizon in HORIZONS:
                result = _validate(frame, horizon, analog_type)
                validation_rows.append(
                    [
                        run_id,
                        secid,
                        analog_type,
                        horizon,
                        result["observations"],
                        result["mae"],
                        result["baseline_mae"],
                        result["improvement"],
                        result["downside"],
                        result["usefulness"],
                        result["status"],
                        False,
                        True,
                        True,
                    ]
                )
    # Combined weight is unlocked only where it beats both component methods on the same sample.
    for secid in SECIDS:
        for horizon in HORIZONS:
            rows = [row for row in validation_rows if row[1] == secid and row[3] == horizon]
            maes = {row[2]: row[5] for row in rows}
            for row in rows:
                if row[2] == "combined" and all(maes.get(key) is not None for key in ANALOG_TYPES):
                    row[11] = bool(maes["combined"] < min(maes["path"], maes["state"]))
    con.executemany("INSERT INTO state_similarity_matches VALUES (?,?,?,?,?,?,?,?,?,?,?)", match_rows)
    con.executemany("INSERT INTO state_similarity_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", outcome_rows)
    con.executemany(
        "INSERT INTO state_similarity_validation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", validation_rows
    )
    con.execute(
        "INSERT INTO state_similarity_runs VALUES (?,?,?, ?,?,?,?,?,TRUE,TRUE,'completed',?)",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            len(SECIDS),
            len(match_rows),
            len(validation_rows),
            VERSION,
            True,
            json.dumps({"pit_only": True, "model_outputs_used": False}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT cutoff,matches,validations,status FROM state_similarity_runs WHERE run_id=?",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "cutoff": row[0],
        "matches": row[1],
        "validations": row[2],
        "status": row[3],
        "production_changes": 0,
        "probability_gate_changed": False,
    }
