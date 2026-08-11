"""Stage 93: keep analog, model, and consensus semantics explicitly separate."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np

from moex_analytics.analog_projection.core import HORIZONS
from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

VERSION = "multi-horizon-price-scenarios-v1"
BRANCH_LABELS = {
    "favorable": "Благоприятный",
    "central": "Базовый / центральный",
    "dip_recovery": "Просадка с восстановлением",  # noqa: RUF001
    "adverse": "Неблагоприятный",
}


def _latest_distribution(con: Any) -> str | None:
    try:
        row = con.execute(
            """SELECT run_id FROM distribution_research_runs
            WHERE status='completed' ORDER BY finished_at DESC LIMIT 1"""
        ).fetchone()
        return row[0] if row else None
    except duckdb.CatalogException:
        return None


def _branch(terminal: float, minimum: float, q25: float, q75: float) -> str:
    if terminal <= q25:
        return "adverse"
    if minimum <= -0.05 and terminal > 0:
        return "dip_recovery"
    if terminal >= q75:
        return "favorable"
    return "central"


def _medoid(group: dict[Any, dict[int, float]]) -> Any:
    sessions = sorted(set.intersection(*(set(points) for points in group.values())))
    center = {
        session: float(np.median([points[session] for points in group.values()]))
        for session in sessions
    }
    return min(
        group,
        key=lambda analog: (
            float(np.mean([abs(group[analog][session] - center[session]) for session in sessions])),
            analog,
        ),
    )


def build_price_scenarios(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    projection = con.execute(
        """SELECT run_id,cutoff FROM analog_projection_runs
        WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not projection:
        raise ValueError("completed Stage 92 projection is required")
    projection_run, cutoff = projection
    distribution_run = _latest_distribution(con)
    run_id = hashlib.sha256(
        f"{VERSION}|{projection_run}|{distribution_run}|{cutoff}".encode()
    ).hexdigest()[:20]
    if con.execute("SELECT 1 FROM price_scenario_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}

    layers: list[list[Any]] = []
    branches: list[list[Any]] = []
    touches: list[list[Any]] = []
    for secid in SECIDS:
        analog_rows = con.execute(
            """SELECT horizon,status,current_price,central_price,q10_price,q25_price,q75_price,
            q90_price FROM analog_projection_horizons WHERE run_id=? AND secid=?""",
            [projection_run, secid],
        ).fetchall()
        for horizon, analog_status, _price, center, q10, q25, q75, q90 in analog_rows:
            model = None
            scorecard = None
            if distribution_run and horizon in {5, 20, 60, 120, 250}:
                model = con.execute(
                    """SELECT method,q10_price,q25_price,q50_price,q75_price,q90_price,status
                    FROM current_return_distributions WHERE run_id=? AND secid=? AND horizon=?""",
                    [distribution_run, secid, horizon],
                ).fetchone()
                scorecard = con.execute(
                    """SELECT observations,baseline_delta,status FROM distribution_scorecards
                    WHERE run_id=? AND horizon=? AND sample_type='untouched_holdout_frozen'""",
                    [distribution_run, horizon],
                ).fetchone()
            model_usable = bool(
                model
                and scorecard
                and scorecard[0] >= 50
                and scorecard[1] is not None
                and scorecard[1] > 0
                and scorecard[2] == "SHADOW_CANDIDATE"
            )
            model_values = model[1:6] if model else (None,) * 5
            layers.append(
                [run_id, secid, horizon, analog_status, center, q10, q25, q75, q90,
                 "validated_shadow_range" if model_usable else "not_usable",
                 model[0] if model else None, *model_values,
                 "not_validated",
                 "no frozen OOS test proves that blending analog and model distributions "
                 "improves MAE, pinball, coverage and downside",
                 True]
            )
        paths_rows = con.execute(
            """SELECT analog_date,relative_session,historical_return,current_price
            FROM analog_projected_paths WHERE run_id=? AND secid=? AND relative_session>0
            ORDER BY analog_date,relative_session""",
            [projection_run, secid],
        ).fetchall()
        paths: dict[Any, dict[int, float]] = {}
        current_price = None
        for analog_date, session, value, price in paths_rows:
            paths.setdefault(analog_date, {})[int(session)] = float(value)
            current_price = float(price)
        if not paths:
            for branch, label in BRANCH_LABELS.items():
                branches.append(
                    [run_id, secid, branch, label, 0, None, json.dumps({}), None,
                     "insufficient_history", True]
                )
            continue
        terminal_values = np.asarray([points[max(points)] for points in paths.values()])
        q25, q75 = np.quantile(terminal_values, [0.25, 0.75])
        grouped: dict[str, dict[Any, dict[int, float]]] = {name: {} for name in BRANCH_LABELS}
        for analog_date, points in paths.items():
            name = _branch(points[max(points)], min(points.values()), float(q25), float(q75))
            grouped[name][analog_date] = points
        for name, label in BRANCH_LABELS.items():
            group = grouped[name]
            if not group:
                branches.append(
                    [run_id, secid, name, label, 0, None, json.dumps({}), None,
                     "not_observed", True]
                )
                continue
            medoid = _medoid(group)
            points = group[medoid]
            prices = {
                str(horizon): current_price * (1 + points[horizon])
                for horizon in HORIZONS
                if horizon in points
            }
            drawdown = float(min(points.values()))
            branches.append(
                [run_id, secid, name, label, len(group), medoid, json.dumps(prices),
                 drawdown, "historical_cluster", True]
            )
        for horizon in HORIZONS:
            eligible_paths = [points for points in paths.values() if horizon in points]
            minima = [
                min(value for session, value in path.items() if session <= horizon)
                for path in eligible_paths
            ]
            maxima = [
                max(value for session, value in path.items() if session <= horizon)
                for path in eligible_paths
            ]
            touches.append(
                [run_id, secid, horizon, len(eligible_paths),
                 sum(value <= -0.05 for value in minima),
                 sum(value <= -0.10 for value in minima),
                 sum(value >= 0.05 for value in maxima),
                 sum(value >= 0.10 for value in maxima),
                 True]
            )
    con.executemany(
        """INSERT INTO price_scenario_layers (
        run_id,secid,horizon,analog_status,analog_central_price,analog_q10_price,
        analog_q25_price,analog_q75_price,analog_q90_price,model_status,model_method,
        model_q10_price,model_q25_price,model_q50_price,model_q75_price,model_q90_price,
        consensus_status,consensus_reason,immutable
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        layers,
    )
    con.executemany(
        """INSERT INTO price_scenario_branches (
        run_id,secid,branch,label,episodes,medoid_analog_date,terminal_prices_json,
        max_drawdown,status,immutable) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        branches,
    )
    con.executemany(
        """INSERT INTO price_scenario_touch_memory (
        run_id,secid,horizon,analog_count,touch_down_5,touch_down_10,touch_up_5,
        touch_up_10,immutable) VALUES (?,?,?,?,?,?,?,?,?)""",
        touches,
    )
    con.execute(
        """INSERT INTO price_scenario_runs (
        run_id,created_at,cutoff,projection_run_id,distribution_run_id,instruments,branches,
        methodology_version,status,immutable,production_unchanged,probability_gate_unchanged,
        details_json) VALUES (?,?,?,?,?,?,?,?,'completed',TRUE,TRUE,TRUE,?)""",
        [run_id, datetime.now(UTC), cutoff, projection_run, distribution_run, len(SECIDS),
         len(branches), VERSION, json.dumps({"consensus_default": "not_validated",
                                             "branch_source": "real path clusters",
                                             "probability_wording": False})],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: Any, run_id: str) -> dict[str, Any]:
    row = con.execute(
        """SELECT count(*),count(*) FILTER(WHERE analog_status='ready'),
        count(*) FILTER(WHERE model_status='validated_shadow_range'),
        count(*) FILTER(WHERE consensus_status<>'not_validated')
        FROM price_scenario_layers WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {"run_id": run_id, "layers": row[0], "analog_ready": row[1],
            "model_usable": row[2], "consensus_usable": row[3], "status": "completed",
            "production_changes": 0, "probability_gate_changed": False}
