"""Reproducible SBER fundamental history pipeline."""

from __future__ import annotations

import time

import duckdb

from ..config import PROJECT_ROOT, load_settings
from .backtest import run as backtest
from .confidence import calculate_current
from .derived import build as build_derived
from .documents import discover, download
from .fact_scenarios import calculate as calculate_fact_scenarios
from .history import import_validated, install_regimes, parse_downloaded, validate
from .point_in_time import build_snapshots


def status(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        """SELECT count(*),count(*) FILTER(validation_status='validated'),
        count(*) FILTER(validation_status='requires_manual_review'),min(period_end),max(period_end)
        FROM fundamental_documents"""
    ).fetchone()
    return {
        "documents": row[0],
        "validated": row[1],
        "manual_review": row[2],
        "period_from": str(row[3]) if row[3] else None,
        "period_to": str(row[4]) if row[4] else None,
        "metrics": con.execute("SELECT count(*) FROM fundamental_metric_values").fetchone()[0],
        "snapshots": con.execute("SELECT count(*) FROM fundamental_snapshots").fetchone()[0],
        "backtest_rows": con.execute("SELECT count(*) FROM fundamental_backtest_results").fetchone()[0],
    }


def update(con: duckdb.DuckDBPyConnection) -> dict:
    started = time.perf_counter()
    before = status(con)
    discovered = discover(con)
    raw = PROJECT_ROOT / load_settings()["paths"]["raw_data"] / "fundamentals" / "sber"
    downloaded = download(con, raw)
    parsed = parse_downloaded(con)
    checked = validate(con)
    imported = import_validated(con)
    install_regimes(con)
    if imported == 0 and before["validated"] > 0:
        return {
            "status": "no_change",
            "rows_written": 0,
            "duration_seconds": time.perf_counter() - started,
            "discover": discovered,
            "download": downloaded,
            "parse": parsed,
            "validate": checked,
            "state": status(con),
        }
    snapshots = build_snapshots(con)
    derived = build_derived(con)
    confidence = calculate_current(con)
    valuation = calculate_fact_scenarios(con, PROJECT_ROOT / "config" / "sber_fundamental_history.yaml")
    tested = backtest(con)
    return {
        "status": "success",
        "duration_seconds": time.perf_counter() - started,
        "discover": discovered,
        "download": downloaded,
        "parse": parsed,
        "validate": checked,
        "imported": imported,
        "snapshots": snapshots,
        "derived": derived,
        "confidence": confidence,
        "valuation": valuation,
        "backtest": tested,
        "state": status(con),
    }
