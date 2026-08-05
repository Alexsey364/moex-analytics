"""Official document catalogue, download and deduplication."""

from __future__ import annotations

import hashlib
import re
from calendar import monthrange
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import requests

PARSER_VERSION = "cbr-html-v1"
CBR = "https://cbr.ru/banking_sector/credit/coinfo/{form}/1904/?dt={archive}&regnum=1481"


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def archive_candidates(first_year: int = 1997, last_year: int | None = None) -> list[dict]:
    """Search the whole plausible public-reporting history; do not impose a 2010/2013 cutoff."""
    end = last_year or date.today().year - 1
    result = []
    for year in range(first_year, end + 1):
        archive = f"{year + 1}01"
        for form, kind in (("f806", "RAS annual balance"), ("f807", "RAS annual income")):
            result.append(
                {
                    "year": year,
                    "archive": archive,
                    "form": form,
                    "document_type": kind,
                    "source_url": CBR.format(form=form, archive=archive),
                }
            )
    return result


def _publication_date(archive: str) -> date:
    """Conservative availability: last day of the official archive month."""
    year, month = int(archive[:4]), int(archive[4:])
    return date(year, month, monthrange(year, month)[1])


def discover(con: duckdb.DuckDBPyConnection, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    found = 0
    checked = 0
    for item in archive_candidates():
        checked += 1
        response = client.get(item["source_url"], timeout=30, headers={"User-Agent": "moex-analytics/0.1.0"})
        response.raise_for_status()
        title_match = re.search(r"<title>(.*?)\s*\|\s*Банк России</title>", response.text, re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        if not title:
            continue
        digest = file_hash(response.content)
        document_id = digest[:24]
        period_end = date(item["year"], 12, 31)
        published = _publication_date(item["archive"])
        con.execute(
            """INSERT INTO fundamental_documents VALUES
          (?,'SBER',?,'RAS',?,?,?,?,?,?,NULL,?,'text/html',?,'discovered','pending',
           'original',current_timestamp,?) ON CONFLICT(source_url,revision_id) DO NOTHING""",
            [
                document_id,
                item["document_type"],
                date(item["year"], 1, 1),
                period_end,
                published,
                datetime.combine(published, time(23, 59), ZoneInfo("Europe/Moscow")),
                title,
                item["source_url"],
                digest,
                PARSER_VERSION,
                "publication_date uses conservative archive-month end; exact timestamp absent in HTML",
            ],
        )
        found += int(con.execute("SELECT changes()").fetchone()[0]) if False else 1
    return {"checked": checked, "found": found}


def download(con: duckdb.DuckDBPyConnection, raw_root: Path, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    downloaded = 0
    unchanged = 0
    rows = con.execute("""SELECT document_id,source_url,file_hash FROM fundamental_documents
                          WHERE processing_status='discovered' ORDER BY period_end,document_type""").fetchall()
    for document_id, url, expected_hash in rows:
        response = client.get(url, timeout=30, headers={"User-Agent": "moex-analytics/0.1.0"})
        response.raise_for_status()
        digest = file_hash(response.content)
        if digest != expected_hash:
            con.execute(
                "UPDATE fundamental_documents SET processing_status='requires_manual_review',notes=notes||'; hash changed' WHERE document_id=?",
                [document_id],
            )
            continue
        path = raw_root / f"{document_id}.html"
        if path.exists() and file_hash(path.read_bytes()) == digest:
            unchanged += 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            downloaded += 1
        con.execute(
            "UPDATE fundamental_documents SET local_path=?,processing_status='downloaded' WHERE document_id=?",
            [str(path), document_id],
        )
    return {"downloaded": downloaded, "unchanged": unchanged, "documents": len(rows)}
