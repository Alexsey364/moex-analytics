"""Secondary event views for analog episodes without causal claims."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from moex_analytics.analog_engine.core import INSTRUMENTS
from moex_analytics.trajectory_engine.core import HORIZONS, MIN_EFFECTIVE_N

from .schema import DDL

VERSION = "event-conditioned-analogs-v1"


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def event_is_available(event: dict, asof: pd.Timestamp) -> bool:
    """Surprise outcomes cannot exist before their declared availability."""
    available = event.get("available_from")
    if available is None:
        return False
    timestamp = pd.Timestamp(available)
    cutoff = pd.Timestamp(asof)
    if timestamp.tz is not None and cutoff.tz is None:
        cutoff = cutoff.tz_localize("UTC")
    return timestamp <= cutoff


def summarize_subset(values: pd.Series) -> dict:
    clean = values.dropna()
    if len(clean) < MIN_EFFECTIVE_N:
        return {"status": "insufficient_data", "reason": "event subset has fewer than five episodes"}
    return {
        "status": "ready",
        "reason": "descriptive association; not causal",
        "n": len(clean),
        "median": float(clean.median()),
        "q25": float(clean.quantile(0.25)),
        "q75": float(clean.quantile(0.75)),
        "positive": float((clean > 0).mean()),
        "dispersion": float(clean.quantile(0.75) - clean.quantile(0.25)),
    }


def _write_profiles(con: Any, run_id: str, analog_run: str) -> int:
    rows = con.execute(
        """SELECT DISTINCT ?,a.secid,a.method,a.path_window,a.analog_date,e.event_id,
                  e.event_family,e.event_type,t.event_state,e.surprise_event,e.available_from,TRUE
           FROM historical_analogs_v3 a
           JOIN historical_event_timeline t ON t.trade_date=a.analog_date AND t.pit_safe
                AND (t.secid='MARKET' OR t.secid=a.secid)
           JOIN historical_events e USING(event_id)
           WHERE a.run_id=? AND a.analog_type='issuer'
             AND e.validation_status='validated' AND e.pit_status='pit_safe'
             AND CAST(e.available_from AT TIME ZONE 'UTC' AS DATE)<=a.analog_date""",
        [run_id, analog_run],
    ).fetchall()
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO analog_event_profiles "
            "(run_id,secid,method,path_window,analog_date,event_id,event_family,event_type,event_state,"
            "surprise_event,available_from,pit_safe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
    return len(rows)


def _current_context(con: Any, run_id: str, cutoff) -> dict[str, set[str]]:
    by_secid: dict[str, set[str]] = {}
    for secid in INSTRUMENTS:
        active = con.execute(
            """SELECT DISTINCT e.event_family FROM historical_event_timeline t
               JOIN historical_events e USING(event_id)
               WHERE t.trade_date=? AND t.pit_safe AND (t.secid='MARKET' OR t.secid=?)
                 AND e.validation_status='validated' AND e.pit_status='pit_safe'
                 AND CAST(e.available_from AT TIME ZONE 'UTC' AS DATE)<=?""",
            [cutoff, secid, cutoff],
        ).fetchall()
        families = {row[0] for row in active}
        scheduled = con.execute(
            """SELECT e.event_family,t.days_until_scheduled_event FROM historical_event_timeline t
               JOIN historical_events e USING(event_id)
               WHERE t.trade_date<=? AND t.pit_safe AND e.expected_or_scheduled
                 AND t.days_until_scheduled_event>=0 AND (t.secid='MARKET' OR t.secid=?)
               ORDER BY t.trade_date DESC,t.days_until_scheduled_event LIMIT 1""",
            [cutoff, secid],
        ).fetchone()
        matches = 0
        if families:
            placeholders = ",".join("?" for _ in families)
            matches = con.execute(
                f"SELECT count(DISTINCT analog_date) FROM analog_event_profiles "
                f"WHERE run_id=? AND secid=? AND event_family IN ({placeholders})",
                [run_id, secid, *sorted(families)],
            ).fetchone()[0]
        novelty = "familiar" if matches >= MIN_EFFECTIVE_N else "event_context_novel"
        adjustment = "none" if novelty == "familiar" else "lower_analog_confidence"
        con.execute(
            "INSERT OR REPLACE INTO current_event_contexts "
            "(run_id,secid,cutoff,event_families_json,nearest_scheduled_family,"
            "days_until_scheduled,historical_matches,novelty_status,confidence_adjustment,status,reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,'ready',?)",
            [run_id, secid, cutoff, json.dumps(sorted(families)), scheduled[0] if scheduled else None,
             scheduled[1] if scheduled else None, matches, novelty, adjustment,
             "event coincidence metadata; no causal attribution"],
        )
        by_secid[secid] = families
    return by_secid


def _write_distributions(con: Any, run_id: str, trajectory_run: str,
                         current: dict[str, set[str]]) -> int:
    written = 0
    keys = con.execute(
        "SELECT DISTINCT secid,method,path_window FROM analog_forward_trajectories WHERE run_id=?",
        [trajectory_run],
    ).fetchall()
    for secid, method, window in keys:
        for horizon in HORIZONS:
            frame = con.execute(
                """SELECT t.analog_date,t.forward_return,
                          list_distinct(list(p.event_family) FILTER (
                              WHERE p.event_family IS NOT NULL)) families
                   FROM analog_forward_trajectories t LEFT JOIN analog_event_profiles p
                     ON p.run_id=? AND p.secid=t.secid AND p.method=t.method
                    AND p.path_window=t.path_window AND p.analog_date=t.analog_date
                   WHERE t.run_id=? AND t.secid=? AND t.method=? AND t.path_window=?
                     AND t.forward_session=? GROUP BY 1,2""",
                [run_id, trajectory_run, secid, method, window, horizon],
            ).df()
            frame["families"] = frame.families.map(
                lambda value: list(value) if isinstance(value, (list, tuple, np.ndarray)) else []
            )
            subsets = [("all", "all", frame.forward_return),
                       ("without_event", "none", frame.loc[frame.families.map(len) == 0, "forward_return"])]
            families = sorted({item for values in frame.families for item in values})
            for family in families:
                subsets.append(("with_event", family,
                                frame.loc[frame.families.map(lambda x, f=family: f in x), "forward_return"]))
            for family in sorted(current.get(secid, set())):
                subsets.append(("current_event_match", family,
                                frame.loc[frame.families.map(lambda x, f=family: f in x), "forward_return"]))
            for subset, family, values in subsets:
                stats = summarize_subset(values)
                con.execute(
                    "INSERT OR REPLACE INTO event_conditioned_distributions "
                    "(run_id,secid,method,path_window,horizon,subset,event_family,effective_n,"
                    "median_return,q25,q75,positive_fraction,dispersion,status,reason) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [run_id, secid, method, window, horizon, subset, family,
                     stats.get("n", len(values)), stats.get("median"), stats.get("q25"),
                     stats.get("q75"), stats.get("positive"), stats.get("dispersion"),
                     stats["status"], stats["reason"]],
                )
                written += 1
    return written


def run_event_conditioning(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id,analog_run_id,cutoff FROM analog_trajectory_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise ValueError("completed Stage 45 trajectory run is required")
    trajectory_run, analog_run, cutoff = source
    run_id = hashlib.sha256(f"{trajectory_run}|{cutoff}|{VERSION}".encode()).hexdigest()[:20]
    for table in ("event_analog_runs", "analog_event_profiles", "current_event_contexts",
                  "event_conditioned_distributions"):
        con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
    con.execute(
        "INSERT INTO event_analog_runs (run_id,trajectory_run_id,cutoff,created_at,status,"
        "profile_rows,distribution_rows,methodology_version,details_json) "
        "VALUES (?,?,?,current_timestamp,'running',0,0,?,?)",
        [run_id, trajectory_run, cutoff, VERSION, json.dumps({"causal_claim": False})],
    )
    try:
        profiles = _write_profiles(con, run_id, analog_run)
        current = _current_context(con, run_id, cutoff)
        distributions = _write_distributions(con, run_id, trajectory_run, current)
        con.execute(
            "UPDATE event_analog_runs SET finished_at=current_timestamp,status='completed',"
            "profile_rows=?,distribution_rows=?,details_json=? WHERE run_id=?",
            [profiles, distributions, json.dumps({"future_surprise_used": False,
             "secondary_view_only": True, "production_changes": 0}), run_id],
        )
        return {"run_id": run_id, "profiles": profiles, "distributions": distributions,
                "current_contexts": len(current), "cutoff": cutoff}
    except Exception as exc:
        con.execute(
            "UPDATE event_analog_runs SET finished_at=current_timestamp,status='failed',"
            "details_json=? WHERE run_id=?",
            [json.dumps({"error": str(exc), "error_type": type(exc).__name__}), run_id],
        )
        raise


def event_analog_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,cutoff,profile_rows,distribution_rows FROM event_analog_runs "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "cutoff", "profiles", "distributions"), row, strict=True
    ))
