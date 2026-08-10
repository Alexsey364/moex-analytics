"""Stage 63 prospective-only ranking snapshots and maturity evaluation."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

VERSION = "live-ranking-track-record-v1"
HORIZONS = (20, 60, 120, 250)
DDL = """
CREATE TABLE IF NOT EXISTS live_ranking_snapshots(
 snapshot_id VARCHAR PRIMARY KEY,as_of_date DATE,cutoff DATE,secid VARCHAR,horizon INTEGER,
 portfolio_rank DOUBLE,broader_universe_rank DOUBLE,rank_group VARCHAR,model_version VARCHAR,
 features_hash VARCHAR,source_run_id VARCHAR,created_at TIMESTAMP,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS live_ranking_outcomes(
 snapshot_id VARCHAR PRIMARY KEY,maturity_date DATE,start_price DOUBLE,end_price DOUBLE,
 realized_return DOUBLE,imoex_return DOUBLE,status VARCHAR,evaluated_at TIMESTAMP,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS live_ranking_daily_metrics(
 as_of_date DATE,horizon INTEGER,matured INTEGER,rank_ic DOUBLE,top_group_return DOUBLE,
 bottom_group_return DOUBLE,top_bottom_spread DOUBLE,imoex_relative DOUBLE,rank_stability DOUBLE,
 evidence_status VARCHAR,PRIMARY KEY(as_of_date,horizon));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _price_after_sessions(con: Any, secid: str, cutoff: Any, sessions: int) -> tuple[Any, ...] | None:
    return con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? "
        "AND trade_date>? AND close IS NOT NULL ORDER BY trade_date LIMIT 1 OFFSET ?",
        [secid, cutoff, sessions - 1],
    ).fetchone()


def update_live_rankings(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id,cutoff,dataset_version FROM ranking_research_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    groups_run = con.execute(
        "SELECT run_id FROM rank_group_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not source or not groups_run:
        raise ValueError("completed current ranking groups required")
    source_run, cutoff, model_version = source
    groups = con.execute(
        "SELECT secid,horizon,rank_estimate,group_label FROM current_rank_groups "
        "WHERE run_id=? AND horizon IN (60,120,250)", [groups_run[0]]
    ).df()
    # Stage 63 starts prospectively: only the current saved cutoff is admitted.
    created = 0
    for horizon in HORIZONS:
        sample = groups[groups.horizon == horizon]
        if sample.empty:
            continue
        portfolio_ranks = sample.rank_estimate.rank(pct=True)
        for position, row in enumerate(sample.itertuples()):
            snapshot_id = hashlib.sha256(
                f"{VERSION}|{cutoff}|{row.secid}|{horizon}|{source_run}".encode()
            ).hexdigest()[:24]
            exists = con.execute("SELECT 1 FROM live_ranking_snapshots WHERE snapshot_id=?",
                                 [snapshot_id]).fetchone()
            if exists:
                continue
            features_hash = hashlib.sha256(f"{source_run}|{model_version}".encode()).hexdigest()
            con.execute("INSERT INTO live_ranking_snapshots (snapshot_id,as_of_date,cutoff,secid,"
                        "horizon,portfolio_rank,broader_universe_rank,rank_group,model_version,"
                        "features_hash,source_run_id,created_at,immutable) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,current_timestamp,true)",
                        [snapshot_id, cutoff, cutoff, row.secid, horizon,
                         float(portfolio_ranks.iloc[position]), row.rank_estimate, row.group_label,
                         model_version, features_hash, source_run])
            con.execute("INSERT INTO live_ranking_outcomes (snapshot_id,status,immutable) "
                        "VALUES (?,'pending',true)", [snapshot_id])
            created += 1
    pending = con.execute(
        "SELECT s.snapshot_id,s.secid,s.cutoff,s.horizon FROM live_ranking_snapshots s "
        "JOIN live_ranking_outcomes o USING(snapshot_id) WHERE o.status='pending'"
    ).fetchall()
    matured = 0
    for snapshot_id, secid, snapshot_cutoff, horizon in pending:
        start = con.execute("SELECT close FROM canonical_daily_prices WHERE canonical_secid=? "
                            "AND trade_date<=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1",
                            [secid, snapshot_cutoff]).fetchone()
        end = _price_after_sessions(con, secid, snapshot_cutoff, horizon)
        benchmark_start = con.execute("SELECT close FROM canonical_daily_prices WHERE "
                                      "canonical_secid='IMOEX' AND trade_date<=? ORDER BY trade_date "
                                      "DESC LIMIT 1", [snapshot_cutoff]).fetchone()
        benchmark_end = _price_after_sessions(con, "IMOEX", snapshot_cutoff, horizon)
        if not start or not end:
            continue
        realized = end[1] / start[0] - 1
        imoex = (benchmark_end[1] / benchmark_start[0] - 1
                 if benchmark_start and benchmark_end else np.nan)
        con.execute("UPDATE live_ranking_outcomes SET maturity_date=?,start_price=?,end_price=?,"
                    "realized_return=?,imoex_return=?,status='matured',evaluated_at=current_timestamp "
                    "WHERE snapshot_id=? AND status='pending'",
                    [end[0], start[0], end[1], realized, imoex, snapshot_id])
        matured += 1
    total, pending_count, matured_count = con.execute(
        "SELECT count(*),count(*) FILTER(WHERE status='pending'),"
        "count(*) FILTER(WHERE status='matured') FROM live_ranking_outcomes"
    ).fetchone()
    return {"created": created, "matured_now": matured, "total": total,
            "pending": pending_count, "matured": matured_count,
            "evidence_status": "insufficient_live_evidence" if matured_count < 30 else "available",
            "retrospective_reconstruction": False, "probability_published": False}


def live_ranking_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT count(*),count(*) FILTER(WHERE status='pending'),"
                      "count(*) FILTER(WHERE status='matured') FROM live_ranking_outcomes").fetchone()
    return dict(zip(("total", "pending", "matured"), row, strict=True))
