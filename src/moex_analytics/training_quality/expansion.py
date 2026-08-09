"""Evidence-first expansion of the high-quality historical universe (Stage 34)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import requests

from moex_analytics.config import PROJECT_ROOT

from .corporate_actions import build_corporate_action_quality
from .panel import build_training_universe
from .schema import DDL

ISS_SECURITY = "https://iss.moex.com/iss/securities/{secid}.json"


def _counts(con) -> dict[str, int]:
    return dict(con.execute(
        "SELECT training_tier,count(*) FROM historical_quality_v2 GROUP BY 1"
    ).fetchall())


def _queue(con, run_id: str) -> list[tuple]:
    con.execute("DELETE FROM quality_promotion_queue WHERE run_id=?", [run_id])
    rows = con.execute(
        """WITH ep AS (
          SELECT secid,count(*) FILTER(review_status!='auto_validated') unresolved
          FROM corporate_action_candidate_episodes GROUP BY 1
        ) SELECT q.secid,q.training_tier,q.observations,q.history_years,q.missingness,
        q.liquidity_coverage,q.numtrades_coverage,coalesce(ep.unresolved,0),
        greatest(3,q.observations*.005) allowance
        FROM historical_quality_v2 q LEFT JOIN ep USING(secid)
        WHERE q.training_tier IN ('B','C')
        ORDER BY CASE q.training_tier WHEN 'C' THEN 0 ELSE 1 END,
        q.observations DESC"""
    ).fetchall()
    selected = []
    for secid, tier, observations, years, missing, volume, trades, unresolved, allowance in rows:
        blockers = []
        if years < 4 or observations < 600:
            blockers.append("minimum_history")
        if missing > 0.08:
            blockers.append("missing_ohlc")
        if volume < 0.7:
            blockers.append("volume_coverage")
        if unresolved > allowance:
            blockers.append("corporate_action_uncertainty")
        if tier == "B":
            if years < 8 or observations < 1200:
                blockers.append("tier_a_history")
            if trades < 0.7:
                blockers.append("numtrades_coverage")
        priority = (0 if tier == "C" and len(blockers) == 1 else 100) + len(blockers)
        missing_evidence = [
            "official_split_consolidation_redenomination_document"
        ] if "corporate_action_uncertainty" in blockers else []
        con.execute(
            """INSERT INTO quality_promotion_queue VALUES
            (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
            [run_id, secid, tier, "B" if tier == "C" else "A", priority,
             observations, years, unresolved, json.dumps(blockers),
             json.dumps(missing_evidence), "evidence_required" if missing_evidence else "blocked"],
        )
        if priority < 100:
            selected.append((secid, blockers))
    return selected


def _fetch_metadata(con, run_id: str, secid: str, session=requests) -> bool:
    endpoint = ISS_SECURITY.format(secid=secid)
    retrieved = datetime.now(UTC)
    response = session.get(endpoint, params={"iss.meta": "off"}, timeout=30)
    payload = response.content
    digest = hashlib.sha256(payload).hexdigest()
    raw = PROJECT_ROOT / "data" / "raw" / "quality_evidence"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / f"{secid}_{retrieved:%Y%m%dT%H%M%S%f}_{digest[:12]}.json"
    path.write_bytes(payload)
    valid_json = False
    description = []
    if response.ok:
        try:
            body = response.json()
            description = body.get("description", {}).get("data", [])
            valid_json = bool(description)
        except (ValueError, AttributeError):
            pass
    # Security metadata proves identity/board context, never a price adjustment ratio.
    status = "context_only" if valid_json else "source_error"
    attempt_id = hashlib.sha256(f"{run_id}:{secid}:{digest}".encode()).hexdigest()[:24]
    con.execute(
        """INSERT OR IGNORE INTO quality_evidence_attempts VALUES
        (?,?,?,?,?,?,?,?,?,?,?)""",
        [attempt_id, run_id, secid, "MOEX ISS security metadata", endpoint, retrieved,
         response.status_code, digest, "security_identity_and_board_context", status,
         json.dumps({"raw_path": str(path.relative_to(PROJECT_ROOT)),
                     "description_rows": len(description),
                     "cannot_validate_price_ratio": True})],
    )
    return valid_json


def expand_quality_universe(con, *, fetch_official: bool = True, session=requests) -> dict:
    con.execute(DDL)
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"stage34:{started.isoformat()}".encode()).hexdigest()[:20]
    before = _counts(con)
    candidates = _queue(con, run_id)
    requests_made = 0
    source_success = 0
    if fetch_official:
        for secid, _ in candidates:
            try:
                source_success += int(_fetch_metadata(con, run_id, secid, session))
            except requests.RequestException as exc:
                attempt_id = hashlib.sha256(f"{run_id}:{secid}:error".encode()).hexdigest()[:24]
                con.execute(
                    """INSERT INTO quality_evidence_attempts VALUES
                    (?,?,?,?,current_timestamp,NULL,NULL,?,?,?)""",
                    [attempt_id, run_id, secid, "MOEX ISS security metadata",
                     ISS_SECURITY.format(secid=secid), "security_identity_and_board_context",
                     "source_error", json.dumps({"error": str(exc)})],
                )
            requests_made += 1
    quality = build_corporate_action_quality(con)
    after = _counts(con)
    panel = build_training_universe(con)
    ab_after = after.get("A", 0) + after.get("B", 0)
    stop = "target_reached" if ab_after >= 250 else "official_source_exhaustion"
    details = {"raw_rewritten": False, "thresholds_changed": False,
               "source_context_success": source_success,
               "ratio_validations_from_metadata": 0,
               "target_ab": 250, "diminishing_returns": ab_after < 250}
    con.execute(
        """INSERT INTO quality_expansion_runs VALUES
        (?,?,current_timestamp,'completed',?,?,?,?,?,?,?,?,?,?,0,?)""",
        [run_id, started, before.get("A", 0), before.get("B", 0), after.get("A", 0),
         after.get("B", 0), len(candidates), requests_made, quality["auto_validated"],
         quality["unresolved"] + quality["manual_review"], panel["dataset_version"], stop,
         json.dumps(details)],
    )
    return {"run_id": run_id, "before": before, "after": after,
            "promotion_candidates": len(candidates), "requests": requests_made,
            "panel": panel, "stop_reason": stop, "production_changes": 0, **details}


def expansion_status(con) -> dict:
    con.execute(DDL)
    latest = con.execute(
        "SELECT * FROM quality_expansion_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    queue = con.execute(
        """SELECT secid,current_tier,target_tier,blocking_issues_json,
        missing_evidence_json,queue_status FROM quality_promotion_queue
        WHERE run_id=(SELECT run_id FROM quality_expansion_runs ORDER BY started_at DESC LIMIT 1)
        ORDER BY priority,secid LIMIT 100"""
    ).fetchall()
    return {"latest": latest, "promotion_queue": queue}
