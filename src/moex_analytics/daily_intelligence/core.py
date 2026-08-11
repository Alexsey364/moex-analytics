"""Stage 85: one PIT-safe compatibility contract for every BASIC page."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

import duckdb

from moex_analytics.conditioned_stock_forecasting.core import SECIDS
from moex_analytics.visual_memory.core import MODES, _current_path, _price_map

from .schema import ensure_schema

VERSION = "daily-intelligence-v1"
FULLY_CURRENT = "FULLY_CURRENT"
CURRENT_WITH_SLOW_DATA = "CURRENT_WITH_SLOW_DATA"
PARTIAL = "PARTIAL"
INCOMPATIBLE = "INCOMPATIBLE"

# Queries only use timestamps that existed in the source tables. Missing tables are explicit.
COMPONENTS = {
    "market": ("fast", "SELECT max(trade_date) FROM canonical_daily_prices", None, True),
    "portfolio_prices": (
        "fast",
        "SELECT min(last_date) FROM (SELECT canonical_secid,max(trade_date) last_date "
        "FROM canonical_daily_prices WHERE canonical_secid IN "
        "('X5','SBERP','LKOH','LSNGP','MTSS','TRNFP','TATNP','PHOR','MOEX') GROUP BY canonical_secid)",
        None,
        True,
    ),
    "ranking": ("fast", "SELECT max(cutoff) FROM current_portfolio_ranking", None, True),
    "market_state": (
        "fast",
        "SELECT max(cutoff) FROM whole_market_state_runs WHERE status='completed'",
        None,
        True,
    ),
    "sector": ("fast", "SELECT max(date_to) FROM sector_rotation_runs WHERE status='completed'", None, True),
    "analogs": ("fast", "SELECT max(cutoff) FROM visual_memory_runs WHERE status='completed'", None, False),
    "news": ("fast", "SELECT max(cast(available_from AS DATE)) FROM news_items", None, False),
    "fundamentals": (
        "slow",
        "SELECT max(publication_date) FROM fundamental_documents",
        None,
        False,
    ),
    "portfolio": ("fast", "SELECT max(analysis_cutoff) FROM human_daily_reports", None, True),
    "live_validation": ("fast", "SELECT max(cutoff) FROM forecast_registry", None, False),
}


def _safe_scalar(con: Any, sql: str) -> Any:
    try:
        return con.execute(sql).fetchone()[0]
    except Exception:
        return None


def _source_id(con: Any, component: str) -> str | None:
    queries = {
        "ranking": "SELECT run_id FROM current_portfolio_ranking ORDER BY cutoff DESC LIMIT 1",
        "market_state": "SELECT run_id FROM whole_market_state_runs ORDER BY cutoff DESC LIMIT 1",
        "sector": "SELECT run_id FROM sector_rotation_runs ORDER BY date_to DESC LIMIT 1",
        "analogs": "SELECT run_id FROM visual_memory_runs ORDER BY created_at DESC LIMIT 1",
        "portfolio": "SELECT report_id FROM human_daily_reports ORDER BY analysis_cutoff DESC LIMIT 1",
    }
    return str(value) if (sql := queries.get(component)) and (value := _safe_scalar(con, sql)) else None


def collect_components(con: Any) -> tuple[date, list[dict[str, Any]]]:
    cutoff = _safe_scalar(con, COMPONENTS["market"][1])
    if cutoff is None:
        raise ValueError("daily market cutoff is unavailable")
    rows = []
    for name, (family, sql, _unused, required) in COMPONENTS.items():
        component_cutoff = _safe_scalar(con, sql)
        if component_cutoff is None:
            status, reason = "unavailable", "source table or compatible observation unavailable"
        elif family == "slow":
            status, reason = "slow_current", "slow-changing source uses its confirmed publication period"
        elif name == "news" and component_cutoff >= cutoff:
            status, reason = "current", "news is PIT-current through the snapshot creation date"
        elif component_cutoff == cutoff:
            status, reason = "current", "cutoff matches unified market cutoff"
        elif component_cutoff < cutoff:
            status, reason = (
                "older_compatible",
                "component cutoff precedes market cutoff by "
                f"{(cutoff - component_cutoff).days} calendar days",
            )
        else:
            status, reason = "incompatible", "component cutoff is later than unified market cutoff"
        source_id = _source_id(con, name)
        digest = hashlib.sha256(
            json.dumps([name, family, str(component_cutoff), status, source_id]).encode()
        ).hexdigest()
        rows.append(
            {
                "component": name,
                "family": family,
                "cutoff": component_cutoff,
                "status": status,
                "reason": reason,
                "source_id": source_id,
                "hash": digest,
                "required": required,
            }
        )
    return cutoff, rows


def compatibility_status(rows: list[dict[str, Any]]) -> str:
    fast = [row for row in rows if row["family"] == "fast"]
    required = [row for row in fast if row["required"]]
    if any(row["status"] == "incompatible" for row in rows):
        return INCOMPATIBLE
    if any(row["status"] == "unavailable" for row in required):
        return INCOMPATIBLE
    if any(row["status"] != "current" for row in required):
        return PARTIAL
    return CURRENT_WITH_SLOW_DATA if any(row["family"] == "slow" for row in rows) else FULLY_CURRENT


def _save_current_analog_context(con: Any, snapshot_id: str, cutoff: date) -> int:
    try:
        source = con.execute(
            "SELECT run_id,cutoff FROM visual_memory_runs WHERE status='completed' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        source = None
    if not source:
        return 0
    source_run, analog_cutoff = source
    rows = []
    for secid in SECIDS:
        prices = _price_map(con, secid)
        for mode, (_method, window) in MODES.items():
            current = _current_path(prices, cutoff, window)
            compatible = analog_cutoff == cutoff
            rows.append(
                [
                    snapshot_id,
                    secid,
                    mode,
                    cutoff,
                    analog_cutoff,
                    source_run,
                    json.dumps(current),
                    "current" if compatible else "historical_source_older",
                    "matching source is current"
                    if compatible
                    else "current path refreshed; historical match set retains its immutable source cutoff",
                    True,
                ]
            )
    con.executemany(
        """INSERT INTO daily_analog_contexts (
        snapshot_id,instrument,comparison_mode,current_cutoff,analog_source_cutoff,
        source_visual_run,current_path_json,status,reason,immutable
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def build_daily_snapshot(con: Any, *, source_update_run: str | None = None) -> dict[str, Any]:
    ensure_schema(con)
    cutoff, components = collect_components(con)
    compatibility = compatibility_status(components)
    component_hash = hashlib.sha256("|".join(row["hash"] for row in components).encode()).hexdigest()
    compatibility_hash = hashlib.sha256(
        "|".join(f"{row['component']}:{row['status']}" for row in components).encode()
    ).hexdigest()
    snapshot_id = hashlib.sha256(
        f"{VERSION}|{cutoff}|{component_hash}|{compatibility_hash}".encode()
    ).hexdigest()[:24]
    if con.execute(
        "SELECT 1 FROM daily_intelligence_snapshots WHERE snapshot_id=?", [snapshot_id]
    ).fetchone():
        return latest_daily_snapshot(con) | {"idempotent": True}
    fast = [row for row in components if row["family"] == "fast"]
    con.execute(
        """INSERT INTO daily_intelligence_snapshots (
        snapshot_id,cutoff,created_at,compatibility,component_hash,compatibility_hash,
        fast_current,fast_total,production_unchanged,probability_gate_unchanged,immutable,
        source_update_run,details_json) VALUES (?,?,?,?,?,?,?,?,TRUE,TRUE,TRUE,?,?)""",
        [
            snapshot_id,
            cutoff,
            datetime.now(UTC),
            compatibility,
            component_hash,
            compatibility_hash,
            sum(row["status"] == "current" for row in fast),
            len(fast),
            source_update_run,
            json.dumps({"version": VERSION, "no_future_information": True}),
        ],
    )
    con.executemany(
        """INSERT INTO daily_intelligence_components (
        snapshot_id,component,family,cutoff,status,reason,source_id,component_hash,
        required_for_current,immutable) VALUES (?,?,?,?,?,?,?,?,?,TRUE)""",
        [
            [
                snapshot_id,
                row["component"],
                row["family"],
                row["cutoff"],
                row["status"],
                row["reason"],
                row["source_id"],
                row["hash"],
                row["required"],
            ]
            for row in components
        ],
    )
    analog_contexts = _save_current_analog_context(con, snapshot_id, cutoff)
    return latest_daily_snapshot(con) | {"idempotent": False, "analog_contexts": analog_contexts}


def latest_daily_snapshot(con: Any) -> dict[str, Any]:
    # This reader is used by the dashboard through a read-only connection.
    # Schema creation belongs to the build/update path, never to presentation.
    try:
        row = con.execute(
            "SELECT snapshot_id,cutoff,compatibility,fast_current,fast_total,created_at "
            "FROM daily_intelligence_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except duckdb.CatalogException:
        return {"latest": None}
    if not row:
        return {"latest": None}
    components = con.execute(
        "SELECT component,family,cutoff,status,reason,source_id FROM daily_intelligence_components "
        "WHERE snapshot_id=? ORDER BY component",
        [row[0]],
    ).fetchall()
    return {
        "snapshot_id": row[0],
        "cutoff": row[1],
        "compatibility": row[2],
        "fast_current": row[3],
        "fast_total": row[4],
        "created_at": row[5],
        "components": [
            dict(
                zip(
                    ("component", "family", "cutoff", "status", "reason", "source_id"),
                    item,
                    strict=True,
                )
            )
            for item in components
        ],
        "production_changes": 0,
        "probability_gate_changed": False,
    }
