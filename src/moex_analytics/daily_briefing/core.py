"""Stage 90 one-screen briefing built only from the compatible saved snapshot."""

from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from moex_analytics.config import PROJECT_ROOT

from .schema import ensure_schema

VERSION = "daily-investor-briefing-v2.1"
EXPORT_DIR = PROJECT_ROOT / "reports" / "daily_briefings"


def _safe_rows(con: Any, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    try:
        return con.execute(sql, params or []).fetchall()
    except Exception:
        return []


def _safe_row(con: Any, sql: str, params: list[Any] | None = None) -> tuple[Any, ...] | None:
    rows = _safe_rows(con, sql, params)
    return rows[0] if rows else None


def _payload(con: Any, snapshot_id: str, cutoff: Any) -> dict[str, Any]:
    snapshot = _safe_row(
        con,
        "SELECT compatibility,fast_current,fast_total FROM daily_intelligence_snapshots WHERE snapshot_id=?",
        [snapshot_id],
    )
    market = _safe_row(
        con,
        """SELECT market_state_label,return_20,drawdown,realized_vol20,breadth_json,
        rates_json,commodities_json FROM whole_market_state_daily WHERE trade_date=?
        ORDER BY available_from DESC LIMIT 1""",
        [cutoff],
    )
    changes = _safe_rows(
        con,
        "SELECT secid,change_state,material,reasons_json FROM daily_decision_changes "
        "WHERE snapshot_id=? ORDER BY material DESC,secid",
        [snapshot_id],
    )
    review = _safe_row(
        con,
        "SELECT run_id,verdict_run_id FROM portfolio_review_runs WHERE cutoff=? "
        "AND status='completed' ORDER BY created_at DESC LIMIT 1",
        [cutoff],
    )
    verdicts = []
    allocation = None
    if review:
        verdict_run = _safe_row(
            con,
            """SELECT run_id FROM portfolio_verdict_runs WHERE cutoff=? AND status='completed'
            ORDER BY created_at DESC LIMIT 1""",
            [cutoff],
        )
        verdicts = _safe_rows(
            con,
            """SELECT v.instrument,s.investment_status,s.allocation_status,v.risk_status,
            max(CASE WHEN h.horizon=20 THEN h.directional_state END),
            max(CASE WHEN h.horizon=60 THEN h.directional_state END),
            max(CASE WHEN h.horizon=120 THEN h.directional_state END),
            max(CASE WHEN h.horizon=250 THEN h.directional_state END)
            FROM portfolio_final_verdicts v JOIN portfolio_horizon_verdicts h
            ON v.run_id=h.run_id AND v.instrument=h.instrument
            JOIN investment_allocation_views s
            ON s.run_id=v.run_id AND s.instrument=v.instrument WHERE v.run_id=?
            GROUP BY v.instrument,s.investment_status,s.allocation_status,v.risk_status
            ORDER BY v.instrument""",
            [verdict_run[0] if verdict_run else review[1]],
        )
        if not verdicts:
            verdicts = _safe_rows(
                con,
                """SELECT v.instrument,v.portfolio_action,v.portfolio_action,v.risk_status,
                max(CASE WHEN h.horizon=20 THEN h.directional_state END),
                max(CASE WHEN h.horizon=60 THEN h.directional_state END),
                max(CASE WHEN h.horizon=120 THEN h.directional_state END),
                max(CASE WHEN h.horizon=250 THEN h.directional_state END)
                FROM portfolio_final_verdicts v JOIN portfolio_horizon_verdicts h
                ON v.run_id=h.run_id AND v.instrument=h.instrument WHERE v.run_id=?
                GROUP BY v.instrument,v.portfolio_action,v.risk_status ORDER BY v.instrument""",
                [review[1]],
            )
        allocation = _safe_row(
            con,
            "SELECT allocation_json,cash_reserve,status,reason FROM portfolio_review_allocations "
            "WHERE run_id=? AND amount=100000",
            [review[0]],
        )
    scenario_run = _safe_row(
        con,
        "SELECT run_id FROM portfolio_scenario_runs WHERE cutoff=? AND status='completed' "
        "ORDER BY created_at DESC LIMIT 1",
        [cutoff],
    )
    scenarios = (
        _safe_rows(
            con,
            """SELECT label,episodes,total_episodes,median_imoex_return,median_drawdown,
            historical_frequency_text FROM portfolio_scenario_branches
            WHERE run_id=? ORDER BY episodes DESC LIMIT 3""",
            [scenario_run[0]],
        )
        if scenario_run
        else []
    )
    analog_run = _safe_row(
        con,
        "SELECT run_id FROM state_similarity_runs WHERE cutoff=? AND status='completed' "
        "ORDER BY created_at DESC LIMIT 1",
        [cutoff],
    )
    analogs = (
        _safe_rows(
            con,
            """SELECT analog_date,count(*) support,avg(similarity) similarity
            FROM state_similarity_matches WHERE run_id=? AND analog_type='state'
            GROUP BY analog_date ORDER BY support DESC,similarity DESC LIMIT 3""",
            [analog_run[0]],
        )
        if analog_run
        else []
    )
    news = _safe_rows(
        con,
        "SELECT headline,event_type,reliability FROM news_stories WHERE status='active' "
        "AND first_report_at<=? ORDER BY last_update_at DESC LIMIT 5",
        [str(cutoff) + " 23:59:59"],
    )
    live = _safe_row(
        con,
        """SELECT count(*),count(*) FILTER(WHERE outcome_status='matured')
        FROM forecast_registry r LEFT JOIN forecast_outcomes o USING(forecast_id)""",
    ) or (0, 0)
    return {
        "version": VERSION,
        "snapshot_id": snapshot_id,
        "cutoff": str(cutoff),
        "compatibility": snapshot[0] if snapshot else "unavailable",
        "fast_components": f"{snapshot[1]}/{snapshot[2]}" if snapshot else "0/0",
        "market": {
            "state": market[0] if market else "unavailable",
            "return_20": market[1] if market else None,
            "drawdown": market[2] if market else None,
            "volatility": market[3] if market else None,
            "breadth": json.loads(market[4] or "{}") if market else {},
            "rates": json.loads(market[5] or "{}") if market else {},
            "commodities": json.loads(market[6] or "{}") if market else {},
        },
        "changes": [
            {"secid": row[0], "state": row[1], "material": row[2], "reasons": json.loads(row[3] or "[]")}
            for row in changes
        ],
        "verdicts": [
            dict(
                zip(
                    ("secid", "investment", "allocation", "risk", "1m", "3m", "6m", "1y"),
                    row,
                    strict=True,
                )
            )
            for row in verdicts
        ],
        "new_money": {
            "allocation": json.loads(allocation[0] or "{}"),
            "reserve": allocation[1],
            "status": allocation[2],
            "reason": allocation[3],
        }
        if allocation
        else {"status": "unavailable"},
        "scenarios": [
            {
                "label": row[0],
                "episodes": row[1],
                "total": row[2],
                "return": row[3],
                "drawdown": row[4],
                "frequency": row[5],
            }
            for row in scenarios
        ],
        "analogs": [{"date": str(row[0]), "support": row[1], "similarity": row[2]} for row in analogs],
        "news": [{"headline": row[0], "event_type": row[1], "reliability": row[2]} for row in news],
        "live": {"total": live[0], "matured": live[1], "pending": live[0] - live[1]},
        "production_changes": 0,
        "probability_gate_changed": False,
    }


def _markdown(payload: dict[str, Any]) -> str:
    market = payload["market"]
    lines = [
        f"# Ежедневный инвестиционный обзор — {payload['cutoff']}",
        "",
        f"Единый snapshot: `{payload['snapshot_id']}` · {payload['compatibility']} · "
        f"fast components {payload['fast_components']}",
        "",
        "## Рынок",
        "",
        f"Состояние: {market['state']}; 20 сессий: {market['return_20']}; "
        f"просадка: {market['drawdown']}; волатильность: {market['volatility']}.",
        "",
        "## Что изменилось",
        "",
    ]
    material = [row for row in payload["changes"] if row["material"]]
    lines.extend(
        [f"- {row['secid']}: {row['state']} — {'; '.join(row['reasons'])}" for row in material]
        or ["- Материальных изменений нет."]
    )
    lines.extend(
        [
            "",
            "## Портфель",
            "",
            "| Акция | Рыночный вывод | Портфельный вывод | 1м | 3м | 6м | 12м | Риск |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| {row['secid']} | {row['investment']} | {row['allocation']} | "
        f"{row['1m']} | {row['3m']} | "
        f"{row['6m']} | {row['1y']} | {row['risk']} |"
        for row in payload["verdicts"]
    )
    money = payload["new_money"]
    lines.extend(
        [
            "",
            "## Новые 100 000 ₽",
            "",
            f"Статус: {money.get('status')}; резерв: {money.get('reserve')}; {money.get('reason', '')}",
        ]
    )
    lines.extend(["", "## Исторические сценарии", ""])
    lines.extend(
        f"- {row['label']}: {row['frequency']}; IMOEX {row['return']:+.1%}." for row in payload["scenarios"]
    )
    lines.extend(["", "## На что сейчас похож рынок", ""])  # noqa: RUF001
    lines.extend(f"- {row['date']}: поддержка {row['support']} инструментов." for row in payload["analogs"])
    lines.extend(
        [
            "",
            "## Live learning",
            "",
            f"Проверено {payload['live']['matured']}; ожидается {payload['live']['pending']}.",
        ]
    )
    lines.extend(["", "Research-only. Это не BUY/SELL и не числовая вероятность."])
    return "\n".join(lines) + "\n"


def _html(markdown: str, payload: dict[str, Any]) -> str:
    return (
        "<!doctype html><html lang='ru'><meta charset='utf-8'><title>MOEX daily briefing</title>"
        "<style>body{font:16px system-ui;max-width:1200px;margin:40px auto;color:#172033}"
        "pre{white-space:pre-wrap;line-height:1.5;background:#f5f7fb;padding:28px;border-radius:16px}</style>"
        f"<body><h1>MOEX Analytics · {html.escape(payload['cutoff'])}</h1>"
        f"<pre>{html.escape(markdown)}</pre></body></html>"
    )


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.stem + "-", suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_daily_briefing(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    snapshot = con.execute(
        "SELECT snapshot_id,cutoff FROM daily_intelligence_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not snapshot:
        raise ValueError("unified daily snapshot is required")
    snapshot_id, cutoff = snapshot
    payload = _payload(con, snapshot_id, cutoff)
    if not payload["verdicts"]:
        raise ValueError("same-cutoff portfolio verdict is required for daily briefing")
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    briefing_id = hashlib.sha256(f"{VERSION}|{snapshot_id}|{input_hash}".encode()).hexdigest()[:24]
    if con.execute("SELECT 1 FROM daily_investor_briefings WHERE briefing_id=?", [briefing_id]).fetchone():
        return latest_briefing(con) | {"idempotent": True}
    previous = con.execute(
        "SELECT briefing_id,payload_json FROM daily_investor_briefings WHERE cutoff<? "
        "ORDER BY cutoff DESC,created_at DESC LIMIT 1",
        [cutoff],
    ).fetchone()
    markdown = _markdown(payload)
    html_text = _html(markdown, payload)
    stem = f"{cutoff}_{briefing_id}"
    markdown_path = EXPORT_DIR / f"{stem}.md"
    html_path = EXPORT_DIR / f"{stem}.html"
    _atomic_write(markdown_path, markdown)
    _atomic_write(html_path, html_text)
    con.execute(
        """INSERT INTO daily_investor_briefings (
        briefing_id,snapshot_id,cutoff,created_at,previous_briefing_id,payload_json,
        markdown_text,html_text,input_hash,markdown_path,html_path,immutable,
        production_unchanged,probability_gate_unchanged)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,TRUE,TRUE,TRUE)""",
        [
            briefing_id,
            snapshot_id,
            cutoff,
            datetime.now(UTC),
            previous[0] if previous else None,
            json.dumps(payload, ensure_ascii=False),
            markdown,
            html_text,
            input_hash,
            str(markdown_path),
            str(html_path),
        ],
    )
    previous_payload = json.loads(previous[1]) if previous else None
    market_change = (
        f"{previous_payload['market']['state']} → {payload['market']['state']}"
        if previous_payload and previous_payload["market"]["state"] != payload["market"]["state"]
        else "UNCHANGED"
    )
    material = [row for row in payload["changes"] if row["material"]]
    con.execute(
        "INSERT INTO daily_briefing_comparisons VALUES (?,?,?,?,?,?,TRUE)",
        [
            briefing_id,
            previous[0] if previous else None,
            market_change,
            json.dumps(material, ensure_ascii=False),
            len(material),
            sum(len(row["reasons"]) for row in material),
        ],
    )
    return latest_briefing(con) | {"idempotent": False}


def latest_briefing(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        """SELECT briefing_id,snapshot_id,cutoff,payload_json,markdown_path,html_path,
        previous_briefing_id FROM daily_investor_briefings ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not row:
        return {"latest": None}
    return {
        "briefing_id": row[0],
        "snapshot_id": row[1],
        "cutoff": row[2],
        "payload": json.loads(row[3]),
        "markdown_path": row[4],
        "html_path": row[5],
        "previous_briefing_id": row[6],
        "status": "completed",
        "production_changes": 0,
        "probability_gate_changed": False,
    }
