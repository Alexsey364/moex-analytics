"""Stage 64 portfolio snapshot completeness audit."""

from __future__ import annotations

import hashlib
import json
from typing import Any

PORTFOLIO = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
VERSION = "portfolio-snapshot-freshness-v2"
DDL = """
CREATE TABLE IF NOT EXISTS snapshot_freshness_runs(
 run_id VARCHAR PRIMARY KEY,cutoff DATE,created_at TIMESTAMP,status VARCHAR,eligible INTEGER,
 details_json JSON,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS instrument_freshness_states(
 run_id VARCHAR,secid VARCHAR,price_date DATE,return_date DATE,feature_date DATE,
 context_date DATE,fundamental_date DATE,price_fresh BOOLEAN,features_fresh BOOLEAN,
 context_fresh BOOLEAN,fundamental_fresh BOOLEAN,rank_eligible BOOLEAN,reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _latest(con: Any, table: str, date_column: str, id_column: str, secid: str):
    try:
        return con.execute(
            f"SELECT max({date_column}) FROM {table} WHERE {id_column}=?", [secid]
        ).fetchone()[0]
    except Exception:
        return None


def audit_current_freshness(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    if cutoff is None:
        raise ValueError("canonical daily prices required")
    return_state = con.execute(
        "SELECT count(*),max(trade_date),sum(hash(trade_date,canonical_secid)) FROM daily_returns"
    ).fetchone()
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}|{return_state}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,eligible FROM snapshot_freshness_runs WHERE run_id=?",
                         [run_id]).fetchone()
    if cached:
        return {"run_id": run_id, "status": cached[0], "eligible": cached[1], "cached": True}
    rows = []
    try:
        global_context_date = con.execute(
            "SELECT max(observation_date) FROM stage30_context_features"
        ).fetchone()[0]
    except Exception:
        global_context_date = None
    for secid in PORTFOLIO:
        price_date = _latest(con, "canonical_daily_prices", "trade_date", "canonical_secid", secid)
        return_date = _latest(con, "daily_returns", "trade_date", "canonical_secid", secid)
        feature_date = _latest(con, "daily_features", "trade_date", "canonical_secid", secid)
        context_date = global_context_date
        # This layer is informational and issuer keyed; absence must not be
        # confused with stale daily market data.
        fundamental_date = _latest(con, "issuer_derived_fundamental_features", "trade_date",
                                   "issuer_group", secid)
        price_fresh = bool(price_date and (cutoff - price_date).days <= 4)
        features_fresh = bool(return_date and (cutoff - return_date).days <= 4)
        context_fresh = context_date is None or (cutoff - context_date).days <= 10
        # Quarterly fundamentals have independent cadence and are not required for ranking.
        fundamental_fresh = bool(fundamental_date and (cutoff - fundamental_date).days <= 550)
        eligible = price_fresh and features_fresh and context_fresh
        reasons = []
        if not price_fresh:
            reasons.append("daily_price_missing_or_stale")
        if not features_fresh:
            reasons.append("daily_return_feature_missing_or_stale")
        if not context_fresh:
            reasons.append("daily_context_missing_or_stale")
        if not fundamental_fresh:
            reasons.append("fundamental_not_fresh_informational_only")
        rows.append([run_id, secid, price_date, return_date, feature_date, context_date,
            fundamental_date, price_fresh, features_fresh, context_fresh, fundamental_fresh,
            eligible, ";".join(reasons) or "eligible", True])
    con.executemany("INSERT INTO instrument_freshness_states (run_id,secid,price_date,return_date,"
                    "feature_date,context_date,fundamental_date,price_fresh,features_fresh,"
                    "context_fresh,fundamental_fresh,rank_eligible,reason,immutable) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    eligible_count = sum(row[11] for row in rows)
    status = "complete" if eligible_count == len(PORTFOLIO) else "incomplete"
    con.execute("INSERT INTO snapshot_freshness_runs (run_id,cutoff,created_at,status,eligible,"
                "details_json,immutable) VALUES (?,?,current_timestamp,?,?,?,true)",
                [run_id, cutoff, status, eligible_count, json.dumps({
                    "required": len(PORTFOLIO), "no_silent_mixing": True,
                    "fundamental_cadence_independent": True, "production_changes": 0})])
    return {"run_id": run_id, "status": status, "eligible": eligible_count, "cached": False}


def freshness_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,cutoff,status,eligible,details_json FROM "
                      "snapshot_freshness_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "cutoff", "status", "eligible", "details"), row, strict=True
    ))
