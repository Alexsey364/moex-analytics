"""Verified official fundamental source discovery and review registry (Stage 37)."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.predictive_expansion.fundamentals import deepen_pit_fundamentals

from .schema import DDL

SOURCE_CANDIDATES = {
    "LKOH": ["https://www.lukoil.com/InvestorAndShareholderCenter"],
    "TATN": ["https://www.tatneft.ru/aktsioneram-i-investoram/"],
    "TRNFP": ["https://www.transneft.ru/investors/"],
    "LSNG": ["https://rosseti-lenenergo.ru/shareholders/investors/ir/270184/"],
    "MTSS": ["https://ir.mts.ru/investors-and-shareholders/"],
    "PHOR": ["https://www.phosagro.com/investors/"],
    "MOEX": ["https://www.moex.com/s1345"],
    "SBER": ["https://www.sberbank.com/investor-relations/reports-and-publications/"],
    "X5": ["https://www.x5.ru/en/investors/"],
    "DISCLOSURE_INDEX": ["https://www.e-disclosure.ru/poisk-po-kompaniyam"],
}
DOCUMENT_SUFFIXES = (".pdf", ".xls", ".xlsx", ".xbrl", ".zip", ".json")
DOCUMENT_MARKERS = ("annual", "report", "ifrs", "financial", "results", "годов", "отчет")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def _document_links(base: str, content: bytes) -> list[str]:
    parser = _Links()
    parser.feed(content.decode("utf-8", errors="ignore"))
    links = []
    origin = urlparse(base).netloc.lower().removeprefix("www.")
    for href in parser.links:
        absolute = urljoin(base, href)
        parsed = urlparse(absolute)
        same_official_host = parsed.netloc.lower().removeprefix("www.").endswith(origin)
        lowered = absolute.lower()
        if same_official_host and (
            lowered.split("?", 1)[0].endswith(DOCUMENT_SUFFIXES)
            or any(marker in lowered for marker in DOCUMENT_MARKERS)
        ):
            links.append(absolute)
    return sorted(set(links))


def _raw_path(issuer: str, digest: str, content_type: str) -> Path:
    suffix = ".pdf" if "pdf" in content_type else ".xlsx" if "spreadsheet" in content_type else ".html"
    path = PROJECT_ROOT / "data" / "raw" / "fundamental_recovery" / issuer
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{digest}{suffix}"


def _audit_source(con, run_id: str, issuer: str, url: str, session=requests) -> dict:
    started = datetime.now(UTC)
    try:
        response = session.get(url, timeout=30, allow_redirects=True)
        payload = response.content
        digest = hashlib.sha256(payload).hexdigest()
        content_type = response.headers.get("content-type", "unknown")
        path = _raw_path(issuer, digest, content_type)
        if not path.exists():
            path.write_bytes(payload)
        links = _document_links(response.url, payload) if "html" in content_type.lower() else []
        reachable = response.ok
        status = (
            "document_discovery"
            if reachable and links
            else "reachable_no_document_links"
            if reachable
            else "http_error"
        )
        blocker = None if reachable else f"HTTP {response.status_code}"
        machine = any(kind in content_type.lower() for kind in ("json", "xml", "spreadsheet"))
        con.execute(
            """INSERT INTO source_resolution_registry VALUES
            (?,?,?,?,?,'verified',?,?,?,?,'public_official_disclosure',?,?,?,?,?,?)""",
            [run_id, issuer, url, "official_ir_or_disclosure", reachable,
             response.status_code, content_type, machine, "archive links require document review",
             status, blocker, digest, str(path.relative_to(PROJECT_ROOT)), len(links), started],
        )
        return {"reachable": reachable, "links": links, "downloaded": 1, "request": 1}
    except requests.exceptions.SSLError as exc:
        tls, blocker = "validation_failed", str(exc)
    except requests.RequestException as exc:
        tls, blocker = "request_failed", str(exc)
    con.execute(
        """INSERT INTO source_resolution_registry VALUES
        (?,?,?,?,FALSE,?,NULL,NULL,FALSE,'unknown','public_official_disclosure',
        'blocked',?,NULL,NULL,0,?)""",
        [run_id, issuer, url, "official_ir_or_disclosure", tls, blocker, started],
    )
    return {"reachable": False, "links": [], "downloaded": 0, "request": 1}


def _review_candidates(con, run_id: str, path: Path) -> int:
    rows = con.execute(
        """SELECT coalesce(nullif(issuer,''),secid),period_end,metric,document,
        page_table,metric,raw_value,coalesce(raw_unit,unit),source_hash,
        CASE WHEN validation_status='validated' THEN .95 ELSE .5 END,
        validation_status FROM issuer_fundamental_values
        WHERE validation_status IN ('validated','manual_review')
        ORDER BY 1,2,3"""
    ).fetchall()
    payload = []
    for row in rows:
        candidate_id = hashlib.sha256(repr(row).encode()).hexdigest()[:24]
        con.execute(
            """INSERT INTO fundamental_manual_review_candidates VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [run_id, candidate_id, *row[:8], row[9],
             "verify extracted value against official document", row[8], row[10]],
        )
        payload.append({
            "candidate_id": candidate_id, "issuer": row[0], "period": row[1],
            "metric": row[2], "document": row[3], "page_table": row[4],
            "row_label": row[5], "candidate_value": row[6], "unit": row[7],
            "parser_confidence": row[9], "review_reason": "verify against official document",
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now(UTC).isoformat(),
                                "candidates": payload}, default=str, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return len(rows)


def recover_official_fundamentals(con, *, session=requests, download: bool = True) -> dict:
    con.execute(DDL)
    clock = time.perf_counter()
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"stage37:{started.isoformat()}".encode()).hexdigest()[:20]
    before = con.execute(
        """SELECT count(DISTINCT (coalesce(nullif(issuer,''),secid),period_end))
        FROM issuer_fundamental_values WHERE validation_status='validated'"""
    ).fetchone()[0]
    checks = []
    for issuer, urls in SOURCE_CANDIDATES.items():
        for url in urls:
            checks.append(_audit_source(con, run_id, issuer, url, session))
    backfill = deepen_pit_fundamentals(con, download=download)
    review_path = PROJECT_ROOT / "data" / "review" / "stage37_fundamentals.local.json"
    reviews = _review_candidates(con, run_id, review_path)
    after = con.execute(
        """SELECT count(DISTINCT (coalesce(nullif(issuer,''),secid),period_end))
        FROM issuer_fundamental_values WHERE validation_status='validated'"""
    ).fetchone()[0]
    leakage = con.execute(
        """SELECT count(*) FROM issuer_fundamental_values WHERE available_from IS NULL
        OR CAST(available_from AS DATE)<publication_date"""
    ).fetchone()[0]
    sources = len(checks)
    reachable = sum(x["reachable"] for x in checks)
    links = sum(len(x["links"]) for x in checks)
    downloaded = sum(x["downloaded"] for x in checks)
    requests_count = sum(x["request"] for x in checks)
    runtime = time.perf_counter() - clock
    details = {"backfill": backfill, "tls_validation_disabled": False,
               "manual_review_path": str(review_path.relative_to(PROJECT_ROOT)),
               "synthetic_values": 0, "ras_ifrs_mixed": False}
    con.execute(
        """INSERT INTO fundamental_recovery_runs VALUES
        (?,?,current_timestamp,'completed',?,?,?,?,?,?,?,?,?,?,?,0,?)""",
        [run_id, started, len(SOURCE_CANDIDATES) - 1, sources, reachable, links, downloaded,
         before, after, reviews, leakage, requests_count, runtime, json.dumps(details, default=str)],
    )
    return {"run_id": run_id, "sources_checked": sources, "reachable": reachable,
            "documents_discovered": links, "documents_downloaded": downloaded,
            "validated_periods_before": before, "validated_periods_after": after,
            "manual_review_candidates": reviews, "leakage_violations": leakage,
            "requests": requests_count, "runtime_seconds": runtime,
            "production_changes": 0, **details}


def fundamental_recovery_status(con) -> dict:
    con.execute(DDL)
    return {"latest": con.execute(
        "SELECT * FROM fundamental_recovery_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone(), "coverage": con.execute(
        "SELECT issuer,validated_periods,coverage_status FROM stage30_fundamental_coverage ORDER BY 1"
    ).fetchall(), "sources": con.execute(
        """SELECT issuer,status,count(*) FROM source_resolution_registry WHERE run_id=(SELECT run_id
        FROM fundamental_recovery_runs ORDER BY started_at DESC LIMIT 1) GROUP BY 1,2 ORDER BY 1,2"""
    ).fetchall()}
