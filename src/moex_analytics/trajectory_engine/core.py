"""Actual historical trajectories and temporally strict analog replays."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from moex_analytics.analog_engine.core import INSTRUMENTS, independent_nearest

from .schema import DDL

VERSION = "analog-trajectories-v1"
HORIZONS = (1, 5, 20, 60, 120, 250)
MIN_EFFECTIVE_N = 5
REPLAY_STEP = 5
REPLAY_LOOKBACK = 20


def _bulk_insert(con: Any, table: str, columns: tuple[str, ...], records: list[list]) -> None:
    if not records:
        return
    relation = f"_{table}_batch"
    con.register(relation, pd.DataFrame.from_records(records, columns=columns))
    names = ",".join(columns)
    con.execute(f"INSERT OR REPLACE INTO {table} ({names}) SELECT {names} FROM {relation}")
    con.unregister(relation)


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def normalize_forward_path(prices: pd.Series, position: int, maximum: int = 250) -> pd.Series:
    """Return observed post-T0 prices normalized to 100; never interpolate."""
    if position < 0 or position >= len(prices) or prices.iloc[position] <= 0:
        return pd.Series(dtype=float)
    observed = prices.iloc[position : min(len(prices), position + maximum + 1)]
    return observed.astype(float) / float(observed.iloc[0]) * 100.0


def terminal_statistics(returns: pd.Series, adverse: pd.Series, favorable: pd.Series) -> dict:
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < MIN_EFFECTIVE_N:
        return {"status": "insufficient_data", "reason": "fewer than five matured episodes"}
    quantiles = clean.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    dispersion = float(clean.quantile(0.75) - clean.quantile(0.25))
    directional = max(float((clean > 0).mean()), float((clean < 0).mean()))
    consensus = "stronger" if directional >= 0.70 and dispersion <= 0.25 else "high_dispersion"
    return {
        "status": "ready",
        "reason": "actual matured historical trajectories",
        "n": len(clean),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "q10": float(quantiles.loc[0.10]),
        "q25": float(quantiles.loc[0.25]),
        "q50": float(quantiles.loc[0.50]),
        "q75": float(quantiles.loc[0.75]),
        "q90": float(quantiles.loc[0.90]),
        "positive": float((clean > 0).mean()),
        "negative": float((clean < 0).mean()),
        "adverse": float(adverse.reindex(clean.index).mean()),
        "favorable": float(favorable.reindex(clean.index).mean()),
        "dispersion": dispersion,
        "consensus": consensus,
    }


def _prices(con: Any, secid: str) -> pd.Series:
    frame = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices "
        "WHERE canonical_secid=? AND close>0 ORDER BY trade_date",
        [secid],
    ).df()
    if frame.empty:
        return pd.Series(dtype=float)
    return pd.Series(frame.close.to_numpy(float), index=pd.to_datetime(frame.trade_date), name=secid)


def _write_current_trajectories(con: Any, run_id: str, analog_run: str) -> tuple[int, int]:
    trajectory_rows = 0
    distribution_rows = 0
    for secid in INSTRUMENTS:
        trajectory_records = []
        distribution_records = []
        prices = _prices(con, secid)
        if prices.empty:
            continue
        positions = {date: pos for pos, date in enumerate(prices.index)}
        analogs = con.execute(
            "SELECT method,path_window,analog_date,similarity_score,data_quality "
            "FROM historical_analogs_v3 WHERE run_id=? AND analog_type='issuer' AND secid=?",
            [analog_run, secid],
        ).fetchall()
        groups: dict[tuple[str, int], list[tuple[pd.Timestamp, float, float]]] = {}
        for method, window, date, similarity, quality in analogs:
            key = (method, int(window))
            groups.setdefault(key, []).append((pd.Timestamp(date), float(similarity), float(quality)))
            date = pd.Timestamp(date)
            if date not in positions:
                continue
            path = normalize_forward_path(prices, positions[date])
            for session, (source_date, value) in enumerate(path.items()):
                if session == 0:
                    continue
                trajectory_records.append(
                    [run_id, secid, method, window, date, session, float(value),
                     float(value / 100 - 1), source_date, similarity, quality]
                )
                trajectory_rows += 1
        for (method, window), episodes in groups.items():
            for horizon in HORIZONS:
                terminal, adverse, favorable = {}, {}, {}
                for date, _, _ in episodes:
                    if date not in positions:
                        continue
                    path = normalize_forward_path(prices, positions[date], horizon)
                    if len(path) <= horizon:
                        continue
                    values = path.iloc[1:] / 100 - 1
                    terminal[date] = float(values.iloc[horizon - 1])
                    adverse[date] = float(values.min())
                    favorable[date] = float(values.max())
                stats = terminal_statistics(pd.Series(terminal), pd.Series(adverse), pd.Series(favorable))
                current_price = float(prices.iloc[-1])
                median = stats.get("median")
                distribution_records.append(
                    [run_id, secid, method, window, horizon, stats.get("n", len(terminal)),
                     stats.get("mean"), median, stats.get("q10"), stats.get("q25"), stats.get("q50"),
                     stats.get("q75"), stats.get("q90"), stats.get("positive"), stats.get("negative"),
                     stats.get("adverse"), stats.get("favorable"), stats.get("dispersion"),
                     stats.get("consensus"), current_price,
                     current_price * (1 + median) if median is not None else None,
                     stats["status"], stats["reason"]]
                )
                distribution_rows += 1
        if trajectory_records:
            _bulk_insert(
                con, "analog_forward_trajectories",
                ("run_id", "secid", "method", "path_window", "analog_date", "forward_session",
                 "normalized_price", "forward_return", "source_trade_date", "similarity_score",
                 "data_quality"), trajectory_records,
            )
        if distribution_records:
            _bulk_insert(
                con, "analog_terminal_distributions",
                ("run_id", "secid", "method", "path_window", "horizon", "effective_n",
                 "mean_return", "median_return", "q10", "q25", "q50", "q75", "q90",
                 "positive_fraction", "negative_fraction", "mean_adverse_excursion",
                 "mean_favorable_excursion", "dispersion", "consensus_status", "current_price",
                 "terminal_reference", "status", "reason"), distribution_records,
            )
    return trajectory_rows, distribution_rows


def _write_oos_replays(con: Any, run_id: str) -> int:
    """Expanding-window replay: every neighbor and transform precedes the simulated cutoff."""
    written = 0
    for secid in INSTRUMENTS:
        records = []
        prices = _prices(con, secid)
        returns = prices.pct_change(fill_method=None)
        state = prices.pct_change(REPLAY_LOOKBACK, fill_method=None)
        for cutoff_pos in range(750, len(prices) - max(HORIZONS), REPLAY_STEP):
            cutoff = prices.index[cutoff_pos]
            candidates = state.iloc[: cutoff_pos - max(HORIZONS)].dropna()
            if candidates.empty or pd.isna(state.iloc[cutoff_pos]):
                continue
            distances = (candidates - state.iloc[cutoff_pos]).abs()
            nearest = independent_nearest(distances, separation=20, limit=20)
            for horizon in HORIZONS:
                outcomes = []
                for date in nearest.index:
                    pos = prices.index.get_loc(date)
                    if pos + horizon < cutoff_pos:
                        outcomes.append(float(prices.iloc[pos + horizon] / prices.iloc[pos] - 1))
                if len(outcomes) < MIN_EFFECTIVE_N or cutoff_pos + horizon >= len(prices):
                    continue
                forecast = float(np.median(outcomes))
                actual = float(prices.iloc[cutoff_pos + horizon] / prices.iloc[cutoff_pos] - 1)
                baseline = float(returns.iloc[:cutoff_pos].mean() * horizon)
                records.append(
                    [run_id, secid, cutoff, horizon, len(outcomes), forecast, actual,
                     bool(np.sign(forecast) == np.sign(actual)), abs(forecast - actual), baseline,
                     prices.index[cutoff_pos - 1]]
                )
                written += 1
        if records:
            extended = [[*row, True, "research_only"] for row in records]
            _bulk_insert(
                con, "analog_oos_replays",
                ("run_id", "secid", "cutoff", "horizon", "effective_n",
                 "forecast_median_return", "actual_return", "direction_correct", "absolute_error",
                 "baseline_return", "history_end", "train_only", "status"), extended,
            )
    return written


def run_trajectory_forecasting(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id,cutoff FROM analog_search_runs_v3 WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise ValueError("completed Stage 44 analog run is required")
    analog_run, cutoff = source
    run_id = hashlib.sha256(f"{analog_run}|{cutoff}|{VERSION}".encode()).hexdigest()[:20]
    for table in ("analog_trajectory_runs", "analog_forward_trajectories",
                  "analog_terminal_distributions", "analog_oos_replays"):
        con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
    con.execute(
        "INSERT INTO analog_trajectory_runs "
        "(run_id,analog_run_id,cutoff,created_at,status,trajectory_rows,distribution_rows,replay_rows,"
        "methodology_version,details_json) VALUES (?,?,?,current_timestamp,'running',0,0,0,?,?)",
        [run_id, analog_run, cutoff, VERSION, json.dumps({"synthetic_paths": False})],
    )
    try:
        trajectories, distributions = _write_current_trajectories(con, run_id, analog_run)
        replays = _write_oos_replays(con, run_id)
        status = "completed" if distributions and replays else "completed_insufficient_data"
        details = {"future_leakage": False, "production_changes": 0, "probability_published": False}
        con.execute(
            "UPDATE analog_trajectory_runs SET finished_at=current_timestamp,status=?,trajectory_rows=?,"
            "distribution_rows=?,replay_rows=?,details_json=? WHERE run_id=?",
            [status, trajectories, distributions, replays, json.dumps(details), run_id],
        )
        return {"run_id": run_id, "status": status, "trajectories": trajectories,
                "distributions": distributions, "replays": replays, "cutoff": cutoff}
    except Exception as exc:
        con.execute(
            "UPDATE analog_trajectory_runs SET finished_at=current_timestamp,status='failed',details_json=? "
            "WHERE run_id=?", [json.dumps({"error": str(exc), "error_type": type(exc).__name__}), run_id]
        )
        raise


def trajectory_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,cutoff,trajectory_rows,distribution_rows,replay_rows "
        "FROM analog_trajectory_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"latest": None}
    names = ("run_id", "status", "cutoff", "trajectories", "distributions", "replays")
    return dict(zip(names, row, strict=True))
