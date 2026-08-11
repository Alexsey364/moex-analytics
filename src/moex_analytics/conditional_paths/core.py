"""Session-by-session conditional path and risk analytics (Stage 98)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import yaml

from moex_analytics.conditional_forecast.core import effective_sample_size, weighted_quantile
from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

VERSION = "conditional-path-analytics-v1"
RISK_HORIZONS = (20, 60, 120, 250)


def _config() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[3] / "config" / "conditional_forecasting.yaml"
    raw = path.read_bytes()
    return yaml.safe_load(raw)["conditional_weighting"], hashlib.sha256(raw).hexdigest()


def path_statistics(path: Any) -> dict[str, Any]:
    values = np.asarray(path, dtype=float)
    if not len(values):
        raise ValueError("path cannot be empty")
    prices = 1 + values
    running_peak = np.maximum.accumulate(prices)
    drawdown = prices / running_peak - 1
    trough = int(np.argmin(values))
    peak = int(np.argmax(values))
    recovery = next((position for position in range(trough + 1, len(values)) if values[position] >= 0), None)
    dipped = float(values.min()) < 0
    return {
        "mae": float(values.min()),
        "mfe": float(values.max()),
        "max_drawdown": float(drawdown.min()),
        "time_to_trough": trough,
        "time_to_peak": peak,
        "underwater": bool(values[-1] < 0),
        "recovered": bool(dipped and recovery is not None),
        "recovery_time": recovery,
        "new_high_after_recovery": bool(
            recovery is not None and recovery + 1 < len(values) and values[recovery + 1 :].max() > 0
        ),
        "fall_first_end_positive": bool(dipped and values[-1] > 0 and trough < len(values) - 1),
    }


def _path(con: duckdb.DuckDBPyConnection, secid: str, analog_date: Any) -> np.ndarray | None:
    rows = con.execute(
        """SELECT close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>=?
        AND close>0 ORDER BY trade_date LIMIT 251""",
        [secid, analog_date],
    ).fetchall()
    if len(rows) < 251:
        return None
    prices = np.asarray([row[0] for row in rows], dtype=float)
    return prices / prices[0] - 1


def _weighted_frequency(flags: np.ndarray, weights: np.ndarray) -> float:
    return float(weights[np.asarray(flags, dtype=bool)].sum())


def _risk_row(
    run_id: str,
    secid: str,
    horizon: int,
    paths: list[np.ndarray],
    weights: np.ndarray,
    minimum_ess: float,
) -> list[Any]:
    if not paths:
        return [run_id, secid, horizon, "insufficient_conditional_history", 0] + [None] * 16 + [
            "insufficient_evidence", True
        ]
    stats = [path_statistics(path[: horizon + 1]) for path in paths]
    ess = effective_sample_size(weights)
    evidence = "uncalibrated_research" if ess >= minimum_ess else "insufficient_evidence"
    recovery_times = np.asarray(
        [row["recovery_time"] if row["recovery_time"] is not None else np.nan for row in stats]
    )
    recovered = np.isfinite(recovery_times)
    recovery_weights = weights[recovered]
    recovery_median = (
        weighted_quantile(recovery_times[recovered], recovery_weights, 0.5)
        if recovered.any()
        else None
    )
    drawdowns = np.asarray([row["max_drawdown"] for row in stats])
    return [
        run_id,
        secid,
        horizon,
        "ready_research_unvalidated",
        len(paths),
        ess,
        weighted_quantile([row["mae"] for row in stats], weights, 0.5),
        weighted_quantile([row["mfe"] for row in stats], weights, 0.5),
        weighted_quantile(drawdowns, weights, 0.5),
        weighted_quantile([row["time_to_trough"] for row in stats], weights, 0.5),
        weighted_quantile([row["time_to_peak"] for row in stats], weights, 0.5),
        _weighted_frequency(np.asarray([row["underwater"] for row in stats]), weights),
        _weighted_frequency(np.asarray([row["recovered"] for row in stats]), weights),
        recovery_median,
        _weighted_frequency(np.asarray([row["new_high_after_recovery"] for row in stats]), weights),
        _weighted_frequency(np.asarray([row["fall_first_end_positive"] for row in stats]), weights),
        *[
            _weighted_frequency(drawdowns <= -threshold, weights)
            for threshold in (0.03, 0.05, 0.10, 0.15, 0.20)
        ],
        evidence,
        True,
    ]


def build_conditional_paths(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    config, signature = _config()
    regime_source = con.execute(
        """SELECT run_id,cutoff FROM conditional_regime_runs
        WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    forecast_source = con.execute(
        """SELECT run_id FROM conditional_forecast_runs
        WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not regime_source or not forecast_source:
        raise ValueError("completed Stage 96 and Stage 97 runs are required")
    regime_run_id, cutoff = regime_source
    forecast_run_id = forecast_source[0]
    run_id = hashlib.sha256(
        f"{VERSION}|{regime_run_id}|{forecast_run_id}|{signature}".encode()
    ).hexdigest()[:20]
    existing = con.execute(
        "SELECT paths,curve_rows,status FROM conditional_path_runs WHERE run_id=?", [run_id]
    ).fetchone()
    if existing:
        return {
            "run_id": run_id, "paths": existing[0], "curve_rows": existing[1],
            "status": existing[2], "idempotent": True,
        }
    path_rows: list[list[Any]] = []
    curve_rows: list[list[Any]] = []
    risk_rows: list[list[Any]] = []
    for secid in SECIDS:
        current = con.execute(
            """SELECT close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date<=?
            ORDER BY trade_date DESC LIMIT 1""",
            [secid, cutoff],
        ).fetchone()
        if not current:
            continue
        current_price = float(current[0])
        analogs = con.execute(
            """SELECT r.analog_date,r.episode_id,r.scenario_role,coalesce(w.normalized_weight,0)
            FROM regime_conditioned_analogs r LEFT JOIN conditional_analog_weights w
            ON w.run_id=? AND w.secid=r.secid AND w.analog_date=r.analog_date
            WHERE r.run_id=? AND r.secid=? ORDER BY r.scenario_role,r.analog_date""",
            [forecast_run_id, regime_run_id, secid],
        ).fetchall()
        path_items = []
        for analog_date, episode_id, role, weight in analogs:
            values = _path(con, secid, analog_date)
            if values is None:
                continue
            path_items.append((analog_date, episode_id, role, float(weight), values))
        expected = [item for item in path_items if item[2] == "EXPECTED_CONDITIONAL" and item[3] > 0]
        weights = np.asarray([item[3] for item in expected], dtype=float)
        if weights.sum() > 0:
            weights /= weights.sum()
        for analog_date, episode_id, role, _, values in path_items:
            normalized_weight = 0.0
            if role == "EXPECTED_CONDITIONAL":
                matches = [position for position, item in enumerate(expected) if item[0] == analog_date]
                normalized_weight = float(weights[matches[0]]) if matches else 0.0
            for session, value in enumerate(values):
                path_rows.append([
                    run_id, secid, analog_date, episode_id, role, session, float(value),
                    current_price * (1 + float(value)), normalized_weight, True,
                ])
        expected_paths = [item[4] for item in expected]
        stress_paths = [item[4] for item in path_items if item[2] == "STRESS"]
        ess = effective_sample_size(weights)
        evidence = (
            "uncalibrated_research"
            if ess >= float(config["minimum_effective_sample"])
            else "insufficient_evidence"
        )
        for session in range(251):
            if not expected_paths:
                curve_rows.append([
                    run_id, secid, session, "insufficient_conditional_history", *([None] * 8),
                    0, None, "insufficient_evidence", True,
                ])
                continue
            values = np.asarray([path[session] for path in expected_paths])
            stress_values = np.asarray([path[session] for path in stress_paths])
            median = weighted_quantile(values, weights, 0.5)
            curve_rows.append([
                run_id, secid, session, "ready_research_unvalidated", median,
                current_price * (1 + median),
                current_price * (1 + weighted_quantile(values, weights, 0.2)),
                current_price * (1 + weighted_quantile(values, weights, 0.8)),
                current_price * (1 + weighted_quantile(values, weights, 0.1)),
                current_price * (1 + weighted_quantile(values, weights, 0.9)),
                current_price * (1 + stress_values.min()) if len(stress_values) else None,
                current_price * (1 + stress_values.max()) if len(stress_values) else None,
                len(expected_paths), ess, evidence, True,
            ])
        for horizon in RISK_HORIZONS:
            risk_rows.append(
                _risk_row(
                    run_id, secid, horizon, expected_paths, weights,
                    float(config["minimum_effective_sample"]),
                )
            )
    if path_rows:
        con.executemany(
            """INSERT INTO conditional_analog_paths
            (run_id,secid,analog_date,episode_id,scenario_role,session,normalized_return,
            projected_price,weight,immutable) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            path_rows,
        )
    if curve_rows:
        con.executemany(
            """INSERT INTO conditional_path_curves
            (run_id,secid,session,status,weighted_median_return,weighted_median_price,
            expected_low,expected_high,plausible_low,plausible_high,stress_low,stress_high,
            raw_n,effective_sample_size,evidence_status,immutable)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            curve_rows,
        )
    if risk_rows:
        con.executemany(
            """INSERT INTO conditional_path_risk
            (run_id,secid,horizon,status,raw_n,effective_sample_size,median_mae,median_mfe,
            median_max_drawdown,median_time_to_trough,median_time_to_peak,underwater_frequency,
            recovery_frequency,median_recovery_time,new_high_after_recovery_frequency,
            fall_first_end_positive_frequency,dd3_frequency,dd5_frequency,dd10_frequency,
            dd15_frequency,dd20_frequency,evidence_status,immutable)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            risk_rows,
        )
    details = {"raw_branches_default": "off", "center_role": "EXPECTED_CONDITIONAL"}
    con.execute(
        """INSERT INTO conditional_path_runs
        (run_id,created_at,cutoff,regime_run_id,forecast_run_id,path_version,paths,curve_rows,
        immutable,production_unchanged,probability_gate_unchanged,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [run_id, datetime.now(UTC), cutoff, regime_run_id, forecast_run_id, VERSION,
         len(path_rows), len(curve_rows), json.dumps(details, sort_keys=True)],
    )
    return {
        "run_id": run_id, "paths": len(path_rows), "curve_rows": len(curve_rows),
        "status": "completed", "idempotent": False,
    }
