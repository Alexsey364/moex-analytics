"""Stage 82 explainable verdict synthesis without a magic score."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb

from .schema import ensure_schema

VERSION = "stage82-v1"
HORIZON_LABELS = {5: "сейчас", 20: "1 месяц", 60: "3 месяца", 120: "6 месяцев", 250: "1 год"}


def action_policy(
    *,
    stress: bool,
    concentration: float | None,
    positive: int,
    negative: int,
    eligible_direction: bool,
    severe_data: bool = False,
) -> tuple[str, str]:
    if severe_data:
        return "⚪ Недостаточно данных", "severe current data problem"
    if concentration is not None and concentration >= 0.25:
        return "🔴 Не увеличивать из-за риска/концентрации", "portfolio concentration restriction"  # noqa: RUF001
    if stress and negative > positive:
        return "🟠 Пока не увеличивать", "stress market and adverse evidence"
    if eligible_direction and positive > negative:
        return "🟢 Можно рассматривать небольшое пополнение", "positive eligible evidence"
    return "🟡 Держать / наблюдать", "mixed or directionally unproven evidence"


def _latest(con: duckdb.DuckDBPyConnection, table: str) -> str:
    return con.execute(f"SELECT run_id FROM {table} ORDER BY created_at DESC LIMIT 1").fetchone()[0]


def build_portfolio_verdicts(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    evidence_run = _latest(con, "evidence_registry_runs")
    live_run = _latest(con, "whole_market_live_runs")
    state_run = _latest(con, "whole_market_state_runs")
    cutoff, state = con.execute(
        """SELECT trade_date,market_state_label FROM whole_market_state_daily
        WHERE run_id=? ORDER BY trade_date DESC LIMIT 1""",
        [state_run],
    ).fetchone()
    signature = f"{VERSION}|{evidence_run}|{live_run}|{cutoff}"
    run_id = hashlib.sha256(signature.encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM portfolio_verdict_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    positions = {}
    if con.execute("SELECT count(*) FROM portfolio_positions").fetchone()[0]:
        snapshot = con.execute(
            """SELECT snapshot_id FROM portfolio_positions GROUP BY snapshot_id
            ORDER BY snapshot_id DESC LIMIT 1"""
        ).fetchone()[0]
        positions = dict(
            con.execute(
                "SELECT secid,weight FROM portfolio_positions WHERE snapshot_id=?", [snapshot]
            ).fetchall()
        )
    live = con.execute(
        """SELECT secid,horizon,predicted_rank,qualitative_state,status
        FROM live_stock_rank_forecasts WHERE run_id=?""",
        [live_run],
    ).fetchall()
    rows, final_rows = [], []
    by_instrument: dict[str, list[dict[str, Any]]] = {}
    for instrument, horizon, rank, direction, live_status in live:
        blocks = con.execute(
            """SELECT block_type,evidence_status,decision_eligible,reason,relative_improvement,
            fold_stable FROM evidence_registry_blocks WHERE run_id=? AND instrument=? AND horizon=?""",
            [evidence_run, instrument, horizon],
        ).fetchall()
        eligible = [item for item in blocks if item[2]]
        positive = sum(item[4] is not None and item[4] > 0 for item in eligible)
        negative = sum(item[4] is not None and item[4] < 0 for item in eligible)
        directional = any(item[0] not in {"risk", "portfolio_concentration"} for item in eligible)
        strengths = [item[1] for item in blocks]
        strength = (
            "stronger"
            if "STRONG_RESEARCH_EVIDENCE" in strengths
            else "medium"
            if "MODERATE_RESEARCH_EVIDENCE" in strengths
            else "low"
        )
        relative = "выше средней" if rank <= 3 else "ниже средней" if rank >= 7 else "средняя"
        conflict = positive > 0 and negative > 0
        top_for = [item[3] for item in eligible if item[4] is None or item[4] > 0][:3]
        top_against = ["Рынок находится в режиме stress"] if state == "stress" else []
        if not directional:
            top_against.append("Направленное преимущество отдельно не доказано")
        if live_status != "matured":
            top_against.append("Live-выборка ещё слишком мала")
        improve = ["Стабильный положительный результат в новых live outcomes", "Выход рынка из stress"]
        worsen = ["Рост концентрации", "Ухудшение downside или рыночного режима"]
        strongest = next(
            (
                item[3]
                for item in blocks
                if item[1] in {"STRONG_RESEARCH_EVIDENCE", "MODERATE_RESEARCH_EVIDENCE"}
            ),
            "Нет устойчивого направленного доказательства",
        )
        record = dict(
            instrument=instrument,
            horizon=horizon,
            direction=direction,
            strength=strength,
            relative=relative,
            rank=rank,
            positive=positive,
            negative=negative,
            directional=directional,
            conflict=conflict,
            top_for=top_for,
            top_against=top_against,
            improve=improve,
            worsen=worsen,
            strongest=strongest,
        )
        by_instrument.setdefault(instrument, []).append(record)
        rows.append(
            [
                run_id,
                instrument,
                horizon,
                direction,
                strength,
                relative,
                rank,
                state,
                "relative sector rank included",
                strongest,
                "shadow/context only",
                "context only; predictive weight 0",
                "high" if state == "stress" else "normal",
                f"weight {positions.get(instrument, 0):.1%}",
                "too small / pending",
                json.dumps(top_for),
                json.dumps(top_against),
                json.dumps(improve),
                json.dumps(worsen),
                conflict,
                json.dumps([item[0] for item in eligible]),
            ]
        )
    for instrument, records in by_instrument.items():
        positive = sum(item["positive"] for item in records)
        negative = sum(item["negative"] for item in records)
        directional = any(item["directional"] for item in records)
        action, reason = action_policy(
            stress=state == "stress",
            concentration=positions.get(instrument),
            positive=positive,
            negative=negative,
            eligible_direction=directional,
        )
        conflict = any(item["conflict"] for item in records)
        verdict = "Смешанная картина" if conflict or not directional else reason
        top_for = [reason for item in records for reason in item["top_for"]][:3]
        top_against = list(dict.fromkeys(reason for item in records for reason in item["top_against"]))[:3]
        final_rows.append(
            [
                run_id,
                instrument,
                "mixed" if conflict else "research_only",
                action,
                "high" if state == "stress" else "normal",
                verdict,
                json.dumps(top_for),
                json.dumps(top_against),
                json.dumps(records[0]["improve"]),
                json.dumps(records[0]["worsen"]),
            ]
        )
    con.executemany(
        "INSERT INTO portfolio_horizon_verdicts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.executemany("INSERT INTO portfolio_final_verdicts VALUES (?,?,?,?,?,?,?,?,?,?)", final_rows)
    con.execute(
        "INSERT INTO portfolio_verdict_runs VALUES (?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            evidence_run,
            len(by_instrument),
            VERSION,
            json.dumps({"no_magic_score": True, "conflicts_are_explicit": True}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        """SELECT count(*),count(DISTINCT instrument),sum(conflict)
        FROM portfolio_horizon_verdicts WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "horizon_verdicts": row[0],
        "instruments": row[1],
        "conflicts": row[2],
        "status": "completed",
        "production_changes": 0,
        "probability_gate_changed": False,
    }
