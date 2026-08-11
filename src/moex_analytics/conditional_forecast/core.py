"""Transparent conditional analog weighting and distributions (Stage 96)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import yaml

from moex_analytics.analog_projection.core import HORIZONS
from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

VERSION = "conditional-weighting-v1.1"


def _config() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[3] / "config" / "conditional_forecasting.yaml"
    raw = path.read_bytes()
    return yaml.safe_load(raw)["conditional_weighting"], hashlib.sha256(raw).hexdigest()


def weighted_quantile(values: Any, weights: Any, quantile: float) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights >= 0)
    values, weights = values[valid], weights[valid]
    if not len(values) or weights.sum() <= 0:
        return float("nan")
    order = np.argsort(values, kind="mergesort")
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights) / weights.sum()
    return float(values[min(np.searchsorted(cumulative, quantile, side="left"), len(values) - 1)])


def effective_sample_size(weights: Any) -> float:
    values = np.asarray(weights, dtype=float)
    total = values.sum()
    if total <= 0:
        return 0.0
    normalized = values / total
    return float(1.0 / np.sum(normalized**2))


def _outcome(
    con: duckdb.DuckDBPyConnection, secid: str, analog_date: Any, horizon: int
) -> tuple[float, float] | None:
    rows = con.execute(
        """SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=?
        AND trade_date>=? AND close>0 ORDER BY trade_date LIMIT ?""",
        [secid, analog_date, horizon + 1],
    ).fetchall()
    if len(rows) < horizon + 1 or rows[0][0] != analog_date:
        return None
    prices = np.asarray([row[1] for row in rows], dtype=float)
    path = prices[1:] / prices[0] - 1
    return float(path[-1]), float(path.min())


def _weights(rows: list[tuple[Any, ...]], power: float) -> np.ndarray:
    similarity = np.asarray([row[2] for row in rows], dtype=float) / 100.0
    regime = np.asarray([row[3] for row in rows], dtype=float)
    reliability = np.ones(len(rows), dtype=float)
    raw = np.maximum(similarity, 0) ** power * np.maximum(regime, 0) * reliability
    return raw / raw.sum() if raw.sum() > 0 else np.zeros(len(raw))


def _leave_one_out(
    returns: np.ndarray, weights: np.ndarray, current_price: float
) -> tuple[float | None, float | None, float | None, float | None]:
    if len(returns) < 2:
        return None, None, None, None
    centers, widths, up = [], [], []
    for position in range(len(returns)):
        keep = np.arange(len(returns)) != position
        subset_weights = weights[keep]
        if subset_weights.sum() <= 0:
            continue
        subset_weights = subset_weights / subset_weights.sum()
        subset = returns[keep]
        centers.append(current_price * (1 + weighted_quantile(subset, subset_weights, 0.5)))
        low = weighted_quantile(subset, subset_weights, 0.1)
        high = weighted_quantile(subset, subset_weights, 0.9)
        widths.append(high - low)
        up.append(float(subset_weights[subset > 0].sum()))
    if not centers:
        return None, None, None, None
    return min(centers), max(centers), max(widths) - min(widths), max(up) - min(up)


def _forecast_row(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    secid: str,
    horizon: int,
    current_price: float,
    analogs: list[tuple[Any, ...]],
    stress_dates: list[Any],
    config: dict[str, Any],
) -> list[Any]:
    observed = []
    for row in analogs:
        outcome = _outcome(con, secid, row[0], horizon)
        if outcome is not None:
            observed.append((row, *outcome))
    if not observed:
        return (
            [run_id, secid, horizon, "insufficient_conditional_history", current_price, 0]
            + [None] * 20
            + [True]
        )
    rows = [item[0] for item in observed]
    returns = np.asarray([item[1] for item in observed])
    drawdowns = np.asarray([item[2] for item in observed])
    weights = _weights(rows, float(config["similarity_power"]))
    ess = effective_sample_size(weights)
    max_weight = float(weights.max())
    median_return = weighted_quantile(returns, weights, 0.5)
    mean_return = float(np.average(returns, weights=weights))
    q20, q80 = [weighted_quantile(returns, weights, q) for q in (0.2, 0.8)]
    q10, q90 = [weighted_quantile(returns, weights, q) for q in (0.1, 0.9)]
    stress_returns = [
        outcome[0]
        for date in stress_dates
        if (outcome := _outcome(con, secid, date, horizon)) is not None
    ]
    loo = _leave_one_out(returns, weights, current_price)
    concentrated = max_weight >= float(config["concentrated_max_weight"])
    low_ess = ess < float(config["minimum_effective_sample"])
    robustness = "fragile" if concentrated or low_ess or (loo[3] or 0) > 0.25 else "stable_research"
    evidence = "low_evidence_uncalibrated" if low_ess else "uncalibrated_research"
    return [
        run_id,
        secid,
        horizon,
        "ready_research_unvalidated",
        current_price,
        len(returns),
        ess,
        max_weight,
        mean_return,
        median_return,
        current_price * (1 + median_return),
        current_price * (1 + q20),
        current_price * (1 + q80),
        current_price * (1 + q10),
        current_price * (1 + q90),
        float(weights[returns > 0].sum()),
        float(weights[returns < 0].sum()),
        weighted_quantile(drawdowns, weights, 0.5),
        current_price * (1 + min(stress_returns)) if stress_returns else None,
        current_price * (1 + max(stress_returns)) if stress_returns else None,
        *loo,
        robustness,
        evidence,
        True,
    ]


def build_conditional_forecasts(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    config, signature = _config()
    source = con.execute(
        """SELECT run_id,cutoff FROM conditional_similarity_runs
        WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed conditional similarity run is required")
    similarity_run_id, cutoff = source
    run_id = hashlib.sha256(
        f"{VERSION}|{similarity_run_id}|{cutoff}|{signature}".encode()
    ).hexdigest()[:20]
    existing = con.execute(
        "SELECT forecasts,status FROM conditional_forecast_runs WHERE run_id=?", [run_id]
    ).fetchone()
    if existing:
        return {"run_id": run_id, "forecasts": existing[0], "status": existing[1], "idempotent": True}
    instruments = list(SECIDS)
    forecast_rows, weight_rows = [], []
    for secid in instruments:
        analogs = con.execute(
            """SELECT analog_date,episode_id,total_similarity,regime_compatibility
            FROM conditional_analog_diagnostics WHERE run_id=? AND secid=?
            AND eligibility IN ('STRONG','MEDIUM','WEAK') ORDER BY total_similarity DESC""",
            [similarity_run_id, secid],
        ).fetchall()
        stress_dates = [
            row[0]
            for row in con.execute(
                """SELECT analog_date FROM conditional_analog_diagnostics WHERE run_id=? AND secid=?
                AND eligibility='STRESS_ONLY' QUALIFY row_number() OVER
                (PARTITION BY episode_id ORDER BY total_similarity DESC,analog_date)=1""",
                [similarity_run_id, secid],
            ).fetchall()
        ]
        price_row = con.execute(
                """SELECT close FROM canonical_daily_prices WHERE canonical_secid=?
                AND trade_date<=? ORDER BY trade_date DESC LIMIT 1""",
                [secid, cutoff],
            ).fetchone()
        if not price_row:
            continue
        current_price = float(price_row[0])
        if analogs:
            normalized = _weights(analogs, float(config["similarity_power"]))
            for row, weight in zip(analogs, normalized, strict=True):
                similarity_component = max(float(row[2]) / 100.0, 0) ** float(
                    config["similarity_power"]
                )
                raw_weight = similarity_component * float(row[3])
                weight_rows.append(
                    [
                        run_id, secid, row[0], row[1], similarity_component, row[3], 1.0,
                        raw_weight, weight, True,
                    ]
                )
        for horizon in HORIZONS:
            forecast_rows.append(
                _forecast_row(con, run_id, secid, horizon, current_price, analogs, stress_dates, config)
            )
    if weight_rows:
        con.executemany(
            """INSERT INTO conditional_analog_weights
            (run_id,secid,analog_date,episode_id,similarity_component,regime_component,
            reliability_component,raw_weight,normalized_weight,immutable)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            weight_rows,
        )
    columns = """run_id,secid,horizon,status,current_price,raw_n,effective_sample_size,
    max_weight,weighted_mean_return,weighted_median_return,center_price,expected_low,
    expected_high,plausible_low,plausible_high,weighted_up_frequency,weighted_down_frequency,
    median_max_drawdown,stress_low,stress_high,loo_center_min,loo_center_max,
    loo_width_sensitivity,loo_up_sensitivity,robustness_status,evidence_status,immutable""".replace("\n", "")
    if forecast_rows:
        placeholders = ",".join("?" for _ in columns.split(","))
        con.executemany(
            f"INSERT INTO conditional_forecast_horizons ({columns}) VALUES ({placeholders})",
            forecast_rows,
        )
    details = {"recency_weighting": "none", "historical_reliability": "neutral_until_stage100"}
    con.execute(
        """INSERT INTO conditional_forecast_runs
        (run_id,created_at,cutoff,similarity_run_id,weighting_version,config_signature,
        instruments,forecasts,immutable,production_unchanged,probability_gate_unchanged,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [run_id, datetime.now(UTC), cutoff, similarity_run_id, VERSION, signature,
         len(instruments), len(forecast_rows), json.dumps(details, sort_keys=True)],
    )
    return {"run_id": run_id, "forecasts": len(forecast_rows), "status": "completed", "idempotent": False}
