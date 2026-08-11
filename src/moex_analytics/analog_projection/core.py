"""Stage 92: rescale real post-T0 analog returns to the current observed price."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np

from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

VERSION = "analog-forward-projection-v2"
HORIZONS = (1, 5, 20, 40, 60, 80, 100, 120, 250)
MIN_ANALOGS = 5


def _medoid(paths: dict[Any, dict[int, float]], median: dict[int, float]) -> Any | None:
    candidates: list[tuple[float, Any]] = []
    for analog_date, points in paths.items():
        common = sorted(set(points) & set(median))
        if common:
            candidates.append((float(np.mean([abs(points[x] - median[x]) for x in common])), analog_date))
    return min(candidates, default=(0.0, None), key=lambda item: (item[0], item[1]))[1]


def _drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    wealth = np.asarray([1.0, *[1 + value for value in values]], dtype=float)
    return float(np.min(wealth / np.maximum.accumulate(wealth) - 1))


def build_analog_projections(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        """SELECT run_id,trajectory_run_id,cutoff FROM scenario_research_runs
        WHERE status='completed' ORDER BY finished_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed scenario research is required")
    scenario_run, trajectory_run, cutoff = source
    run_id = hashlib.sha256(f"{VERSION}|{scenario_run}|{trajectory_run}|{cutoff}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM analog_projection_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}

    path_rows: list[list[Any]] = []
    band_rows: list[list[Any]] = []
    horizon_rows: list[list[Any]] = []
    eligible = 0
    for secid in SECIDS:
        price_row = con.execute(
            """SELECT close FROM canonical_daily_prices
            WHERE canonical_secid=? AND trade_date=? AND close>0""",
            [secid, cutoff],
        ).fetchone()
        if not price_row:
            for horizon in HORIZONS:
                horizon_rows.append(
                    [run_id, secid, horizon, "insufficient_history", None, None, None, None, None,
                     None, None, 0, None, None, None, 0, None, True]
                )
            continue
        current_price = float(price_row[0])
        matches = con.execute(
            """SELECT analog_date,similarity_score FROM scenario_multiscale_matches
            WHERE run_id=? AND secid=? AND method='path_cosine' AND independent
            ORDER BY combined_distance,analog_date LIMIT 10""",
            [scenario_run, secid],
        ).fetchall()
        paths: dict[Any, dict[int, float]] = {}
        source_dates: dict[tuple[Any, int], Any] = {}
        similarity: dict[Any, float | None] = {}
        for analog_date, score in matches:
            points = con.execute(
                """SELECT forward_session,source_trade_date,forward_return
                FROM analog_forward_trajectories WHERE run_id=? AND secid=?
                AND method='path_cosine' AND path_window=20 AND analog_date=?
                AND forward_session BETWEEN 1 AND 250 ORDER BY forward_session""",
                [trajectory_run, secid, analog_date],
            ).fetchall()
            if not points:
                continue
            paths[analog_date] = {int(row[0]): float(row[2]) for row in points}
            similarity[analog_date] = score
            for session, source_date, _return in points:
                source_dates[(analog_date, int(session))] = source_date
        by_session: dict[int, list[float]] = {}
        for points in paths.values():
            for session, value in points.items():
                by_session.setdefault(session, []).append(value)
        medians = {
            session: float(np.median(values))
            for session, values in by_session.items()
            if len(values) >= MIN_ANALOGS
        }
        medoid = _medoid(paths, medians)
        if medians:
            eligible += 1
        for analog_date, points in paths.items():
            path_rows.append(
                [run_id, secid, analog_date, 0, analog_date, 0.0, current_price, current_price,
                 similarity[analog_date], analog_date == medoid, True]
            )
            for session, value in points.items():
                path_rows.append(
                    [run_id, secid, analog_date, session, source_dates[(analog_date, session)], value,
                     current_price, current_price * (1 + value), similarity[analog_date],
                     analog_date == medoid, True]
                )
        if paths:
            band_rows.append(
                [run_id, secid, 0, current_price, len(paths), current_price, current_price,
                 current_price, current_price, current_price, True]
            )
        for session, values in sorted(by_session.items()):
            if len(values) < MIN_ANALOGS:
                continue
            q10, q25, q50, q75, q90 = np.quantile(values, [0.1, 0.25, 0.5, 0.75, 0.9])
            band_rows.append(
                [run_id, secid, session, current_price, len(values),
                 current_price * (1 + q10), current_price * (1 + q25),
                 current_price * (1 + q50), current_price * (1 + q75),
                 current_price * (1 + q90), True]
            )
        for horizon in HORIZONS:
            terminal = [
                (analog_date, points[horizon])
                for analog_date, points in paths.items()
                if horizon in points
            ]
            status = "ready" if len(terminal) >= MIN_ANALOGS else "insufficient_history"
            if status != "ready":
                horizon_rows.append(
                    [run_id, secid, horizon, status, current_price, None, None, None, None, None,
                     None, len(terminal), None, None, None, 0, medoid, True]
                )
                continue
            values = np.asarray([value for _, value in terminal], dtype=float)
            q10, q25, q50, q75, q90 = np.quantile(values, [0.1, 0.25, 0.5, 0.75, 0.9])
            drawdowns = [
                _drawdown([points[x] for x in sorted(points) if x <= horizon])
                for analog_date, points in paths.items()
                if horizon in points
            ]
            horizon_rows.append(
                [run_id, secid, horizon, status, current_price, current_price * (1 + q50), q50,
                 current_price * (1 + q10), current_price * (1 + q25), current_price * (1 + q75),
                 current_price * (1 + q90), len(values), float(np.median(drawdowns)),
                 float(values.min()), float(values.max()), int(np.sum(values > 0)), medoid, True]
            )
    if path_rows:
        con.executemany(
            """INSERT INTO analog_projected_paths (
            run_id,secid,analog_date,relative_session,source_trade_date,historical_return,
            current_price,projected_price,similarity,is_medoid,immutable
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            path_rows,
        )
    if band_rows:
        con.executemany(
            """INSERT INTO analog_projection_bands (
            run_id,secid,relative_session,current_price,analog_count,q10_price,q25_price,
            median_price,q75_price,q90_price,immutable
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            band_rows,
        )
    con.executemany(
        """INSERT INTO analog_projection_horizons (
        run_id,secid,horizon,status,current_price,central_price,median_return,q10_price,q25_price,
        q75_price,q90_price,analog_count,median_max_drawdown,worst_return,best_return,above_count,
        medoid_analog_date,immutable
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        horizon_rows,
    )
    con.execute(
        """INSERT INTO analog_projection_runs (
        run_id,created_at,cutoff,scenario_run_id,trajectory_run_id,instruments,
        eligible_instruments,methodology_version,status,immutable,production_unchanged,
        probability_gate_unchanged,details_json
        ) VALUES (?,?,?,?,?,?,?,?,'completed',TRUE,TRUE,TRUE,?)""",
        [run_id, datetime.now(UTC), cutoff, scenario_run, trajectory_run, len(SECIDS), eligible,
         VERSION, json.dumps({"source": "real historical relative paths", "synthetic": False,
                              "matching_pre_t0_only": True, "smoothing": False})],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        """SELECT count(*),count(*) FILTER(WHERE status='ready'),count(DISTINCT secid)
        FILTER(WHERE status='ready') FROM analog_projection_horizons WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {"run_id": run_id, "horizons": row[0], "ready_horizons": row[1],
            "eligible_instruments": row[2], "status": "completed", "production_changes": 0,
            "probability_gate_changed": False}
