"""Stage 86 deterministic, non-retrospective daily decision change memory."""

from __future__ import annotations

import json
from typing import Any

from .schema import ensure_schema

VERSION = "decision-change-memory-v1"
STATUS_ORDER = {
    "do_not_increase": 0,
    "insufficient_data": 1,
    "wait": 2,
    "hold": 2,
    "consider": 3,
}
MATERIAL_BLOCKS = {"status", "rank_group", "risk_state", "market_state", "portfolio_action"}


def _scalar(con: Any, sql: str, params: list[Any] | None = None) -> Any:
    try:
        row = con.execute(sql, params or []).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _rank_group(con: Any, secid: str, cutoff: Any) -> str:
    value = _scalar(
        con,
        "SELECT tie_group FROM current_portfolio_ranking WHERE secid=? AND cutoff<=? "
        "ORDER BY cutoff DESC,horizon DESC LIMIT 1",
        [secid, cutoff],
    )
    return f"group_{value}" if value is not None else "unavailable"


def _analog_state(con: Any, snapshot_id: str, secid: str) -> str:
    return (
        _scalar(
            con,
            "SELECT status FROM daily_analog_contexts WHERE snapshot_id=? AND instrument=? "
            "AND comparison_mode='full_state'",
            [snapshot_id, secid],
        )
        or "unavailable"
    )


def _news_state(con: Any, cutoff: Any, secid: str) -> str:
    count = _scalar(
        con,
        "SELECT count(*) FROM news_stories WHERE status='active' AND first_report_at<? "
        "AND (entities_json LIKE ? OR entities_json LIKE '%Russia%' OR entities_json LIKE '%MOEX%')",
        [str(cutoff) + " 23:59:59", f"%{secid}%"],
    )
    return f"active_{count}" if count is not None else "unavailable"


def _change(previous: dict[str, Any], current: dict[str, Any]) -> tuple[str, bool, list[str], list[str]]:
    changed = [key for key in current if key in previous and current[key] != previous[key]]
    material_blocks = [key for key in changed if key in MATERIAL_BLOCKS]
    reasons = [f"{key}: {previous[key]} → {current[key]}" for key in material_blocks]
    if not material_blocks:
        return "UNCHANGED", False, changed, []
    old_score = STATUS_ORDER.get(previous["status"], 1)
    new_score = STATUS_ORDER.get(current["status"], 1)
    positive = new_score > old_score or (
        previous["risk_state"] == "Повышенный риск" and current["risk_state"] != previous["risk_state"]
    )
    negative = new_score < old_score or (
        current["risk_state"] == "Повышенный риск" and current["risk_state"] != previous["risk_state"]
    )
    state = (
        "MIXED"
        if positive and negative
        else "IMPROVED"
        if positive
        else "DETERIORATED"
        if negative
        else "MIXED"
    )
    return state, True, changed, reasons


