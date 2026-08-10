"""Canonical PIT event materialization from already persisted provenance layers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .schema import DDL

VERSION = "historical-events-v1"
PORTFOLIO = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")

SOURCE_CATALOG = (
    (
        "cbr",
        "Bank of Russia",
        ["central_bank", "macro"],
        "official",
        True,
        "https://www.cbr.ru/",
        "official_public",
        True,
        True,
        "publication_time_required",
        "Calendar availability must be preserved separately from released outcomes.",
    ),
    (
        "moex",
        "Moscow Exchange ISS",
        ["market", "corporate"],
        "official_api",
        True,
        "https://iss.moex.com/iss/",
        "official_public",
        True,
        True,
        "endpoint_dependent",
        "Announcement timestamps are not present in every ISS dataset.",
    ),
    (
        "issuer",
        "Official issuer disclosures",
        ["corporate", "dividend"],
        "official",
        True,
        "issuer disclosure pages",
        "source_specific",
        False,
        False,
        "document_timestamp",
        "Automated mass download is disabled until source-specific license review.",
    ),
    (
        "fed",
        "Federal Reserve",
        ["central_bank", "macro"],
        "official",
        True,
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "official_public",
        False,
        False,
        "publication_time_required",
        "Catalog only; no bulk ingestion in Stage 42.",
    ),
    (
        "ecb",
        "European Central Bank",
        ["central_bank", "macro"],
        "official",
        True,
        "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
        "official_public",
        False,
        False,
        "publication_time_required",
        "Catalog only; use only when relevant.",
    ),
)

CRISIS_EPISODES = (
    (
        "ru-1998",
        "1998 Russian crisis",
        "1998-08-17",
        "1998-12-31",
        ["default_restructuring", "fx_shock"],
        "manual research label; source review required",
    ),
    (
        "gfc-2008",
        "2008 global financial crisis",
        "2008-09-15",
        "2009-03-31",
        ["global_financial_shock"],
        "manual research label; source review required",
    ),
    (
        "ru-2014",
        "2014 RUB/oil/geopolitical shock",
        "2014-07-01",
        "2015-02-28",
        ["fx_shock", "commodity_shock", "sanctions_announced"],
        "manual research label; boundaries are explainability metadata",
    ),
    (
        "covid-2020",
        "2020 COVID",
        "2020-03-11",
        "2020-06-30",
        ["pandemic_event", "global_financial_shock"],
        "manual research label; source review required",
    ),
    (
        "ru-2022",
        "2022 market closure/sanctions shock",
        "2022-02-24",
        "2022-03-24",
        ["sanctions_announced", "market_closed", "capital_control_announced"],
        "manual research label; individual events require official timestamps",
    ),
)


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _exists(con: Any, table: str) -> bool:
    return bool(
        con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [table]).fetchone()[
            0
        ]
    )


def _hash(*values: object) -> str:
    return hashlib.sha256("|".join(str(value or "") for value in values).encode()).hexdigest()


def classify_pit(available_from, expected: bool, surprise: bool) -> tuple[str, str]:
    if available_from is None:
        return "manual_review", "missing_available_from"
    if expected and surprise:
        return "manual_review", "contradictory_schedule_flags"
    return "validated", "pit_safe"


def _upsert(con: Any, values: list[Any]) -> int:
    before = con.execute("SELECT count(*) FROM historical_events WHERE event_id=?", [values[0]]).fetchone()[0]
    marks = ",".join("?" for _ in values)
    con.execute(f"INSERT OR REPLACE INTO historical_events VALUES ({marks})", values)
    return int(before == 0)


def sync_event_calendar(con: Any) -> tuple[int, int]:
    if not _exists(con, "event_calendar"):
        return 0, 0
    rows = con.execute("SELECT * FROM event_calendar ORDER BY event_id").fetchall()
    written = 0
    for (
        event_id,
        event_type,
        country,
        issuer,
        scheduled,
        released,
        source,
        status,
        importance,
        loaded,
        notes,
    ) in rows:
        expected = released is None and scheduled is not None
        validation, pit = classify_pit(released, expected, False)
        start = released or scheduled
        family = (
            "central_bank" if "rate" in event_type else "corporate" if "dividend" in event_type else "market"
        )
        written += _upsert(
            con,
            [
                f"calendar:{event_id}",
                family,
                event_type,
                None,
                event_type.replace("_", " "),
                notes,
                start,
                None,
                released,
                start,
                released,
                country,
                None,
                issuer,
                None,
                "issuer" if issuer else "market",
                expected,
                False,
                importance,
                source,
                "persisted_table",
                source in {"CBR", "MOEX ISS"},
                source,
                loaded or datetime.now(),
                _hash(event_id, released, scheduled, source),
                validation,
                pit,
                f"{status}; scheduled dates without announcement timestamp "
                "are excluded from pre-event features",
                "event_calendar",
                event_id,
            ],
        )
    return len(rows), written


def sync_sber_events(con: Any) -> tuple[int, int]:
    if not _exists(con, "sber_events"):
        return 0, 0
    rows = con.execute(
        "SELECT event_id,event_type,event_subtype,title,description,scheduled_at,occurred_at,published_at,"
        "available_from,related_entity,expected_status,severity,source_id,source_url,official_status,"
        "point_in_time_safe,validation_status,notes FROM sber_events ORDER BY event_id"
    ).fetchall()
    written = 0
    for row in rows:
        (
            event_id,
            event_type,
            subtype,
            title,
            description,
            scheduled,
            occurred,
            published,
            available,
            entity,
            expected_status,
            severity,
            source,
            reference,
            official,
            pit_safe,
            old_status,
            notes,
        ) = row
        expected = bool(scheduled and str(expected_status).lower() in {"expected", "scheduled", "known"})
        surprise = not expected and occurred is not None
        validation, pit = classify_pit(available, expected, surprise)
        if not pit_safe or old_status != "validated":
            validation = "manual_review"
            pit = "source_not_validated"
        written += _upsert(
            con,
            [
                f"sber:{event_id}",
                "corporate" if entity == "SBER" else "market",
                event_type,
                subtype,
                title,
                description,
                occurred or scheduled or available,
                None,
                published,
                occurred or scheduled,
                available,
                "RU",
                None,
                entity,
                "financials" if entity == "SBER" else None,
                "issuer" if entity == "SBER" else "market",
                expected,
                surprise,
                str(severity) if severity is not None else None,
                source,
                "official_or_reviewed",
                official == "official",
                reference,
                datetime.now(),
                _hash(event_id, available, reference),
                validation,
                pit,
                notes,
                "sber_events",
                event_id,
            ],
        )
    return len(rows), written


def build_catalog(con: Any) -> int:
    for row in SOURCE_CATALOG:
        source_id, name, families, *rest = row
        con.execute(
            "INSERT OR REPLACE INTO historical_event_sources VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [source_id, name, json.dumps(families), *rest],
        )
    for episode_id, label, start, end, categories, reference in CRISIS_EPISODES:
        con.execute(
            "INSERT OR REPLACE INTO historical_crisis_episodes VALUES (?,?,?,?,?,?,?,TRUE,?)",
            [
                episode_id,
                label,
                start,
                end,
                json.dumps(categories),
                reference,
                "manual_review",
                "Explainability label only; not a causal feature and not automatically predictive.",
            ],
        )
    return len(SOURCE_CATALOG)


def validate_events(con: Any) -> dict[str, int]:
    ensure_schema(con)
    con.execute("DELETE FROM historical_event_quality_issues")
    issues: list[tuple[str, str, str, str]] = []
    rows = con.execute(
        "SELECT event_id,available_from,event_start,announcement_time,effective_time,"
        "expected_or_scheduled,surprise_event,content_hash,validation_status FROM historical_events"
    ).fetchall()
    seen: dict[str, str] = {}
    for event_id, available, start, announcement, effective, expected, surprise, digest, status in rows:
        if available is None:
            issues.append((event_id, "missing_available_from", "critical", "PIT availability is unknown"))
        if announcement and available and announcement > available:
            issues.append(
                (event_id, "publication_leakage", "critical", "available_from precedes announcement")
            )
        if expected and surprise:
            issues.append(
                (event_id, "schedule_conflict", "critical", "event cannot be scheduled and surprise")
            )
        if status == "validated" and start is None:
            issues.append((event_id, "missing_event_time", "critical", "validated event has no event time"))
        if effective and announcement and effective < announcement:
            issues.append((event_id, "effective_before_announcement", "warning", "verify timestamps"))
        if digest in seen and seen[digest] != event_id:
            issues.append((event_id, "duplicate_source_copy", "warning", f"same hash as {seen[digest]}"))
        seen[digest] = event_id
    for event_id, kind, severity, description in issues:
        issue_id = _hash(event_id, kind)[:24]
        con.execute(
            "INSERT INTO historical_event_quality_issues VALUES (?,?,?,?,?,current_timestamp)",
            [issue_id, event_id, kind, severity, description],
        )
    return {
        "events": len(rows),
        "issues": len(issues),
        "critical": sum(severity == "critical" for _, _, severity, _ in issues),
    }


def build_timeline(con: Any) -> int:
    """Build PIT-safe states. Surprise events never expose a pre-event countdown."""
    ensure_schema(con)
    con.execute("DELETE FROM historical_event_timeline WHERE calculation_version=?", [VERSION])
    if not _exists(con, "trading_calendar"):
        return 0
    dates = [
        row[0]
        for row in con.execute("SELECT trade_date FROM trading_calendar ORDER BY trade_date").fetchall()
    ]
    events = con.execute(
        "SELECT event_id,CAST(event_start AS DATE),CAST(available_from AS DATE),issuer,"
        "expected_or_scheduled,surprise_event FROM historical_events "
        "WHERE validation_status='validated' AND event_start IS NOT NULL AND available_from IS NOT NULL"
    ).fetchall()
    written = 0
    for event_id, event_date, available_date, issuer, expected, surprise in events:
        for trade_date in dates:
            since = (trade_date - event_date).days if trade_date >= event_date else None
            until = (
                (event_date - trade_date).days
                if expected and not surprise and available_date <= trade_date < event_date
                else None
            )
            if trade_date < event_date and until is None:
                continue
            state = (
                "pre_event"
                if until is not None
                else "event_day"
                if since == 0
                else "post_1"
                if since == 1
                else "post_5"
                if since is not None and since <= 5
                else "post_20"
                if since is not None and since <= 20
                else None
            )
            if state is None:
                continue
            secids = [issuer] if issuer in PORTFOLIO else ["MARKET"]
            for secid in secids:
                con.execute(
                    "INSERT INTO historical_event_timeline VALUES (?,?,?,?,?,?,TRUE,?,current_timestamp)",
                    [trade_date, event_id, secid, since, until, state, VERSION],
                )
                written += 1
    return written


def build_foundation(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    run_id = f"event-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    con.execute(
        "INSERT INTO historical_event_runs(run_id,started_at,status,methodology_version) "
        "VALUES (?,current_timestamp,'running',?)",
        [run_id, VERSION],
    )
    build_catalog(con)
    calendar_rows, calendar_written = sync_event_calendar(con)
    sber_rows, sber_written = sync_sber_events(con)
    validation = validate_events(con)
    timeline = build_timeline(con)
    source_rows = calendar_rows + sber_rows
    written = calendar_written + sber_written
    details = {
        "event_calendar": calendar_rows,
        "sber_events": sber_rows,
        "production_changes": 0,
        "probability_gate_changes": 0,
    }
    con.execute(
        "UPDATE historical_event_runs SET finished_at=current_timestamp,status='completed',"
        "source_rows=?,events_written=?,timeline_rows=?,issues=?,details_json=? WHERE run_id=?",
        [source_rows, written, timeline, validation["issues"], json.dumps(details), run_id],
    )
    return {
        "run_id": run_id,
        "source_rows": source_rows,
        "events_written": written,
        "timeline_rows": timeline,
        **validation,
    }


def event_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    totals = con.execute(
        "SELECT count(*),count(*) FILTER(WHERE validation_status='validated'),"
        "count(*) FILTER(WHERE validation_status='manual_review'),min(CAST(event_start AS DATE)),"
        "max(CAST(event_start AS DATE)),count(DISTINCT event_family) FROM historical_events"
    ).fetchone()
    return {
        "events": totals[0],
        "validated": totals[1],
        "manual_review": totals[2],
        "date_from": totals[3],
        "date_to": totals[4],
        "families": totals[5],
        "timeline_rows": con.execute("SELECT count(*) FROM historical_event_timeline").fetchone()[0],
        "issues": con.execute("SELECT count(*) FROM historical_event_quality_issues").fetchone()[0],
    }
