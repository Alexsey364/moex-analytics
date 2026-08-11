"""Stage 67 copyright-safe incremental RSS ingestion and deterministic stories."""

from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from moex_analytics.news_foundation.core import ensure_schema as ensure_source_schema

VERSION = "live-news-story-v1"
ENTITY_PATTERNS = {
    "Russia": r"\brussia|росси", "CBR": r"bank of russia|банк росс|key rate|ключев.*ставк",
    "MOEX": r"moscow exchange|московск.*бирж|\bmoex\b", "USA": r"united states|\bu\.s\.\b|сша",
    "Fed": r"federal reserve|\bfed\b|фрс", "oil": r"\boil\b|нефт", "Brent": r"\bbrent\b",
    "RUB": r"rouble|ruble|рубл", "sanctions": r"sanction|санкц", "SBERP": r"sber|сбер",
    "LKOH": r"lukoil|лукойл", "MTSS": r"\bmts\b|мтс", "TRNFP": r"transneft|транснефт",
    "PHOR": r"phosagro|фосагро", "TATNP": r"tatneft|татнефт", "LSNGP": r"lenenergo|ленэнерго",
    "X5": r"\bx5\b|пят[её]рочк",
}
EVENT_RULES = (
    ("central_bank", r"central bank|bank of russia|federal reserve|ecb|ключев.*ставк"),
    ("rates", r"interest rate|key rate|ставк"), ("inflation", r"inflation|инфляц"),
    ("FX", r"rouble|ruble|currency|exchange rate|рубл|валют"),
    ("oil", r"\boil\b|\bbrent\b|нефт"), ("sanctions", r"sanction|санкц"),
    ("geopolitics", r"geopolit|military|conflict|геополит|военн"),
    ("earnings", r"earnings|results|revenue|profit|отч[её]т|прибыл"),
    ("dividends", r"dividend|дивиденд"), ("regulation", r"regulat|закон|регулирован"),
    ("M&A", r"merger|acquisition|takeover|слияни|поглощен"),
    ("buyback", r"buyback|обратн.*выкуп"), ("SPO/FPO", r"\bspo\b|\bfpo\b"),
)
DDL = """
CREATE TABLE IF NOT EXISTS news_ingestion_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMPTZ,finished_at TIMESTAMPTZ,status VARCHAR,
 sources INTEGER,requests INTEGER,items_seen INTEGER,items_inserted INTEGER,stories_created INTEGER,
 stories_updated INTEGER,errors INTEGER,duration_seconds DOUBLE,details_json JSON);
CREATE TABLE IF NOT EXISTS news_items(
 news_id VARCHAR PRIMARY KEY,source_id VARCHAR,published_at TIMESTAMPTZ,available_from TIMESTAMPTZ,
 headline VARCHAR,summary VARCHAR,body VARCHAR,url VARCHAR,content_hash VARCHAR,language VARCHAR,
 entities_json JSON,event_type VARCHAR,story_id VARCHAR,confidence VARCHAR,tone VARCHAR,
 scheduled BOOLEAN,scheduled_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS news_stories(
 story_id VARCHAR PRIMARY KEY,event_type VARCHAR,entities_json JSON,first_report_at TIMESTAMPTZ,
 last_update_at TIMESTAMPTZ,status VARCHAR,reliability VARCHAR,source_count INTEGER,item_count INTEGER,
 official_confirmation BOOLEAN,headline VARCHAR,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS news_story_timeline(
 story_id VARCHAR,news_id VARCHAR,event_at TIMESTAMPTZ,stage VARCHAR,source_id VARCHAR,
 PRIMARY KEY(story_id,news_id));
"""


def ensure_schema(con: Any) -> None:
    ensure_source_schema(con)
    con.execute(DDL)


def extract_entities(text: str) -> list[str]:
    lowered = text.lower()
    return sorted(name for name, pattern in ENTITY_PATTERNS.items() if re.search(pattern, lowered))


def classify_event(text: str) -> str:
    lowered = text.lower()
    return next((name for name, pattern in EVENT_RULES if re.search(pattern, lowered)), "other")


def descriptive_tone(text: str) -> str:
    lowered = text.lower()
    positive = bool(re.search(r"improv|growth|record high|повыш|рост|улучш", lowered))
    negative = bool(re.search(r"declin|risk|loss|sanction|сниж|риск|убыт|санкц", lowered))
    return "mixed" if positive and negative else "positive_wording" if positive else (
        "negative_wording" if negative else "uncertain")


