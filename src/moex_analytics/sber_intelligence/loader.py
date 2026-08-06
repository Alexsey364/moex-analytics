"""Official metadata ingestion and deterministic event construction."""

import hashlib
import json
from datetime import datetime, time

from .classifier import classify
from .deduplication import canonical_key
from .discovery import discover
from .expectations import calculate_all as calculate_expectations
from .impact import build_impacts
from .parser import content_hash
from .quality import run as run_quality
from .repository import VERSION, build_live_state, build_studies, calculate_reactions, status
from .validation import validate


def _hash_state(con):
    row = con.execute("SELECT count(*),max(loaded_at) FROM fundamental_documents").fetchone()
    div = con.execute("SELECT count(*),max(loaded_at) FROM dividends WHERE canonical_secid='SBER'").fetchone()
    macro = con.execute(
        "SELECT count(*),max(loaded_at) FROM macro_observations WHERE series_id='cbr_key_rate'"
    ).fetchone()
    return hashlib.sha256(repr((row, div, macro, VERSION)).encode()).hexdigest()[:16]


def load_documents(con) -> int:
    rows = con.execute(
        "SELECT document_id,title,source_url,publication_date,available_from,document_type,validation_status FROM fundamental_documents"
    ).fetchall()
    written = 0
    for doc, title, url, published, available, kind, validation_status in rows:
        before = con.execute(
            "SELECT count(*) FROM sber_information_documents WHERE document_id=?", [doc]
        ).fetchone()[0]
        con.execute(
            """INSERT INTO sber_information_documents VALUES (?,?,?,?,?,?,?,?,?,current_timestamp,?) ON CONFLICT(document_id) DO UPDATE SET validation_status=excluded.validation_status""",
            [
                doc,
                "cbr",
                title,
                url,
                available,
                available,
                content_hash(title, url),
                kind,
                validation_status,
                json.dumps({"publication_date": str(published), "full_text_stored": False}),
            ],
        )
        written += 1 - before
    return written


def build_events(con) -> dict:
    documents = con.execute(
        "SELECT document_id,title,source_url,published_at,available_from,document_type,validation_status FROM sber_information_documents ORDER BY available_from"
    ).fetchall()
    written = 0
    for doc, title, url, published, available, kind, doc_status in documents:
        event_type, subtype, rule = classify(kind)
        canonical = canonical_key("SBER", event_type, available, "annual")
        event_id = f"event-{doc}"
        candidate = {
            "available_from": available,
            "source_url": url,
            "point_in_time_safe": True,
            "official_status": "official",
        }
        validation_status, issues = validate(candidate)
        validation_status = "manual_review" if doc_status != "validated" else validation_status
        before = con.execute("SELECT count(*) FROM sber_events WHERE event_id=?", [event_id]).fetchone()[0]
        con.execute(
            """INSERT INTO sber_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp,?) ON CONFLICT(event_id) DO UPDATE SET validation_status=excluded.validation_status,confirmation_count=excluded.confirmation_count""",
            [
                event_id,
                canonical,
                event_type,
                subtype,
                title,
                None,
                "SBER",
                None,
                None,
                published,
                available,
                "cbr",
                url,
                doc,
                "official",
                1,
                "unknown",
                1.0,
                "unknown",
                0.7,
                "medium_term",
                True,
                validation_status,
                rule,
                "; ".join(issues),
            ],
        )
        con.execute(
            "INSERT INTO sber_event_entities VALUES (?,'SBER','issuer','direct') ON CONFLICT DO NOTHING",
            [event_id],
        )
        written += 1 - before
    groups = con.execute(
        "SELECT canonical_event_id,count(*) FROM sber_events WHERE document_id IS NOT NULL GROUP BY 1"
    ).fetchall()
    for canonical, count in groups:
        con.execute(
            "UPDATE sber_events SET confirmation_count=? WHERE canonical_event_id=?", [count, canonical]
        )
    return {
        "written": written,
        "documents": len(documents),
        "canonical": len(groups),
        "duplicates": len(documents) - len(groups),
    }


