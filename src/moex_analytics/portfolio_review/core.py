"""Stage 83 real nine-stock review and cash-aware allocation snapshot."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from moex_analytics.portfolio_verdict import build_portfolio_verdicts
from moex_analytics.portfolio_verdict.core import HORIZON_LABELS

from .schema import ensure_schema

VERSION = "stage83-v1"
AMOUNTS = (50_000.0, 100_000.0, 250_000.0, 500_000.0)
REPORT = Path("reports/current_portfolio_evidence_review.md")


def _latest(con: duckdb.DuckDBPyConnection, table: str) -> str:
    return con.execute(f"SELECT run_id FROM {table} ORDER BY created_at DESC LIMIT 1").fetchone()[0]


def build_current_portfolio_review(
    con: duckdb.DuckDBPyConnection, report_path: Path = REPORT
) -> dict[str, Any]:
    ensure_schema(con)
    verdict = build_portfolio_verdicts(con)
    verdict_run = verdict["run_id"]
    cutoff = con.execute(
        "SELECT cutoff FROM portfolio_verdict_runs WHERE run_id=?", [verdict_run]
    ).fetchone()[0]
    verdict_rows = con.execute(
        """SELECT instrument,current_status,portfolio_action,risk_status,human_verdict,
        top_for_json,top_against_json FROM portfolio_final_verdicts
        WHERE run_id=? ORDER BY instrument""",
        [verdict_run],
    ).fetchall()
    horizon_rows = con.execute(
        """SELECT instrument,horizon,directional_state,evidence_strength,relative_group,
        relative_rank,market_effect,sector_effect,strongest_evidence,analog_effect,news_effect,
        downside_state,portfolio_concentration,live_evidence,conflict
        FROM portfolio_horizon_verdicts WHERE run_id=? ORDER BY instrument,horizon""",
        [verdict_run],
    ).fetchall()
    serialized = json.dumps([verdict_rows, horizon_rows], default=str, ensure_ascii=False)
    consistency_hash = hashlib.sha256(serialized.encode()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{verdict_run}|{consistency_hash}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM portfolio_review_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    optimizer = con.execute(
        """SELECT tranche,allocation_json,cash_reserve,status FROM portfolio_allocation_plans
        WHERE tranche IN (50000,100000,250000,500000)
        QUALIFY row_number() OVER(PARTITION BY tranche ORDER BY plan_rank)=1 ORDER BY tranche"""
    ).fetchall()
    plans = []
    for amount in AMOUNTS:
        old = next((row for row in optimizer if float(row[0]) == amount), None)
        if old and old[3] == "CASH_PREFERRED":
            allocation, reserve, status = old[1], old[2], old[3]
            reason = "Optimizer prefers cash; live evidence is insufficient and no directional edge passed."
        else:
            allocation, reserve, status = json.dumps({"CASH": amount}), amount, "CASH_PREFERRED"
            reason = "No eligible directional evidence; purchases are not forced."
        plans.append([run_id, amount, allocation, reserve, status, reason])
    con.executemany("INSERT INTO portfolio_review_allocations VALUES (?,?,?,?,?,?)", plans)
    con.execute(
        """INSERT INTO portfolio_review_runs VALUES
        (?,?,?,?,?,?,?,TRUE,TRUE,'completed',?)""",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            verdict_run,
            len(verdict_rows),
            VERSION,
            consistency_hash,
            json.dumps({"surfaces": ["today", "stocks", "qa", "allocation"], "same_snapshot": True}),
        ],
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render(cutoff, verdict_rows, horizon_rows, plans, consistency_hash), encoding="utf-8"
    )
    return _status(con, run_id) | {"idempotent": False, "report": str(report_path)}


def _render(
    cutoff: object, verdicts: list[tuple], horizons: list[tuple], plans: list[list], consistency_hash: str
) -> str:
    by_stock: dict[str, dict[int, tuple]] = {}
    for row in horizons:
        by_stock.setdefault(row[0], {})[row[1]] = row
    lines = [
        "# Current Portfolio Evidence Review",
        "",
        f"Cutoff: {cutoff}",
        f"Consistency snapshot: `{consistency_hash}`",
        "",
        "## Human comparison",
        "",
        "|Акция|Сейчас|1 месяц|3 месяца|6 месяцев|1 год|Относительная сила|Риск|Итог|",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for instrument, _current, action, risk, verdict, *_ in verdicts:
        items = by_stock[instrument]
        states = {
            horizon: f"{items[horizon][2]} ({items[horizon][3]})" for horizon in HORIZON_LABELS
        }
        lines.append(
            f"|{instrument}|{states[5]}|{states[20]}|{states[60]}|{states[120]}|"
            f"{states[250]}|{items[120][4]}|{risk}|{action}: {verdict}|"
        )
    lines += ["", "## Technical evidence by stock", ""]
    for instrument, items in by_stock.items():
        lines += [f"### {instrument}", ""]
        for horizon, row in items.items():
            lines.append(
                f"- {HORIZON_LABELS[horizon]}: direction={row[2]}, strength={row[3]}, "
                f"rank={row[5]}, market={row[6]}, sector={row[7]}, evidence={row[8]}, "
                f"analogs={row[9]}, news={row[10]}, downside={row[11]}, "
                f"concentration={row[12]}, live={row[13]}."
            )
        lines.append("")
    lines += ["## New money", "", "|Amount|Allocation|Cash reserve|Status|Reason|", "|---:|---|---:|---|---|"]
    for _, amount, allocation, reserve, status, reason in plans:
        lines.append(f"|{amount:.0f}|`{allocation}`|{reserve:.0f}|{status}|{reason}|")
    lines += ["", "Production changes: 0. Probability gate unchanged. No orders created."]
    return "\n".join(lines) + "\n"


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT instruments,cutoff,consistency_hash FROM portfolio_review_runs WHERE run_id=?", [run_id]
    ).fetchone()
    cash = con.execute(
        "SELECT cash_reserve FROM portfolio_review_allocations WHERE run_id=? AND amount=100000", [run_id]
    ).fetchone()[0]
    return {
        "run_id": run_id,
        "instruments": row[0],
        "cutoff": str(row[1]),
        "consistency_hash": row[2],
        "cash_100k": cash,
        "status": "completed",
        "production_changes": 0,
    }