def _published(value: str | None, retrieved: datetime) -> datetime:
    if not value:
        return retrieved
    try:
        result = parsedate_to_datetime(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return retrieved


def parse_rss(content: bytes, source_id: str, retrieved: datetime) -> list[dict[str, Any]]:
    root = ET.fromstring(content)
    rows = []
    for item in root.findall(".//item"):
        headline = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()[:500]
        published = _published(item.findtext("pubDate"), retrieved)
        if not headline or not url:
            continue
        digest = hashlib.sha256(f"{source_id}|{url}|{headline}".encode()).hexdigest()
        rows.append({"news_id": digest[:24], "source_id": source_id, "published_at": published,
            "available_from": retrieved, "headline": headline, "summary": summary, "body": None,
            "url": url, "content_hash": hashlib.sha256((headline + summary).encode()).hexdigest(),
            "language": "ru" if re.search("[А-Яа-я]", headline) else "en"})
    return rows


def _story_id(event_type: str, entities: list[str], published: datetime, headline: str) -> str:
    anchors = ",".join(entities[:4]) or "market"
    # The headline is deliberately not part of the key: official updates often
    # rewrite it, while event/entities/day are stable PIT-safe anchors.
    del headline
    return hashlib.sha256(f"{event_type}|{anchors}|{published:%Y-%m-%d}".encode()).hexdigest()[:20]


def ingest_records(con: Any, source_id: str, records: list[dict[str, Any]], retrieved: datetime) -> dict:
    ensure_schema(con)
    inserted = created = updated = 0
    for record in records:
        if con.execute("SELECT 1 FROM news_items WHERE news_id=?", [record["news_id"]]).fetchone():
            continue
        text = f"{record['headline']} {record['summary']}"
        entities, event_type = extract_entities(text), classify_event(text)
        story_id = _story_id(event_type, entities, record["published_at"], record["headline"])
        existing = con.execute("SELECT item_count,source_count FROM news_stories WHERE story_id=?",
                               [story_id]).fetchone()
        scheduled_at = record.get("scheduled_at")
        con.execute("INSERT INTO news_items (news_id,source_id,published_at,available_from,headline,"
            "summary,body,url,content_hash,language,entities_json,event_type,story_id,confidence,tone,"
            "scheduled,scheduled_at,retrieved_at,immutable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "'descriptive_only',?,?,?,?,true)", [record["news_id"], source_id,
            record["published_at"], retrieved, record["headline"], record["summary"], None,
            record["url"], record["content_hash"], record["language"], json.dumps(entities),
            event_type, story_id, descriptive_tone(text), scheduled_at is not None, scheduled_at,
            retrieved])
        con.execute("INSERT INTO news_provenance (news_id,source_id,url,retrieved_at,published_at,"
            "updated_at,content_hash,retention_policy) VALUES (?,?,?,?,?,?,?,"
            "'metadata_headline_summary_only')",
            [record["news_id"], source_id, record["url"], retrieved, record["published_at"], None,
             record["content_hash"]])
        if existing:
            con.execute("UPDATE news_stories SET last_update_at=?,item_count=item_count+1,status="
                        "'developing' WHERE story_id=?", [record["published_at"], story_id])
            updated += 1
            stage = "new_details"
        else:
            con.execute("INSERT INTO news_stories (story_id,event_type,entities_json,first_report_at,"
                "last_update_at,status,reliability,source_count,item_count,official_confirmation,"
                "headline,immutable) VALUES (?,?,?,?,?,'emerging','official_confirmation',"
                "1,1,true,?,true)", [story_id, event_type, json.dumps(entities),
                record["published_at"], record["published_at"], record["headline"]])
            created += 1
            stage = "first_report"
        con.execute("INSERT INTO news_story_timeline (story_id,news_id,event_at,stage,source_id) "
                    "VALUES (?,?,?,?,?)",
                    [story_id, record["news_id"], record["published_at"], stage, source_id])
        source_count = con.execute("SELECT count(DISTINCT source_id) FROM news_story_timeline "
                                   "WHERE story_id=?", [story_id]).fetchone()[0]
        con.execute("UPDATE news_stories SET source_count=?,reliability=? WHERE story_id=?",
                    [source_count, "multi_source_official" if source_count > 1
                     else "official_confirmation", story_id])
        inserted += 1
    return {"inserted": inserted, "stories_created": created, "stories_updated": updated}


def ingest_live_news(con: Any, session: requests.Session | None = None) -> dict[str, Any]:
    ensure_schema(con)
    run_id = hashlib.sha256(f"{VERSION}|{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:20]
    started = time.perf_counter()
    con.execute("INSERT INTO news_ingestion_runs (run_id,started_at,finished_at,status,sources,"
                "requests,items_seen,items_inserted,stories_created,stories_updated,errors,"
                "duration_seconds,details_json) VALUES (?,current_timestamp,NULL,'running',0,0,0,"
                "0,0,0,0,NULL,'{}')", [run_id])
    sources = con.execute("SELECT source_id,endpoint FROM news_source_registry WHERE "
                          "status='active_metadata_only' ORDER BY source_id").fetchall()
    client = session or requests.Session()
    requests_count = seen = inserted = created = updated = errors = 0
    details = []
    for source_id, endpoint in sources:
        retrieved = datetime.now(UTC)
        try:
            response = client.get(endpoint, timeout=(10, 30), headers={"User-Agent": "moex-analytics/1"})
            requests_count += 1
            response.raise_for_status()
            records = parse_rss(response.content, source_id, retrieved)
            result = ingest_records(con, source_id, records, retrieved)
            seen += len(records)
            inserted += result["inserted"]
            created += result["stories_created"]
            updated += result["stories_updated"]
            details.append({"source": source_id, "status": "completed", "seen": len(records)})
        except Exception as exc:
            errors += 1
            details.append({"source": source_id, "status": "failed", "error": type(exc).__name__})
    duration = time.perf_counter() - started
    status = "completed_with_warnings" if errors else "completed"
    con.execute("UPDATE news_ingestion_runs SET finished_at=current_timestamp,status=?,sources=?,"
        "requests=?,items_seen=?,items_inserted=?,stories_created=?,stories_updated=?,errors=?,"
        "duration_seconds=?,details_json=? WHERE run_id=?", [status, len(sources), requests_count,
        seen, inserted, created, updated, errors, duration, json.dumps(details), run_id])
    return {"run_id": run_id, "status": status, "sources": len(sources), "requests": requests_count,
            "seen": seen, "inserted": inserted, "stories_created": created,
            "stories_updated": updated, "errors": errors, "duration_seconds": duration}


def news_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    return dict(zip(("items", "stories", "date_from", "date_to"), con.execute(
        "SELECT count(*),(SELECT count(*) FROM news_stories),min(published_at),max(published_at) "
        "FROM news_items").fetchone(), strict=True))
