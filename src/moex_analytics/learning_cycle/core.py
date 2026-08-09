"""Checkpointed research orchestration with explicit manual promotion boundary."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from moex_analytics.feature_learning import run_feature_learning
from moex_analytics.market_memory import run_market_memory
from moex_analytics.meta_learning import run_meta_learning
from moex_analytics.portfolio_learning import run_portfolio_learning
from moex_analytics.portfolio_research.daily_governance import build_governance_metrics
from moex_analytics.portfolio_research.forecast_scorecards import update_forecast_scorecards
from moex_analytics.uncertainty import run_calibration_audit

from .schema import DDL

STAGES = (
    "freeze_and_tournament",
    "feature_learning",
    "analog_memory",
    "calibration",
    "meta_learning",
    "portfolio_research",
    "champions_and_report",
)
MINIMUM_LIVE_N = 100


def ensure_schema(con) -> None:
    con.execute(DDL)


def _dataset_id(con) -> str:
    state = con.execute(
        "SELECT count(*),max(trade_date),count(DISTINCT canonical_secid) FROM canonical_daily_prices"
    ).fetchone()
    return hashlib.sha256(repr(state).encode()).hexdigest()[:20]


def _latest_completed(con, table: str) -> str | None:
    allowed = {
        "tournament_runs",
        "feature_learning_runs",
        "market_memory_runs",
        "calibration_runs",
        "meta_learning_runs",
        "portfolio_learning_runs",
    }
    if table not in allowed:
        raise ValueError("unsupported checkpoint table")
    row = con.execute(
        f"SELECT run_id FROM {table} WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _checkpoint(con, run_id: str, stage: int, status: str, component: str | None, details: dict) -> None:
    con.execute(
        """INSERT OR REPLACE INTO learning_cycle_checkpoints VALUES
        (?,?,?, ?,coalesce((SELECT started_at FROM learning_cycle_checkpoints
        WHERE run_id=? AND stage=?),current_timestamp),current_timestamp,?,?)""",
        [
            run_id,
            stage,
            STAGES[stage - 1],
            status,
            run_id,
            stage,
            component,
            json.dumps(details, default=str),
        ],
    )
    con.execute("UPDATE learning_cycle_runs SET current_stage=? WHERE run_id=?", [STAGES[stage - 1], run_id])


def _build_champions(con, run_id: str) -> int:
    tournament = _latest_completed(con, "tournament_runs")
    if not tournament:
        return 0
    rows = con.execute(
        """SELECT l.secid,l.horizon,l.winner,l.status,
        r.advantage,r.probability_allowed FROM tournament_leaderboard l
        LEFT JOIN tournament_results r ON r.run_id=l.run_id AND r.secid=l.secid
        AND r.horizon=l.horizon AND r.model=l.winner AND r.split='untouched_holdout'
        WHERE l.run_id=?""",
        [tournament],
    ).fetchall()
    for secid, horizon, challenger, status, advantage, probability_allowed in rows:
        live = con.execute(
            """SELECT count(*),avg(o.direction_correct::INTEGER) FROM forecast_registry f
            JOIN forecast_outcomes o USING(forecast_id) WHERE f.secid=? AND f.horizon_sessions=?
            AND o.outcome_status='matured'""",
            [secid, horizon],
        ).fetchone()
        live_n, live_score = int(live[0]), live[1]
        champion = "baseline"  # production remains frozen until an explicit manual review.
        con.execute(
            "INSERT OR REPLACE INTO model_champion_table VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                secid,
                horizon,
                champion,
                challenger,
                advantage,
                live_n,
                live_score,
                status,
                bool(probability_allowed),
            ],
        )
        oos_ok = bool(advantage is not None and advantage > 0)
        calibration_ok = bool(probability_allowed)
        review_status = (
            "eligible_for_review"
            if live_n >= MINIMUM_LIVE_N and oos_ok and calibration_ok
            else ("continue_shadow" if status == "shadow_candidate" else "not_eligible")
        )
        con.execute(
            "INSERT OR REPLACE INTO learning_promotion_review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                secid,
                horizon,
                challenger,
                MINIMUM_LIVE_N,
                live_n,
                oos_ok,
                False,
                calibration_ok,
                status == "shadow_candidate",
                True,
                False,
                review_status,
                False,
                "manual review required; no automatic production promotion",
            ],
        )
        version = hashlib.sha256(f"{tournament}:{secid}:{horizon}:{challenger}".encode()).hexdigest()[:20]
        con.execute(
            """INSERT OR IGNORE INTO learning_model_versions VALUES
            (?,?,?,?,?,'shadow',current_timestamp,TRUE,FALSE)""",
            [version, tournament, secid, horizon, challenger],
        )
    return len(rows)


def _build_journal(con, run_id: str) -> int:
    rows = con.execute(
        """SELECT f.forecast_id,f.secid,f.horizon_sessions,f.model_version,f.cutoff,
        o.actual_return,o.direction_correct,f.regime_status FROM forecast_registry f
        JOIN forecast_outcomes o USING(forecast_id) WHERE o.outcome_status='matured'"""
    ).fetchall()
    for row in rows:
        diagnostic = "error recorded; no causal attribution" if not row[6] else "correct direction recorded"
        con.execute(
            "INSERT OR IGNORE INTO learning_journal_v2 VALUES (?,?,?,?,?,?,?,?,?,NULL,FALSE,?,FALSE,TRUE)",
            [run_id, *row, diagnostic],
        )
    return len(rows)


def run_controlled_daily(con) -> dict:
    """Update evidence only; never refit coefficients or promote a model."""
    ensure_schema(con)
    run_id = hashlib.sha256(f"daily:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    forecast = update_forecast_scorecards(con)
    governance = build_governance_metrics(con)
    status = forecast["status"]
    new_forecasts = int(forecast["capture"].get("created", 0))
    matured = int(forecast["evaluation"].get("matured", 0))
    models = int(governance.get("rolling_metrics", 0))
    details = {"forecast": forecast, "governance": governance}
    con.execute(
        """INSERT INTO controlled_daily_runs VALUES
        (?,current_timestamp,'completed',?,?,?,?,?,FALSE,?,FALSE,0,?)""",
        [
            run_id,
            0,
            new_forecasts,
            matured,
            models,
            0,
            "no live maturity progress" if matured == 0 else "matured evidence updated",
            json.dumps(details, default=str),
        ],
    )
    return {
        "run_id": run_id,
        "new_rows": 0,
        "new_forecasts": new_forecasts,
        "matured": matured,
        "models_checked": models,
        "feature_scorecards_updated": 0,
        "retrained": False,
        "production_changes": 0,
        "live_status": status.get("live_status"),
    }


def _write_report(con, run_id: str, runtime: float) -> Path:
    champions = con.execute(
        """SELECT secid,horizon,current_champion,best_challenger,
        historical_oos_advantage,live_n,status FROM model_champion_table
        WHERE run_id=? ORDER BY secid,horizon""",
        [run_id],
    ).fetchall()
    lines = [
        "# Controlled Self-Learning Research Report",
        "",
        f"Run: `{run_id}`",
        f"Runtime: {runtime:.2f} seconds",
        "",
        "Production changes: **0**",
        "",
        "## Champion table",
        "",
        "| Instrument | Horizon | Champion | Challenger | OOS advantage | Live N | Status |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for row in champions:
        advantage = "n/a" if row[4] is None else f"{row[4]:+.4f}"
        lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {advantage} | {row[5]} | {row[6]} |")
    lines += [
        "",
        "## Conclusion",
        "",
        "No automatic production promotion was performed. NO EVIDENCE is retained as a valid result.",
    ]
    path = Path("reports") / f"full_learning_cycle_{run_id}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_full_learning_cycle(con, progress=None) -> dict:
    """Run/resume the seven-stage research cycle; never alter production."""
    started = time.perf_counter()
    ensure_schema(con)
    dataset = _dataset_id(con)
    resumable = con.execute(
        """SELECT run_id FROM learning_cycle_runs WHERE dataset_id=?
        AND status IN ('running','interrupted') ORDER BY created_at DESC LIMIT 1""",
        [dataset],
    ).fetchone()
    resumed = bool(resumable)
    run_id = (
        resumable[0]
        if resumable
        else hashlib.sha256(f"{dataset}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    )
    if resumed:
        con.execute("UPDATE learning_cycle_runs SET status='running',resumed=TRUE WHERE run_id=?", [run_id])
    if not resumed:
        con.execute(
            """INSERT INTO learning_cycle_runs VALUES
            (?,?,current_timestamp,NULL,'running',NULL,NULL,FALSE,0,?)""",
            [run_id, dataset, "manual research cycle; no production promotion"],
        )
    completed = {
        row[0]
        for row in con.execute(
            "SELECT stage FROM learning_cycle_checkpoints WHERE run_id=? AND status='completed'", [run_id]
        ).fetchall()
    }
    runners = {
        1: lambda: {"run_id": _latest_completed(con, "tournament_runs"), "reused": True},
        2: lambda: run_feature_learning(con),
        3: lambda: run_market_memory(con),
        4: lambda: run_calibration_audit(con),
        5: lambda: run_meta_learning(con),
        6: lambda: run_portfolio_learning(con),
        7: lambda: {"champions": _build_champions(con, run_id), "journal": _build_journal(con, run_id)},
    }
    try:
        for stage, name in enumerate(STAGES, 1):
            if stage in completed:
                continue
            if progress:
                progress(f"Stage {stage}/{len(STAGES)}: {name}")
            _checkpoint(con, run_id, stage, "running", None, {})
            result = runners[stage]()
            if stage == 1 and not result.get("run_id"):
                raise RuntimeError("completed tournament checkpoint is required")
            _checkpoint(con, run_id, stage, "completed", result.get("run_id"), result)
    except Exception:
        con.execute("UPDATE learning_cycle_runs SET status='interrupted' WHERE run_id=?", [run_id])
        raise
    current_runtime = time.perf_counter() - started
    runtime = con.execute(
        """SELECT coalesce(sum(date_diff('millisecond',started_at,finished_at))/1000.0,?)
        FROM learning_cycle_checkpoints WHERE run_id=? AND status='completed'""",
        [current_runtime, run_id],
    ).fetchone()[0]
    report = _write_report(con, run_id, runtime)
    con.execute(
        """UPDATE learning_cycle_runs SET finished_at=current_timestamp,status='completed',
        runtime_seconds=?,production_changes=0 WHERE run_id=?""",
        [runtime, run_id],
    )
    return {
        "run_id": run_id,
        "dataset_id": dataset,
        "runtime_seconds": runtime,
        "resumed": resumed,
        "report": str(report),
        "production_changes": 0,
    }


def learning_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,current_stage,runtime_seconds,resumed,production_changes
        FROM learning_cycle_runs ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    return {"latest": latest}
