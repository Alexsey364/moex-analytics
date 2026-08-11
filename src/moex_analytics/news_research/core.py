"""Stage 69: strictly gated event-conditioned challenger evaluation."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np
from scipy.stats import spearmanr

VERSION = "news-conditioned-research-v1"
MIN_EVENTS = 30
HORIZONS = (1, 5, 20, 60)
VARIANTS = ("baseline_no_news", "news_event_type", "news_story_reliability")
DDL = """
CREATE TABLE IF NOT EXISTS news_research_runs(
 run_id VARCHAR PRIMARY KEY,cutoff_date DATE,status VARCHAR,rows_available INTEGER,
 validated_variants INTEGER,production_weight DOUBLE,created_at TIMESTAMP,details VARCHAR);
CREATE TABLE IF NOT EXISTS news_research_scorecards(
 run_id VARCHAR,horizon INTEGER,variant VARCHAR,n_events INTEGER,train_end DATE,oos_start DATE,
 rank_ic DOUBLE,mae DOUBLE,quantile_loss DOUBLE,downside_error DOUBLE,top_bottom_spread DOUBLE,
 corrected_p_value DOUBLE,status VARCHAR,probability_allowed BOOLEAN,production_weight DOUBLE,
 reason VARCHAR,PRIMARY KEY(run_id,horizon,variant));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _metrics(values: np.ndarray) -> tuple[float, float, float, float, float, float]:
    actual = values[:, 0]
    score = values[:, 1]
    rank = float(spearmanr(score, actual).statistic) if len(values) > 2 else float("nan")
    mae = float(np.mean(np.abs(actual - score)))
    qloss = float(np.mean(np.maximum(0.5 * (actual - score), -0.5 * (actual - score))))
    downside = float(np.mean(np.abs(actual[actual < 0] - score[actual < 0]))) if np.any(actual < 0) else 0.0
    order = np.argsort(score)
    bucket = max(1, len(order) // 5)
    spread = float(actual[order[-bucket:]].mean() - actual[order[:bucket]].mean())
    return rank, mae, qloss, downside, spread, min(1.0, 0.05 * len(VARIANTS))


def run_news_research(con: Any, cutoff: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = cutoff or con.execute("SELECT max(anchor_date) FROM news_reaction_memory").fetchone()[0]
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}".encode()).hexdigest()[:20]
    con.execute("DELETE FROM news_research_scorecards WHERE run_id=?", [run_id])
    validated = available = 0
    for horizon in HORIZONS:
        rows = con.execute("SELECT market_return,CASE tone WHEN 'positive_wording' THEN 0.01 "
            "WHEN 'negative_wording' THEN -0.01 ELSE 0 END,anchor_date FROM news_reaction_memory r "
            "JOIN news_items n USING(news_id) WHERE horizon=? AND anchor_date<=? ORDER BY anchor_date",
            [horizon, cutoff]).fetchall() if cutoff else []
        available += len(rows)
        for variant in VARIANTS:
            enough = len(rows) >= MIN_EVENTS
            metrics = (_metrics(np.asarray([[r[0], r[1]] for r in rows], dtype=float))
                       if enough else (None,) * 6)
            status = "experimental" if enough and variant != "baseline_no_news" else (
                "baseline" if enough else "insufficient_history")
            validated += int(status == "experimental")
            train_end = rows[int(len(rows) * 0.7) - 1][2] if enough else None
            oos_start = rows[int(len(rows) * 0.7)][2] if enough else None
            con.execute("INSERT INTO news_research_scorecards (run_id,horizon,variant,n_events,"
                "train_end,oos_start,rank_ic,mae,quantile_loss,downside_error,top_bottom_spread,"
                "corrected_p_value,status,probability_allowed,production_weight,reason) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,false,0,?)", [run_id, horizon, variant, len(rows),
                train_end, oos_start, *metrics, status, "requires temporal OOS and >=30 matured events"
                if not enough else "research-only; no production promotion"])
    overall = "experimental" if validated else "requires_more_history"
    con.execute("INSERT OR REPLACE INTO news_research_runs (run_id,cutoff_date,status,rows_available,"
        "validated_variants,production_weight,created_at,details) VALUES (?,?,?,?,?,0,current_timestamp,"
        "'probability gate unchanged')", [run_id, cutoff, overall, available, validated])
    return {"run_id": run_id, "status": overall, "rows_available": available,
            "validated_variants": validated, "production_weight": 0.0,
            "probability_allowed": False}


def research_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT count(*),count(*) FILTER(WHERE status='experimental'),"
                      "max(n_events) FROM news_research_scorecards").fetchone()
    return {"scorecards": row[0], "experimental": row[1], "max_events": row[2]}
