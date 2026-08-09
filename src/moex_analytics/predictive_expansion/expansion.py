"""Bounded, resumable wrapper around the existing page-checkpointed equity backlog."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime

import yaml

from moex_analytics import market_history
from moex_analytics.config import PROJECT_ROOT
from moex_analytics.database import database_path

from .schema import DDL

CONFIG_PATH = PROJECT_ROOT / "config" / "predictive_data_expansion.yaml"


def ensure_schema(con) -> None:
    con.execute(DDL)


def load_config(path=CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _raw_size() -> int:
    root = PROJECT_ROOT / "data" / "raw" / "market_history"
    return sum(path.stat().st_size for path in root.rglob("*.json")) if root.exists() else 0


def _paused(con) -> bool:
    row = con.execute(
        "SELECT control_value FROM market_history_control WHERE control_key='paused'"
    ).fetchone()
    return bool(row and row[0] == "true")


def _checkpoint(con, run_id: str, number: int, initial: dict, batch: dict, started: float) -> None:
    current = market_history.coverage(con)
    con.execute(
        """INSERT OR REPLACE INTO stage30_expansion_checkpoints VALUES
        (?,?,current_timestamp,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            number,
            batch.get("run_id"),
            current["securities"] - initial["securities"],
            current["active"],
            current["inactive"],
            current["completed_jobs"],
            current["rows"],
            batch.get("requests", 0),
            batch.get("failures", 0),
            time.perf_counter() - started,
            batch.get("database_growth", 0),
            batch.get("raw_growth", 0),
            "completed",
            json.dumps(batch, default=str),
        ],
    )


def run_equity_expansion(con, *, target: int | None = None, config: dict | None = None) -> dict:
    """Continue—not reseed—the backlog until target or an explicit safety boundary."""
    ensure_schema(con)
    cfg = config or load_config()
    safety = cfg["safety"]
    target = target or int(cfg["targets"]["minimum_securities"])
    initial = market_history.coverage(con, save=True)
    started_clock = time.perf_counter()
    started_at = datetime.now(UTC)
    db_before = database_path().stat().st_size
    raw_before = _raw_size()
    run_id = hashlib.sha256(f"stage30:{started_at.isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        """INSERT INTO stage30_expansion_runs VALUES
        (?,?,NULL,'running',?,?,?,?,?,0,0,NULL,0,0,NULL,0,?)""",
        [
            run_id,
            started_at,
            target,
            initial["securities"],
            initial["securities"],
            initial["rows"],
            initial["rows"],
            json.dumps(cfg),
        ],
    )
    total_requests = total_errors = consecutive_failed = checkpoint = 0
    last_checkpoint_count = initial["securities"]
    stop_reason = "target_reached"
    while True:
        current = market_history.coverage(con)
        elapsed = time.perf_counter() - started_clock
        disk_growth = database_path().stat().st_size - db_before + _raw_size() - raw_before
        if current["securities"] >= target:
            break
        if _paused(con):
            stop_reason = "paused"
            break
        if elapsed >= float(safety["max_runtime_minutes"]) * 60:
            stop_reason = "max_runtime"
            break
        if total_requests >= int(safety["max_requests"]):
            stop_reason = "max_requests"
            break
        if disk_growth >= float(safety["max_disk_growth_gb"]) * 1024**3:
            stop_reason = "max_disk_growth"
            break
        batch = market_history.run_batch(
            con,
            jobs=int(safety["batch_jobs"]),
            pages_per_job=int(safety["pages_per_job"]),
            pause=float(safety["request_pause_seconds"]),
        )
        total_requests += int(batch.get("requests", 0))
        total_errors += int(batch.get("failures", 0))
        progress = int(batch.get("securities_added", 0)) > 0 or int(batch.get("rows_inserted", 0)) > 0
        consecutive_failed = 0 if progress else consecutive_failed + 1
        current = market_history.coverage(con)
        threshold = int(safety["checkpoint_securities"])
        if current["securities"] - last_checkpoint_count >= threshold or not progress:
            checkpoint += 1
            _checkpoint(con, run_id, checkpoint, initial, batch, started_clock)
            last_checkpoint_count = current["securities"]
        if consecutive_failed >= int(safety["max_consecutive_failed_batches"]):
            stop_reason = "no_progress"
            break
    final = market_history.coverage(con, save=True)
    runtime = time.perf_counter() - started_clock
    status = "target_reached" if final["securities"] >= target else "safety_stop"
    con.execute(
        """UPDATE stage30_expansion_runs SET finished_at=current_timestamp,status=?,
        securities_after=?,rows_after=?,requests=?,errors=?,runtime_seconds=?,
        database_growth=?,raw_growth=?,stop_reason=? WHERE run_id=?""",
        [
            status,
            final["securities"],
            final["rows"],
            total_requests,
            total_errors,
            runtime,
            database_path().stat().st_size - db_before,
            _raw_size() - raw_before,
            stop_reason,
            run_id,
        ],
    )
    return {
        "run_id": run_id,
        "status": status,
        "stop_reason": stop_reason,
        "before": initial,
        "after": final,
        "requests": total_requests,
        "errors": total_errors,
        "runtime_seconds": runtime,
        "database_growth": database_path().stat().st_size - db_before,
        "raw_growth": _raw_size() - raw_before,
        "production_changes": 0,
    }


def expansion_status(con) -> dict:
    ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,target_securities,securities_before,securities_after,
        rows_before,rows_after,requests,errors,runtime_seconds,database_growth,
        raw_growth,stop_reason FROM stage30_expansion_runs ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    return {"latest": latest, "coverage": market_history.coverage(con)}
