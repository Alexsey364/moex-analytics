"""Stage 88 objective-aware outcomes; historical replay never masquerades as live."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS

from .schema import ensure_schema

VERSION = "decision-outcome-memory-v1"
ALLOWED = {"consider", "hold", "do_not_increase", "risk_restriction", "insufficient_data"}


def _decision_type(status: str, portfolio_action: str) -> str:
    if status == "do_not_increase" and "концентр" in portfolio_action.lower():
        return "risk_restriction"
    return {
        "wait": "hold",
        "do_not_increase": "do_not_increase",
        "insufficient_data": "insufficient_data",
        "consider": "consider",
    }.get(status, "hold")


def _objective(decision_type: str) -> str:
    return {
        "risk_restriction": "subsequent_downside_and_concentration_risk",
        "do_not_increase": "subsequent_downside",
        "consider": "relative_return_and_drawdown",
        "hold": "path_stability_not_directional_correctness",
        "insufficient_data": "coverage_only_no_performance_judgement",
    }[decision_type]


def _capture_live(con: Any) -> int:
    rows = con.execute(
        """SELECT snapshot_id,cutoff,secid,status,portfolio_action,source_report_id
        FROM daily_decision_states"""
    ).fetchall()
    inserted = 0
    for snapshot_id, cutoff, secid, status, action, report_id in rows:
        decision_type = _decision_type(status, action)
        decision_id = hashlib.sha256(f"live|{snapshot_id}|{secid}".encode()).hexdigest()[:24]
        before = con.execute(
            "SELECT 1 FROM decision_outcome_records WHERE decision_id=?", [decision_id]
        ).fetchone()
        con.execute(
            """INSERT OR IGNORE INTO decision_outcome_records (
            decision_id,source_type,decision_date,secid,decision_type,source_snapshot_id,
            source_report_id,created_at,immutable) VALUES (?,'live_daily_snapshot',?,?,?,?,?,?,TRUE)""",
            [decision_id, cutoff, secid, decision_type, snapshot_id, report_id, datetime.now(UTC)],
        )
        inserted += int(before is None)
    return inserted


def _capture_saved_research(con: Any) -> int:
    """Use saved historical reports only; do not reconstruct missing daily verdicts."""
    try:
        rows = con.execute(
            """SELECT r.report_id,r.analysis_cutoff,s.secid,s.action_group,s.portfolio_view
            FROM human_daily_reports r JOIN human_instrument_synthesis s USING(report_id)
            WHERE r.report_id IN (SELECT arg_max(report_id,created_at) FROM human_daily_reports
            GROUP BY analysis_cutoff)"""
        ).fetchall()
    except Exception:
        return 0
    inserted = 0
    for report_id, cutoff, secid, status, action in rows:
        decision_type = _decision_type(status, action)
        decision_id = hashlib.sha256(f"research|{report_id}|{secid}".encode()).hexdigest()[:24]
        before = con.execute(
            "SELECT 1 FROM decision_outcome_records WHERE decision_id=?", [decision_id]
        ).fetchone()
        con.execute(
            """INSERT OR IGNORE INTO decision_outcome_records (
            decision_id,source_type,decision_date,secid,decision_type,source_snapshot_id,
            source_report_id,created_at,immutable)
            VALUES (?,'historical_rule_replay',?,?,?,?,?,?,TRUE)""",
            [decision_id, cutoff, secid, decision_type, None, report_id, datetime.now(UTC)],
        )
        inserted += int(before is None)
    return inserted


def _prices(con: Any, secid: str, cutoff: Any) -> list[tuple[Any, float]]:
    return con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? "
        "AND trade_date>=? AND close>0 ORDER BY trade_date",
        [secid, cutoff],
    ).fetchall()


def _evaluate(con: Any) -> int:
    records = con.execute(
        "SELECT decision_id,decision_date,secid,decision_type FROM decision_outcome_records"
    ).fetchall()
    written = 0
    for decision_id, cutoff, secid, decision_type in records:
        issuer = _prices(con, secid, cutoff)
        market = _prices(con, "IMOEX", cutoff)
        if not issuer or not market or issuer[0][0] != cutoff:
            continue
        market_map = dict(market)
        for horizon in HORIZONS:
            if len(issuer) <= horizon:
                continue
            maturity, _end = issuer[horizon]
            if maturity not in market_map or cutoff not in market_map:
                continue
            start = issuer[0][1]
            path = np.array([value / start - 1 for _, value in issuer[1 : horizon + 1]])
            absolute = float(path[-1])
            market_return = float(market_map[maturity] / market_map[cutoff] - 1)
            daily = np.diff(np.r_[0.0, path])
            rank_change = None
            try:
                ranks = con.execute(
                    "SELECT cutoff,tie_group FROM current_portfolio_ranking WHERE secid=? "
                    "AND cutoff IN (?,?) ORDER BY cutoff",
                    [secid, cutoff, maturity],
                ).fetchall()
                rank_change = float(ranks[-1][1] - ranks[0][1]) if len(ranks) == 2 else None
            except Exception:
                pass
            before = con.execute(
                "SELECT 1 FROM decision_realized_outcomes WHERE decision_id=? AND horizon=?",
                [decision_id, horizon],
            ).fetchone()
            con.execute(
                """INSERT OR IGNORE INTO decision_realized_outcomes (
                decision_id,horizon,maturity_date,absolute_return,relative_return,max_drawdown,
                mfe,volatility,rank_change,objective_metric,outcome_status,evaluated_at,immutable)
                VALUES (?,?,?,?,?,?,?,?,?,?,'matured',?,TRUE)""",
                [
                    decision_id,
                    horizon,
                    maturity,
                    absolute,
                    absolute - market_return,
                    float(path.min()),
                    float(path.max()),
                    float(np.std(daily) * np.sqrt(252)),
                    rank_change,
                    _objective(decision_type),
                    datetime.now(UTC),
                ],
            )
            written += int(before is None)
    return written


def _scorecards(con: Any) -> int:
    con.execute("DELETE FROM decision_outcome_scorecards")
    groups = con.execute(
        """SELECT r.source_type,r.decision_type,o.horizon,count(*),median(o.absolute_return),
        median(o.relative_return),median(o.max_drawdown),median(o.mfe),any_value(o.objective_metric)
        FROM decision_outcome_records r JOIN decision_realized_outcomes o USING(decision_id)
        GROUP BY r.source_type,r.decision_type,o.horizon"""
    ).fetchall()
    rows = [
        [*row, "insufficient_sample" if row[3] < 30 else "descriptive_evidence", datetime.now(UTC)]
        for row in groups
    ]
    if rows:
        con.executemany("INSERT INTO decision_outcome_scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def update_decision_outcomes(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    live = _capture_live(con)
    research = _capture_saved_research(con)
    matured = _evaluate(con)
    scorecards = _scorecards(con)
    counts = dict(
        con.execute(
            "SELECT source_type,count(*) FROM decision_outcome_records GROUP BY source_type"
        ).fetchall()
    )
    return {
        "live_records": counts.get("live_daily_snapshot", 0),
        "research_records": counts.get("historical_rule_replay", 0),
        "inserted_live": live,
        "inserted_research": research,
        "matured_new": matured,
        "scorecards": scorecards,
        "status": "completed",
        "production_changes": 0,
        "probability_gate_changed": False,
    }
