"""File-backed live update status; intentionally independent from DuckDB locks."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moex_analytics.config import PROJECT_ROOT

STATUS_PATH = PROJECT_ROOT / "data" / "update_status.local.json"
CANCEL_PATH = PROJECT_ROOT / "data" / "update_cancel.local.flag"
STAGES = (
    ("prices", "MOEX market prices", "MOEX ISS"),
    ("macro", "Trading statistics / macro", "Bank of Russia / MOEX ISS"),
    ("fundamentals", "Fundamentals", "issuer IR"),
    ("dividends_events", "Dividends / events", "MOEX ISS / issuer IR"),
    ("regimes", "Indices / breadth / regimes", "MOEX ISS / local"),
    ("portfolio", "Portfolio analytics", "local"),
    ("forecasts", "Forecast snapshots", "local immutable evidence"),
    ("forecast_evaluation", "Forecast maturity / scorecards", "local market prices"),
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write(state: dict[str, Any], path: Path = STATUS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="update-status-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(path: Path = STATUS_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def start(run_id: str, update_type: str, path: Path = STATUS_PATH) -> dict[str, Any]:
    stamp = now_iso()
    state = {"run_id": run_id, "pid": os.getpid(), "started_at": stamp,
        "update_type": update_type, "current_stage": "starting", "current_source": None,
        "current_dataset": None, "status": "starting", "last_progress_at": stamp,
        "items_total": len(STAGES), "items_done": 0, "requests_total": None,
        "requests_completed": 0, "rows_received": 0, "rows_inserted": 0,
        "rows_revised": 0, "errors": 0, "retries": 0, "events": [], "stages": []}
    _write(state, path)
    return state


def progress(state: dict[str, Any], *, dataset: str, stage: str, source: str,
             status: str = "running", requests: int = 0, rows: int = 0,
             error: str | None = None, duration: float | None = None,
             path: Path = STATUS_PATH) -> dict[str, Any]:
    state.update({"current_dataset": dataset, "current_stage": stage, "current_source": source,
                  "status": status, "last_progress_at": now_iso()})
    state["requests_completed"] += requests
    state["rows_received"] += rows
    state["rows_inserted"] += rows
    state["errors"] += int(error is not None)
    if status not in {"running", "waiting_source", "retrying"}:
        state["items_done"] += 1
        state["stages"].append({"dataset": dataset, "source": source, "status": status,
                                "requests": requests, "rows": rows, "errors": int(bool(error)),
                                "duration_seconds": duration})
    message = f"{source}: {stage} — {status}; requests={requests}; rows={rows}"
    if error:
        message += f"; {error}"
    state["events"] = [*state.get("events", []), {"at": now_iso(), "message": message}][-50:]
    _write(state, path)
    return state


def finish(state: dict[str, Any], status: str, path: Path = STATUS_PATH) -> None:
    state.update({"status": status, "finished_at": now_iso(), "last_progress_at": now_iso()})
    _write(state, path)


def request_cancel(path: Path = CANCEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now_iso(), encoding="ascii")


def cancel_requested(path: Path = CANCEL_PATH) -> bool:
    return path.exists()


def clear_cancel(path: Path = CANCEL_PATH) -> None:
    path.unlink(missing_ok=True)


def health(state: dict[str, Any], now: datetime | None = None,
           slow_seconds: int = 30, stalled_seconds: int = 90) -> str:
    if not state.get("last_progress_at"):
        return "UNKNOWN"
    now = now or datetime.now(UTC)
    age = (now - datetime.fromisoformat(state["last_progress_at"])).total_seconds()
    return "STALLED" if age > stalled_seconds else "SLOW" if age > slow_seconds else "ACTIVE"


def eta_seconds(state: dict[str, Any]) -> float | None:
    done, total = state.get("items_done", 0), state.get("items_total")
    if not total or done < 2 or done >= total:
        return None
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(state["started_at"])).total_seconds()
    return elapsed / done * (total - done)


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def recover_interrupted(path: Path = STATUS_PATH) -> dict[str, Any]:
    state = load(path)
    if state.get("status") in {"starting", "running", "waiting_source", "retrying"} and not process_alive(
        state.get("pid")
    ):
        finish(state, "interrupted", path)
    return state
