"""Weighted first-passage research with explicit evidence gating (Stage 99)."""

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

VERSION = "first-passage-v1"


def _config() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[3] / "config" / "conditional_forecasting.yaml"
    raw = path.read_bytes()
    return yaml.safe_load(raw)["barrier_analytics"], hashlib.sha256(raw).hexdigest()


def first_passage(path: Any, upper: float, lower: float, horizon: int) -> tuple[str, int | None]:
    values = np.asarray(path, dtype=float)[: horizon + 1]
    upper_hits = np.flatnonzero(values >= upper)
    lower_hits = np.flatnonzero(values <= -lower)
    upper_time = int(upper_hits[0]) if len(upper_hits) else None
    lower_time = int(lower_hits[0]) if len(lower_hits) else None
    if upper_time is None and lower_time is None:
        return "neither", None
    if lower_time is None or (upper_time is not None and upper_time < lower_time):
        return "upper", upper_time
    return "lower", lower_time


def evaluate_barriers(
    paths: list[Any], weights: Any, upper: float, lower: float, horizon: int
) -> dict[str, Any]:
    normalized = np.asarray(weights, dtype=float)
    if not paths or normalized.sum() <= 0:
        return {
            "raw_n": 0, "ess": 0.0, "upper": None, "lower": None, "neither": None,
            "upper_count": 0, "lower_count": 0, "neither_count": 0,
            "time_upper": None, "time_lower": None,
        }
    normalized /= normalized.sum()
    outcomes = [first_passage(path, upper, lower, horizon) for path in paths]
    labels = np.asarray([item[0] for item in outcomes])
    upper_mask, lower_mask = labels == "upper", labels == "lower"
    neither_mask = labels == "neither"
    upper_times = np.asarray(
        [item[1] if item[0] == "upper" else np.nan for item in outcomes], dtype=float
    )
    lower_times = np.asarray(
        [item[1] if item[0] == "lower" else np.nan for item in outcomes], dtype=float
    )
    return {
        "raw_n": len(paths),
        "ess": effective_sample_size(normalized),
        "upper": float(normalized[upper_mask].sum()),
        "lower": float(normalized[lower_mask].sum()),
        "neither": float(normalized[neither_mask].sum()),
        "upper_count": int(upper_mask.sum()),
        "lower_count": int(lower_mask.sum()),
        "neither_count": int(neither_mask.sum()),
        "time_upper": weighted_quantile(upper_times, normalized, 0.5) if upper_mask.any() else None,
        "time_lower": weighted_quantile(lower_times, normalized, 0.5) if lower_mask.any() else None,
    }


def _load_paths(
    con: duckdb.DuckDBPyConnection, run_id: str, secid: str
) -> tuple[list[np.ndarray], np.ndarray]:
    rows = con.execute(
        """SELECT analog_date,session,normalized_return,weight FROM conditional_analog_paths
        WHERE run_id=? AND secid=? AND scenario_role='EXPECTED_CONDITIONAL' AND weight>0
        ORDER BY analog_date,session""",
        [run_id, secid],
    ).fetchall()
    grouped: dict[Any, list[float]] = {}
    weights: dict[Any, float] = {}
    for analog_date, _, value, weight in rows:
        grouped.setdefault(analog_date, []).append(float(value))
        weights[analog_date] = float(weight)
    dates = sorted(grouped)
    return [np.asarray(grouped[date]) for date in dates], np.asarray([weights[date] for date in dates])


def build_barrier_analytics(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    config, signature = _config()
    source = con.execute(
        """SELECT run_id,cutoff FROM conditional_path_runs
        WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed conditional path run is required")
    path_run_id, cutoff = source
    run_id = hashlib.sha256(f"{VERSION}|{path_run_id}|{signature}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT rows_created,status FROM conditional_barrier_runs WHERE run_id=?", [run_id]
    ).fetchone()
    if existing:
        return {"run_id": run_id, "rows": existing[0], "status": existing[1], "idempotent": True}
    barriers = [(float(value), float(value), True) for value in config["symmetric"]]
    barriers += [(float(upper), float(lower), False) for upper, lower in config["asymmetric"]]
    result_rows = []
    for secid in SECIDS:
        paths, weights = _load_paths(con, path_run_id, secid)
        for horizon in config["horizons"]:
            for upper, lower, symmetric in barriers:
                result = evaluate_barriers(paths, weights, upper, lower, int(horizon))
                enough = result["ess"] >= float(config["minimum_effective_sample"])
                status = "ready_research_unvalidated" if paths else "insufficient_conditional_history"
                evidence = "uncalibrated_research" if enough else "low_evidence"
                result_rows.append([
                    run_id, secid, horizon, upper, lower, symmetric, status,
                    result["raw_n"], result["ess"], result["upper"], result["lower"],
                    result["neither"], result["upper_count"], result["lower_count"],
                    result["neither_count"], result["time_upper"], result["time_lower"],
                    evidence, False, True,
                ])
    con.executemany(
        """INSERT INTO conditional_barrier_results
        (run_id,secid,horizon,upper_barrier,lower_barrier,is_symmetric,status,raw_n,
        effective_sample_size,upper_first_frequency,lower_first_frequency,neither_frequency,
        upper_first_count,lower_first_count,neither_count,median_time_upper,median_time_lower,
        evidence_status,probability_published,immutable)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        result_rows,
    )
    details = {"frequencies_not_calibrated_probabilities": True, "custom_barriers_supported": True}
    con.execute(
        """INSERT INTO conditional_barrier_runs
        (run_id,created_at,cutoff,path_run_id,barrier_version,config_signature,rows_created,
        immutable,production_unchanged,probability_gate_unchanged,status,details_json)
        VALUES (?,?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [run_id, datetime.now(UTC), cutoff, path_run_id, VERSION, signature,
         len(result_rows), json.dumps(details, sort_keys=True)],
    )
    return {"run_id": run_id, "rows": len(result_rows), "status": "completed", "idempotent": False}
