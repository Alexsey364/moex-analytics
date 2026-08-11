"""Stage 81 immutable mapping from heterogeneous research to decision evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS

from .schema import ensure_schema

VERSION = "stage81-v1"
BLOCKS = (
    "baseline",
    "market_conditioned",
    "sector_conditioned",
    "ranking",
    "distribution",
    "analog",
    "news",
    "fundamental",
    "valuation",
    "risk",
    "portfolio_concentration",
    "live",
)


def evidence_strength(
    *, gain: float | None, ci_low: float | None, folds: bool | None, sample_n: int | None,
    fresh: bool, leakage_free: bool = True, multiple_testing: bool = True,
) -> tuple[str, bool, str]:
    if not sample_n or sample_n < 50:
        return "INSUFFICIENT_HISTORY", False, "sample below 50"
    if not fresh:
        return "NO_EVIDENCE", False, "source snapshot is stale"
    if not leakage_free:
        return "NO_EVIDENCE", False, "leakage control failed"
    if gain is None:
        return "NO_EVIDENCE", False, "no comparable OOS metric"
    if gain <= 0:
        return "NO_EVIDENCE", False, "candidate did not improve the baseline"
    if ci_low is None or ci_low <= 0:
        return "WEAK_RESEARCH_EVIDENCE", False, "bootstrap interval crosses zero"
    if folds is not True:
        return "UNSTABLE", False, "improvement is not stable across chronological folds"
    if not multiple_testing:
        return "MODERATE_RESEARCH_EVIDENCE", True, "positive stable OOS evidence; multiplicity incomplete"
    return "STRONG_RESEARCH_EVIDENCE", True, "positive OOS gain, positive CI and stable folds"


def _latest(con: duckdb.DuckDBPyConnection, table: str) -> str:
    return con.execute(f"SELECT run_id FROM {table} ORDER BY created_at DESC LIMIT 1").fetchone()[0]


def build_evidence_registry(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    source_runs = {
        "conditioned": _latest(con, "conditioned_stock_runs"),
        "tournament": _latest(con, "whole_market_tournament_runs"),
        "live": _latest(con, "whole_market_live_runs"),
    }
    cutoff = con.execute(
        "SELECT cutoff FROM whole_market_live_runs WHERE run_id=?", [source_runs["live"]]
    ).fetchone()[0]
    signature = f"{VERSION}|{cutoff}|{json.dumps(source_runs, sort_keys=True)}"
    run_id = hashlib.sha256(signature.encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM evidence_registry_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    conditioned = {
        (row[0], row[1]): row[2:]
        for row in con.execute(
            """SELECT secid,horizon,feature_block,observations,baseline_mae,model_mae,improvement,
            ci_low,ci_high,fold_stable,status FROM conditioned_stock_scorecards
            WHERE run_id=? QUALIFY row_number() OVER(PARTITION BY secid,horizon
            ORDER BY improvement DESC)=1""",
            [source_runs["conditioned"]],
        ).fetchall()
    }
    shadow = {
        (row[0], row[1]): row
        for row in con.execute(
            """SELECT instrument,horizon,observations,score,baseline_score,improvement,ci_low,ci_high,
            subperiod_stable,regime_stable,status FROM whole_market_tournament_entries
            WHERE run_id=? AND scope='fusion'""",
            [source_runs["tournament"]],
        ).fetchall()
    }
    live_counts = {
        (row[0], row[1]): row[2]
        for row in con.execute(
            """SELECT secid,horizon,count(*) FROM live_stock_rank_forecasts
            WHERE run_id=? GROUP BY secid,horizon""",
            [source_runs["live"]],
        ).fetchall()
    }
    rows = []
    audits = []
    for instrument in SECIDS:
        for horizon in HORIZONS:
            best = conditioned.get((instrument, horizon))
            for block in BLOCKS:
                model = "not_available"
                n = effective = oos = baseline = gain = relative = low = high = None
                folds = subperiod = regime = None
                quality, freshness = "partial", "fresh"
                status, eligible, reason = "NO_EVIDENCE", False, "no comparable validated block"
                details: dict[str, Any] = {"research_only": True, "probability_allowed": False}
                if block in {"market_conditioned", "sector_conditioned"} and best:
                    feature, n, baseline, oos, gain, low, high, folds, raw_status = best
                    matches = block.split("_")[0] in feature or feature == "full_conditioned"
                    if matches:
                        model = f"ridge:{feature}:stage80.5-v2"
                        effective = n
                        relative = gain / baseline if baseline else None
                        status, eligible, reason = evidence_strength(
                            gain=gain, ci_low=low, folds=folds, sample_n=n, fresh=True,
                            multiple_testing=False,
                        )
                        details["raw_status"] = raw_status
                elif block == "analog" and (instrument, horizon) in shadow:
                    item = shadow[(instrument, horizon)]
                    _, _, n, score, baseline_score, gain, low, high, subperiod, regime, raw_status = item
                    oos, baseline, model = -score, -baseline_score, "stage76-frozen-analog-fusion"
                    effective, relative = n, gain / baseline if baseline else None
                    status = (
                        "MODERATE_RESEARCH_EVIDENCE"
                        if raw_status == "shadow_candidate"
                        else "WEAK_RESEARCH_EVIDENCE"
                    )
                    eligible = raw_status == "shadow_candidate"
                    reason = (
                        "shadow-only after multiplicity; independent fold gate absent"
                        if eligible
                        else "analog evidence is not stable enough"
                    )
                elif block == "live":
                    n = live_counts.get((instrument, horizon), 0)
                    status, reason = "LIVE_TOO_SMALL", "fewer than 50 independent matured outcomes"
                    quality = "valid_pending"
                elif block == "news":
                    model, reason, quality = "context-only", "predictive weight is zero", "partial"
                elif block in {"risk", "portfolio_concentration"}:
                    model = "current-portfolio-risk"
                    status, eligible = "MODERATE_RESEARCH_EVIDENCE", True
                    reason = "eligible as a risk constraint, not alpha"
                    quality = "usable"
                rows.append(
                    [run_id, instrument, horizon, block, model, n, effective, oos, baseline,
                     gain, relative, low, high, folds, subperiod, regime, quality, freshness,
                     live_counts.get((instrument, horizon), 0), status, eligible, reason,
                     json.dumps(details)]
                )
                if block in {"market_conditioned", "sector_conditioned", "ranking", "distribution", "analog"}:
                    role = "direction_context"
                elif block in {"risk", "portfolio_concentration"}:
                    role = "constraint"
                else:
                    role = "informational"
                audits.append([run_id,instrument,horizon,block,eligible,role,reason])
    con.executemany(
        """INSERT INTO evidence_registry_blocks
        (run_id,instrument,horizon,block_type,model_version,sample_n,effective_n,oos_metric,
        baseline_metric,absolute_improvement,relative_improvement,ci_low,ci_high,fold_stable,
        subperiod_stable,regime_stable,data_quality,freshness,live_n,evidence_status,
        decision_eligible,reason,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    con.executemany("INSERT INTO evidence_decision_audit VALUES (?,?,?,?,?,?,?)", audits)
    con.execute(
        """INSERT INTO evidence_registry_runs VALUES (?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [run_id,datetime.now(UTC),cutoff,len(rows),len(SECIDS),VERSION,json.dumps({"source_runs":source_runs,"blocks":list(BLOCKS)})],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        """SELECT count(*),count(DISTINCT instrument),sum(decision_eligible),
        sum(evidence_status='STRONG_RESEARCH_EVIDENCE'),sum(evidence_status='UNSTABLE')
        FROM evidence_registry_blocks WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return dict(
        zip(("blocks", "instruments", "eligible", "strong", "unstable"), row, strict=True)
    ) | {
        "run_id": run_id,
        "status": "completed",
        "production_changes": 0,
        "probability_gate_changed": False,
    }
