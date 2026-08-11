"""Resumable Stage 80 full-market research marathon and evidence report."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from moex_analytics.conditioned_stock_forecasting import run_conditioned_stock_research
from moex_analytics.lead_lag_research import run_lead_lag_research
from moex_analytics.market_analog_fusion import run_market_analog_fusion
from moex_analytics.market_forecasting import run_market_forecast_research
from moex_analytics.sector_rotation import run_sector_rotation_research
from moex_analytics.whole_market_live import create_live_forecasts, evaluate_live_forecasts
from moex_analytics.whole_market_state import build_whole_market_state
from moex_analytics.whole_market_tournament import run_whole_market_tournament

from .schema import ensure_schema

VERSION = "stage80-v1"
MAX_RUNTIME_SECONDS = 10 * 60 * 60
REPORT_PATH = Path("reports/full_market_predictive_evidence.md")


def dataset_fingerprint(con: duckdb.DuckDBPyConnection) -> tuple[str, dict[str, Any]]:
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    payload: dict[str, Any] = {}
    specs = {
        "canonical_daily_prices": (
            "SELECT count(*),min(trade_date),max(trade_date),sum(coalesce(close,0)) "
            "FROM canonical_daily_prices"
        ),
        "macro_observations": (
            "SELECT count(*),min(observation_date),max(observation_date),sum(coalesce(value,0)) "
            "FROM macro_observations"
        ),
        "news_items": "SELECT count(*),min(available_from),max(available_from) FROM news_items",
        "regime_market_state_vectors": (
            "SELECT count(*),min(trade_date),max(trade_date) FROM regime_market_state_vectors"
        ),
    }
    for table, query in specs.items():
        payload[table] = con.execute(query).fetchone() if table in tables else None
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest(), payload


def _dashboard_snapshot(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    live_run = con.execute(
        "SELECT run_id FROM whole_market_live_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    market = con.execute(
        """SELECT horizon,qualitative_state,median_return,downside_range,upside_range,regime,status
        FROM live_market_forecasts WHERE run_id=? ORDER BY horizon""",
        [live_run],
    ).fetchall()
    sectors = con.execute(
        """SELECT sector,horizon,predicted_rank,score,status FROM live_sector_rank_forecasts
        WHERE run_id=? ORDER BY horizon,predicted_rank NULLS LAST""",
        [live_run],
    ).fetchall()
    stocks = con.execute(
        """SELECT secid,horizon,predicted_rank,qualitative_state,predicted_return,status
        FROM live_stock_rank_forecasts WHERE run_id=? ORDER BY horizon,predicted_rank""",
        [live_run],
    ).fetchall()
    cutoff = con.execute("SELECT cutoff FROM whole_market_live_runs WHERE run_id=?", [live_run]).fetchone()[0]
    live = {
        "market": len(market),
        "sectors": len(sectors),
        "stocks": len(stocks),
        "matured": con.execute("SELECT count(*) FROM whole_market_live_outcomes").fetchone()[0],
    }
    snapshot_id = hashlib.sha256(f"{run_id}|{live_run}|{cutoff}".encode()).hexdigest()[:24]
    con.execute(
        """INSERT INTO market_dashboard_snapshots
        (snapshot_id,run_id,created_at,cutoff,market_json,sectors_json,stocks_json,live_json,immutable)
        VALUES (?,?,?,?,?,?,?,?,TRUE) ON CONFLICT DO NOTHING""",
        [
            snapshot_id,
            run_id,
            datetime.now(UTC),
            cutoff,
            json.dumps(market, default=str),
            json.dumps(sectors, default=str),
            json.dumps(stocks, default=str),
            json.dumps(live),
        ],
    )
    return {"snapshot_id": snapshot_id, "cutoff": str(cutoff), **live}


def collect_evidence(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    state = con.execute(
        """SELECT trade_date,market_state_label,return_20,drawdown,realized_vol20,breadth_json,
        news_json,regime_json FROM whole_market_state_daily
        WHERE run_id=(SELECT run_id FROM whole_market_state_runs ORDER BY created_at DESC LIMIT 1)
        ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()
    market = con.execute(
        """SELECT horizon,model,balanced_accuracy,baseline_balanced_accuracy,
        improvement_vs_baseline,return_mae,drawdown_mae,volatility_mae,status
        FROM market_forecast_scorecards WHERE sample='frozen_holdout'
        AND run_id=(SELECT run_id FROM market_forecast_runs ORDER BY created_at DESC LIMIT 1)
        QUALIFY row_number() OVER(PARTITION BY horizon ORDER BY improvement_vs_baseline DESC)=1
        ORDER BY horizon"""
    ).fetchall()
    sectors = con.execute(
        """SELECT horizon,rank_ic,top_bottom_spread,status FROM sector_rotation_scorecards
        WHERE sample='frozen_holdout'
        AND run_id=(SELECT run_id FROM sector_rotation_runs ORDER BY created_at DESC LIMIT 1)
        ORDER BY horizon"""
    ).fetchall()
    stocks = con.execute(
        """SELECT secid,horizon,feature_block,improvement,return_correlation,status
        FROM conditioned_stock_scorecards
        WHERE run_id=(SELECT run_id FROM conditioned_stock_runs ORDER BY created_at DESC LIMIT 1)
        QUALIFY row_number() OVER
        (PARTITION BY secid,horizon ORDER BY improvement DESC)=1 ORDER BY secid,horizon"""
    ).fetchall()
    lead_lag = con.execute(
        """SELECT signal,count(*),avg(abs(holdout_correlation)) FROM lead_lag_scorecards
        WHERE status='useful_association'
        AND run_id=(SELECT run_id FROM lead_lag_runs ORDER BY created_at DESC LIMIT 1)
        GROUP BY signal ORDER BY count(*) DESC"""
    ).fetchall()
    analog = con.execute(
        """SELECT count(*),sum(status='experimental'),sum(status='rejected'),avg(improvement),
        avg(direction_accuracy) FROM market_analog_fusion_scorecards
        WHERE run_id=(SELECT run_id FROM market_analog_fusion_runs ORDER BY created_at DESC LIMIT 1)"""
    ).fetchone()
    tournament = con.execute(
        """SELECT status,count(*) FROM whole_market_tournament_entries
        WHERE run_id=(SELECT run_id FROM whole_market_tournament_runs ORDER BY created_at DESC LIMIT 1)
        GROUP BY status ORDER BY status"""
    ).fetchall()
    live = con.execute(
        """SELECT market_rows,sector_rows,stock_rows FROM whole_market_live_runs
        ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    return {
        "run_id": run_id,
        "state": state,
        "market": market,
        "sectors": sectors,
        "stocks": stocks,
        "lead_lag": lead_lag,
        "analog": analog,
        "tournament": tournament,
        "live": live,
        "production_changes": 0,
        "probability_gate_changed": False,
    }


def render_evidence(evidence: dict[str, Any]) -> str:
    state = evidence["state"]
    lines = [
        "# Full Market Predictive Marathon — Evidence Report",
        "",
        f"Run: `{evidence['run_id']}`",
        f"Cutoff: {state[0]}",
        "Production changes: 0",
        "Probability gate changed: no",
        "",
        "## Current market outlook",
        "",
        f"- Regime/state: {state[1]}",
        f"- 20-session return: {state[2]:+.2%}",
        f"- Drawdown: {state[3]:.2%}",
        f"- Realized volatility (20): {state[4]:.2%}",
        "",
        "## IMOEX frozen-holdout evidence",
        "",
        "| Horizon | Best model | Balanced accuracy | Baseline | Difference | "
        "Return MAE | Downside MAE | Volatility MAE | Status |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in evidence["market"]:
        lines.append(
            f"| {row[0]} | {row[1]} | {row[2]:.3f} | {row[3]:.3f} | {row[4]:+.3f} | "
            f"{row[5]:.4f} | {row[6]:.4f} | {row[7]:.4f} | {row[8]} |"
        )
    lines += [
        "",
        "## Sector rotation",
        "",
        "| Horizon | Rank IC | Top-bottom spread | Status |",
        "|---:|---:|---:|---|",
    ]
    for row in evidence["sectors"]:
        lines.append(f"| {row[0]} | {row[1]:+.4f} | {row[2]:+.4f} | {row[3]} |")
    lines += [
        "",
        "## Best conditioned block by portfolio stock and horizon",
        "",
        "| Stock | Horizon | Block | MAE improvement | Return correlation | Status |",
        "|---|---:|---|---:|---:|---|",
    ]
    for row in evidence["stocks"]:
        lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]:+.5f} | {row[4]:+.3f} | {row[5]} |")
    lines += ["", "## Lead-lag associations (not causality)", ""]
    lines.extend(
        f"- {row[0]}: {row[1]} useful slices; mean |holdout correlation| {row[2]:.3f}"
        for row in evidence["lead_lag"]
    )
    lines += [
        "",
        "## Analog contribution",
        "",
        f"- Scorecards: {evidence['analog'][0]}; improved: {evidence['analog'][1]}; "
        f"rejected: {evidence['analog'][2]}.",
        f"- Mean MAE improvement: {evidence['analog'][3]:+.6f}; "
        f"direction accuracy: {evidence['analog'][4]:.3f}.",
        "- Average analog fusion contribution is not positive; no production promotion.",
        "",
        "## News contribution",
        "",
        "News remains context-only with predictive weight 0: no validated incremental OOS contribution.",
        "",
        "## Live evidence",
        "",
        f"- Market forecasts: {evidence['live'][0]}",
        f"- Sector rank forecasts: {evidence['live'][1]}",
        f"- Stock rank forecasts: {evidence['live'][2]}",
        "- Numerical probability is not approved.",
        "",
        "## Decision",
        "",
        "The evidence is research-only. Weak average market accuracy and negative average "
        "conditional/analog MAE effects prevent production promotion.",
    ]
    return "\n".join(lines) + "\n"


def run_full_marathon(con: duckdb.DuckDBPyConnection, report_path: Path = REPORT_PATH) -> dict[str, Any]:
    ensure_schema(con)
    started = time.monotonic()
    dataset_hash, dataset = dataset_fingerprint(con)
    cutoff = dataset["canonical_daily_prices"][2]
    run_id = hashlib.sha256(f"{VERSION}|{dataset_hash}".encode()).hexdigest()[:24]
    con.execute(
        """INSERT INTO market_marathon_runs
    (run_id,started_at,cutoff,dataset_hash,dataset_json,status,methodology_version,max_runtime_seconds,
    production_changes,probability_gate_changed,details_json) VALUES (?,?,?,?,?,'running',?,?,0,FALSE,?)
    ON CONFLICT DO NOTHING""",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            dataset_hash,
            json.dumps(dataset, default=str),
            VERSION,
            MAX_RUNTIME_SECONDS,
            json.dumps({"strict_frozen_holdout": True}),
        ],
    )
    steps: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("market_state", lambda: build_whole_market_state(con)),
        ("market_forecast", lambda: run_market_forecast_research(con)),
        ("sector_rotation", lambda: run_sector_rotation_research(con)),
        ("conditional_stocks", lambda: run_conditioned_stock_research(con)),
        ("lead_lag", lambda: run_lead_lag_research(con)),
        ("analog_fusion", lambda: run_market_analog_fusion(con)),
        ("tournament", lambda: run_whole_market_tournament(con)),
        ("live_capture", lambda: create_live_forecasts(con)),
        ("live_evaluation", lambda: evaluate_live_forecasts(con)),
        ("dashboard_snapshot", lambda: _dashboard_snapshot(con, run_id)),
    ]
    results = {}
    for name, action in steps:
        if time.monotonic() - started > MAX_RUNTIME_SECONDS:
            raise TimeoutError("market predictive marathon exceeded ten hours")
        complete = con.execute(
            """SELECT result_json FROM market_marathon_checkpoints
            WHERE run_id=? AND step=? AND status='completed'""",
            [run_id, name],
        ).fetchone()
        if complete:
            results[name] = json.loads(complete[0])
            continue
        con.execute(
            """INSERT INTO market_marathon_checkpoints
        (run_id,step,started_at,status) VALUES (?,?,?,'running') ON CONFLICT(run_id,step)
        DO UPDATE SET started_at=excluded.started_at,status='running',error=NULL""",
            [run_id, name, datetime.now(UTC)],
        )
        try:
            result = action()
            results[name] = result
            con.execute(
                """UPDATE market_marathon_checkpoints SET finished_at=?,status='completed',
            result_json=? WHERE run_id=? AND step=?""",
                [datetime.now(UTC), json.dumps(result, default=str), run_id, name],
            )
        except Exception as exc:
            con.execute(
                """UPDATE market_marathon_checkpoints SET finished_at=?,status='failed',error=?
                WHERE run_id=? AND step=?""",
                [datetime.now(UTC), str(exc), run_id, name],
            )
            raise
    final_hash, _ = dataset_fingerprint(con)
    if final_hash != dataset_hash:
        raise RuntimeError("frozen input dataset changed during marathon")
    evidence = collect_evidence(con, run_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_evidence(evidence), encoding="utf-8")
    runtime = time.monotonic() - started
    con.execute(
        """UPDATE market_marathon_runs SET finished_at=?,status='completed',runtime_seconds=?,
    details_json=? WHERE run_id=?""",
        [
            datetime.now(UTC),
            runtime,
            json.dumps({"steps": results, "report": str(report_path)}, default=str),
            run_id,
        ],
    )
    return {
        "run_id": run_id,
        "cutoff": str(cutoff),
        "runtime_seconds": runtime,
        "dataset_hash": dataset_hash,
        "steps": results,
        "report": str(report_path),
        "production_changes": 0,
        "probability_gate_changed": False,
    }