def build_key_rate_events(con) -> int:
    rows = con.execute(
        "SELECT observation_date,available_from,value,vintage,source FROM macro_observations WHERE series_id='cbr_key_rate' ORDER BY available_from"
    ).fetchall()
    written = 0
    for obs, available, value, vintage, source in rows:
        event_id = f"cbr-rate-{obs}-{vintage}"
        canonical = canonical_key("CBR", "regulatory", available, "key_rate")
        before = con.execute("SELECT count(*) FROM sber_events WHERE event_id=?", [event_id]).fetchone()[0]
        con.execute(
            """INSERT INTO sber_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp,?) ON CONFLICT DO NOTHING""",
            [
                event_id,
                canonical,
                "regulatory",
                "key_rate",
                f"Ключевая ставка ЦБ: {value:g}%",
                None,
                "CBR",
                None,
                available,
                available,
                available,
                "cbr",
                "https://cbr.ru/hd_base/keyrate/",
                None,
                "official",
                1,
                "unknown",
                0.7,
                "unknown",
                0.8,
                "medium_term",
                True,
                "validated",
                "CBR series cbr_key_rate",
                None,
            ],
        )
        con.execute(
            "INSERT INTO sber_event_metrics VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            [event_id, "key_rate", obs, available.date(), available, value, "percent", source, vintage],
        )
        written += 1 - before
    return written


def build_dividend_events(con) -> int:
    rows = con.execute(
        "SELECT registry_close_date,dividend_per_share,source,loaded_at FROM dividends "
        "WHERE canonical_secid='SBER' ORDER BY registry_close_date"
    ).fetchall()
    written = 0
    for record_date, dps, source, loaded_at in rows:
        event_id = f"sber-dividend-record-{record_date}"
        canonical = canonical_key("SBER", "dividend", datetime.combine(record_date, time.min), "record_date")
        before = con.execute("SELECT count(*) FROM sber_events WHERE event_id=?", [event_id]).fetchone()[0]
        con.execute(
            """INSERT INTO sber_events VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp,?)
            ON CONFLICT(event_id) DO UPDATE SET canonical_event_id=excluded.canonical_event_id""",
            [
                event_id,
                canonical,
                "dividend",
                "record_date",
                f"Дивидендная отсечка SBER: {dps:g} RUB",
                None,
                "SBER",
                None,
                record_date,
                None,
                loaded_at,
                "moex",
                source,
                None,
                "official",
                1,
                "known_after_load",
                1.0,
                "neutral",
                0.6,
                "single_session",
                True,
                "manual_review",
                "MOEX ISS registry date; publication date unavailable",
                "Not strict-study eligible: original publication time absent",
            ],
        )
        con.execute(
            "INSERT INTO sber_event_metrics VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            [
                event_id,
                "dividend_per_share",
                record_date,
                loaded_at.date(),
                loaded_at,
                dps,
                "RUB/share",
                source,
                "original",
            ],
        )
        written += 1 - before
    return written


def update(con) -> dict:
    discover(con)
    state_hash = _hash_state(con)
    previous = con.execute(
        "SELECT run_id FROM sber_intelligence_runs WHERE input_hash=? AND status='success' ORDER BY finished_at DESC LIMIT 1",
        [state_hash],
    ).fetchone()
    if previous:
        return {"status": "no_change", "run_id": previous[0], "rows_written": 0, **status(con)}
    run_id = f"intel-{state_hash}"
    documents = load_documents(con)
    events = build_events(con)
    rates = build_key_rate_events(con)
    dividends = build_dividend_events(con)
    expectations = calculate_expectations(con)
    reactions = calculate_reactions(con)
    studies = build_studies(con)
    impacts = build_impacts(con, VERSION)
    live = build_live_state(con)
    quality = run_quality(con)
    con.execute(
        "INSERT INTO sber_intelligence_runs VALUES (?, 'update',current_timestamp,current_timestamp,'success',?,?,?,?,?,?)",
        [
            run_id,
            state_hash,
            events["documents"],
            events["written"] + rates + dividends,
            events["duplicates"],
            json.dumps(
                {
                    "expectations": expectations,
                    "reactions": reactions,
                    "studies": studies,
                    "impacts": impacts,
                    "live": live,
                    "quality": quality,
                }
            ),
            VERSION,
        ],
    )
    return {
        "status": "success",
        "run_id": run_id,
        "new_documents": documents,
        "new_events": events["written"] + rates + dividends,
        "event_build": events,
        "key_rate_events": rates,
        "dividend_events": dividends,
        "expectations": expectations,
        "reactions": reactions,
        "studies": studies,
        "impacts": impacts,
        "live": live,
        **status(con),
    }