def capture_decision_snapshot(con: Any, snapshot_id: str | None = None) -> dict[str, Any]:
    ensure_schema(con)
    if snapshot_id is None:
        latest = con.execute(
            "SELECT snapshot_id,cutoff FROM daily_intelligence_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    else:
        latest = con.execute(
            "SELECT snapshot_id,cutoff FROM daily_intelligence_snapshots WHERE snapshot_id=?",
            [snapshot_id],
        ).fetchone()
    if not latest:
        raise ValueError("daily intelligence snapshot is required")
    snapshot_id, cutoff = latest
    if con.execute("SELECT 1 FROM daily_decision_states WHERE snapshot_id=?", [snapshot_id]).fetchone():
        return {"snapshot_id": snapshot_id, "status": "completed", "idempotent": True}
    try:
        report = con.execute(
            "SELECT report_id,market_regime FROM human_daily_reports WHERE analysis_cutoff=? "
            "ORDER BY created_at DESC LIMIT 1",
            [cutoff],
        ).fetchone()
    except Exception as exc:
        raise ValueError(
            "same-cutoff human daily report is required; no retrospective reconstruction"
        ) from exc
    if not report:
        raise ValueError("same-cutoff human daily report is required; no retrospective reconstruction")
    report_id, market_state = report
    instruments = con.execute(
        """SELECT secid,action_group,risk_view,portfolio_view,timing_view,
        top_positive,top_negative FROM human_instrument_synthesis
        WHERE report_id=? ORDER BY secid""",
        [report_id],
    ).fetchall()
    rows = []
    current_states: dict[str, dict[str, Any]] = {}
    for secid, status, risk, portfolio, timing, positive, negative in instruments:
        horizons = dict(
            con.execute(
                "SELECT horizon,status FROM human_horizon_views WHERE report_id=? AND secid=?",
                [report_id, secid],
            ).fetchall()
        )
        state = {
            "status": status,
            "rank_group": _rank_group(con, secid, cutoff),
            "risk_state": risk,
            "market_state": market_state,
            "sector_state": "informational_unavailable",
            "analog_state": _analog_state(con, snapshot_id, secid),
            "news_state": _news_state(con, cutoff, secid),
            "portfolio_action": portfolio,
        }
        current_states[secid] = state
        rows.append(
            [
                snapshot_id,
                cutoff,
                secid,
                status,
                json.dumps(horizons),
                state["rank_group"],
                risk,
                market_state,
                state["sector_state"],
                state["analog_state"],
                state["news_state"],
                portfolio,
                json.dumps([positive, negative, timing], ensure_ascii=False),
                report_id,
                True,
            ]
        )
    con.executemany(
        """INSERT INTO daily_decision_states (
        snapshot_id,cutoff,secid,status,horizon_states_json,rank_group,risk_state,market_state,
        sector_state,analog_state,news_state,portfolio_action,top_reasons_json,source_report_id,
        immutable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    previous_id = _scalar(
        con,
        "SELECT snapshot_id FROM daily_decision_states WHERE cutoff<? ORDER BY cutoff DESC LIMIT 1",
        [cutoff],
    )
    changes = []
    for secid, current in current_states.items():
        previous_row = (
            con.execute(
                """SELECT status,rank_group,risk_state,market_state,sector_state,analog_state,
                news_state,portfolio_action FROM daily_decision_states
                WHERE snapshot_id=? AND secid=?""",
                [previous_id, secid],
            ).fetchone()
            if previous_id
            else None
        )
        if previous_row:
            keys = tuple(current)
            previous = dict(zip(keys, previous_row, strict=True))
            change_state, material, blocks, reasons = _change(previous, current)
        else:
            change_state, material, blocks, reasons = "UNCHANGED", False, [], ["first observed daily state"]
        changes.append(
            [
                snapshot_id,
                previous_id,
                cutoff,
                secid,
                change_state,
                material,
                json.dumps(blocks),
                json.dumps(reasons, ensure_ascii=False),
                True,
            ]
        )
    con.executemany(
        """INSERT INTO daily_decision_changes (
        snapshot_id,previous_snapshot_id,cutoff,secid,change_state,material,
        changed_blocks_json,reasons_json,immutable) VALUES (?,?,?,?,?,?,?,?,?)""",
        changes,
    )
    return {
        "snapshot_id": snapshot_id,
        "cutoff": cutoff,
        "states": len(rows),
        "material_changes": sum(row[5] for row in changes),
        "status": "completed",
        "idempotent": False,
        "production_changes": 0,
    }


def latest_changes(con: Any) -> list[dict[str, Any]]:
    ensure_schema(con)
    rows = con.execute(
        """SELECT cutoff,secid,change_state,material,changed_blocks_json,reasons_json
        FROM daily_decision_changes WHERE snapshot_id=(SELECT snapshot_id
        FROM daily_intelligence_snapshots ORDER BY created_at DESC LIMIT 1) ORDER BY secid"""
    ).fetchall()
    return [
        {
            "cutoff": row[0],
            "secid": row[1],
            "change": row[2],
            "material": row[3],
            "blocks": json.loads(row[4] or "[]"),
            "reasons": json.loads(row[5] or "[]"),
        }
        for row in rows
    ]
