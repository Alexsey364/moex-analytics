"""Actual downloads from official sources with hashes and PIT timestamps."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from html.parser import HTMLParser

import requests

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.database import database_path, insert_dividends
from moex_analytics.fundamentals.generic import ensure_generic_schema, migrate_sber, update_coverage
from moex_analytics.macro.repository import upsert_observations
from moex_analytics.macro.sources import cbr
from moex_analytics.moex_client import MoexClient

from .schema import DDL

VERSION = "actual-backfill-v1"
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "fundamentals_actual"

MOEX_ANNUAL = (
    {
        "url": "https://www.moex.com/n15254",
        "period": date(2016, 12, 31),
        "published": date(2017, 3, 2),
        "metrics": (
            ("operating_income", "Operating income", "43.57", 43.57e9, "RUB"),
            ("fee_commission_income", "Fee and commission income", "19.80", 19.80e9, "RUB"),
            ("ebitda", "EBITDA", "33.60", 33.60e9, "RUB"),
            ("operating_expenses", "Operating expenses", "12.26", 12.26e9, "RUB"),
            ("net_profit", "Net income", "25.18", 25.18e9, "RUB"),
        ),
    },
    {
        "url": "https://www.moex.com/n18752",
        "period": date(2017, 12, 31),
        "published": date(2018, 3, 2),
        "metrics": (
            ("fee_commission_income", "Fee and commission", "21.21", 21.21e9, "RUB"),
            ("net_interest_income", "Net interest", "17.29", 17.29e9, "RUB"),
            ("operating_costs_ex_da", "Operating costs excluding", "10.48", 10.48e9, "RUB"),
            ("ebitda_margin", "EBITDA margin", "72.8", 72.8, "percent"),
            ("net_profit", "Net income", "20.26", 20.26e9, "RUB"),
            ("eps", "basic EPS", "9.02", 9.02, "RUB/share"),
        ),
    },
    {
        "url": "https://www.moex.com/n22813",
        "period": date(2018, 12, 31),
        "published": date(2019, 3, 6),
        "metrics": (
            ("fee_commission_income", "Fee and commission income", "23.6", 23.6e9, "RUB"),
            ("net_interest_income", "Net interest", "15.8", 15.8e9, "RUB"),
            ("adjusted_ebitda", "Adjusted EBITDA", "28.7", 28.7e9, "RUB"),
            ("ebitda_margin", "margin", "71.9", 71.9, "percent"),
            ("eps", "earnings per share", "8.76", 8.76, "RUB/share"),
        ),
    },
    {
        "url": "https://www.moex.com/n27211",
        "period": date(2019, 12, 31),
        "published": date(2020, 3, 6),
        "metrics": (
            ("operating_income", "Operating income", "43,229.5", 43229.5e6, "RUB"),
            ("fee_commission_income", "Fee and commission income", "26,181.4", 26181.4e6, "RUB"),
            ("net_interest_income", "Net interest", "16,713.0", 16713e6, "RUB"),
            ("operating_expenses", "Operating expenses", "15,435.3", 15435.3e6, "RUB"),
            ("adjusted_ebitda", "Adjusted EBITDA", "31,123.2", 31123.2e6, "RUB"),
            ("net_profit", "net profit", "20.2", 20.2e9, "RUB"),
        ),
    },
)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(" ".join(data.split()))


def ensure_schema(con) -> None:
    con.execute(DDL)
    ensure_generic_schema(con)


def _counts(con) -> dict:
    ensure_schema(con)
    values = con.execute(
        """SELECT issuer,count(*),count(DISTINCT period_end),min(period_end),max(period_end)
        FROM issuer_fundamental_values WHERE validation_status='validated'
        GROUP BY issuer ORDER BY issuer"""
    ).fetchall()
    documents = con.execute(
        "SELECT issuer,count(*) FROM actual_document_inventory GROUP BY issuer ORDER BY issuer"
    ).fetchall()
    return {"values": values, "documents": documents}


def _download(session: requests.Session, spec: dict) -> tuple[bytes, str]:
    response = session.get(spec["url"], timeout=60, headers={"User-Agent": "moex-analytics/0.1 research"})
    response.raise_for_status()
    content = response.content
    if len(content) < 1000:
        raise RuntimeError(f"official document too small: {spec['url']}")
    return content, response.headers.get("content-type", "application/octet-stream")


def _save_document(con, issuer: str, spec: dict, content: bytes, mime: str) -> str:
    digest = hashlib.sha256(content).hexdigest()
    document_id = digest[:24]
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    path = RAW_ROOT / f"{issuer}_{spec['period']}_{document_id}.html"
    if not path.exists():
        path.write_bytes(content)
    con.execute(
        """INSERT OR REPLACE INTO actual_document_inventory VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
        [document_id, issuer, "official annual results", "IFRS", str(spec["period"]),
         spec["published"], spec["url"], mime, digest, len(content), VERSION,
         "downloaded_hashed", str(path.relative_to(PROJECT_ROOT))],
    )
    return document_id


def import_moex_annual_history(con, session: requests.Session | None = None) -> dict:
    ensure_schema(con)
    client = session or requests.Session()
    documents = inserted = review = 0
    for spec in MOEX_ANNUAL:
        content, mime = _download(client, spec)
        document_id = _save_document(con, "MOEX", spec, content, mime)
        digest = hashlib.sha256(content).hexdigest()
        parser = TextExtractor()
        parser.feed(content.decode("utf-8", errors="replace"))
        text = re.sub(r"\s+", " ", " ".join(parser.parts))
        available = datetime.combine(spec["published"], time(9, 30), UTC)
        con.execute(
            """INSERT OR REPLACE INTO issuer_fundamental_documents VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
            [document_id, "MOEX", "MOEX IR", "moex.com", spec["url"], spec["url"],
             "annual results HTML", "IFRS", str(spec["period"]), spec["published"], available,
             mime, digest, True, "parsed", "validated", None, None],
        )
        documents += 1
        for metric, label, token, value, unit in spec["metrics"]:
            label_ok = label.lower() in text.lower()
            token_ok = token in text or token.replace(",", " ") in text
            if not (label_ok and token_ok):
                candidate_id = hashlib.sha256(f"{document_id}:{metric}".encode()).hexdigest()[:24]
                con.execute(
                    """INSERT OR REPLACE INTO actual_manual_review_candidates VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',current_timestamp)""",
                    [candidate_id, "MOEX", metric, str(spec["period"]), spec["published"],
                     spec["url"], digest, None, "financial highlights", label, value, unit,
                     "expected label/token pair not both found in downloaded bytes"],
                )
                review += 1
                continue
            before = con.execute(
                """SELECT count(*) FROM issuer_fundamental_values
                WHERE secid='MOEX' AND metric=? AND period_end=? AND reporting_standard='IFRS'
                AND revision='original'""", [metric, spec["period"]]
            ).fetchone()[0]
            con.execute(
                """INSERT OR REPLACE INTO issuer_fundamental_values
                (secid,metric,reporting_standard,period_start,period_end,publication_date,
                 available_from,source,document,page_table,raw_value,normalized_value,unit,
                 validation_status,revision,document_id,source_hash,structured_field,
                 parser_version,issuer,raw_unit)
                VALUES ('MOEX',?,'IFRS',NULL,?,?,?,?,?,?,?,?,?,'validated','original',?,?,?,?,
                        'MOEX',?)""",
                [metric, spec["period"], spec["published"], available, spec["url"], spec["url"],
                 f"HTML:{label}", value, value, unit, document_id, digest, label, VERSION, unit],
            )
            inserted += 0 if before else 1
        con.execute(
            "UPDATE actual_document_inventory SET status='parsed_validated' WHERE document_id=?",
            [document_id],
        )
    update_coverage(con, "MOEX")
    return {"documents_downloaded": documents, "new_validated_values": inserted, "manual_review": review}


def sync_document_inventory(con) -> dict:
    ensure_schema(con)
    rows = con.execute(
        """SELECT document_id,issuer,document_type,reporting_standard,reporting_period,
        publication_date,document_url,mime_type,source_hash,parser_status,validation_status
        FROM issuer_fundamental_documents WHERE source_hash IS NOT NULL"""
    ).fetchall()
    for row in rows:
        doc, issuer, kind, standard, period, published, url, mime, digest, parser, status = row
        existing = con.execute(
            "SELECT size_bytes,local_path FROM actual_document_inventory WHERE document_id=?", [doc]
        ).fetchone()
        size, local = existing if existing else (None, None)
        con.execute(
            """INSERT OR REPLACE INTO actual_document_inventory VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
            [doc, issuer, kind, standard, period, published, url, mime, digest, size, parser,
             status, local],
        )
    return {"documents": len(rows)}


def backfill_historical_fundamentals(con) -> dict:
    ensure_schema(con)
    before = _counts(con)
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"fundamentals:{started.isoformat()}".encode()).hexdigest()[:16]
    sber = migrate_sber(con)
    moex = import_moex_annual_history(con)
    inventory = sync_document_inventory(con)
    after = _counts(con)
    new_rows = moex["new_validated_values"]
    con.execute(
        """INSERT INTO actual_backfill_checkpoints VALUES
        ('fundamentals',?,?,current_timestamp,?,?,?,'completed')""",
        [run_id, started, json.dumps(before, default=str), json.dumps(after, default=str), new_rows],
    )
    return {"run_id": run_id, "before": before, "after": after, "SBER": sber,
            "MOEX": moex, "inventory": inventory, "new_rows": new_rows}


def _block_rows(block: dict) -> list[dict]:
    return [dict(zip(block.get("columns", []), row, strict=True)) for row in block.get("data", [])]


def backfill_universe_pilot(con, client: MoexClient | None = None, limit: int = 100) -> dict:
    """Download a reproducible pilot of inactive equities; membership means traded, not index member."""
    ensure_schema(con)
    client = client or MoexClient()
    candidates = con.execute(
        """SELECT secid,primary_board FROM historical_equity_universe
        WHERE NOT is_traded AND instrument_type IN ('common_share','preferred_share')
          AND (regnumber IS NOT NULL OR isin LIKE 'RU%')
        ORDER BY coalesce(last_trade,DATE '1900-01-01') DESC,secid LIMIT ?""",
        [limit],
    ).fetchall()
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"universe:{started.isoformat()}".encode()).hexdigest()[:16]
    before = con.execute("SELECT count(*) FROM tradable_on_date_universe").fetchone()[0]
    requests_count = received = errors = 0
    detail = []
    for secid, primary in candidates:
        try:
            boards = [row for row in client.discover_history(secid) if row["market"] == "shares"]
            requests_count += 1
            if not boards:
                detail.append({"secid": secid, "status": "no_share_history_board"})
                continue
            board = next((row for row in boards if row["boardid"] == primary), None)
            board = board or min(boards, key=lambda row: row["history_from"])
            board = {**board, "board": board["boardid"]}
            first = str(board["history_from"])
            last = str(board.get("history_till") or date.today())
            security_rows = 0
            for payload, _, source in client.history_pages(board | {"secid": secid}, first, last):
                requests_count += 1
                normalized = client.normalize_history(payload, secid, board["board"], source)
                for row in normalized:
                    if not row["trade_date"] or row["close"] is None:
                        continue
                    con.execute(
                        """INSERT OR IGNORE INTO tradable_on_date_universe VALUES
                        (?,?,?,?,?,?,true,?,current_timestamp)""",
                        [row["trade_date"], secid, board["board"], row["close"], row["volume"],
                         row["value"], row["source"]],
                    )
                    security_rows += 1
            received += security_rows
            bounds = con.execute(
                "SELECT min(trade_date),max(trade_date) FROM tradable_on_date_universe WHERE secid=?",
                [secid],
            ).fetchone()
            con.execute(
                "UPDATE historical_equity_universe SET first_trade=?,last_trade=? WHERE secid=?",
                [bounds[0], bounds[1], secid],
            )
            detail.append({"secid": secid, "rows": security_rows, "board": board["board"]})
        except Exception as exc:
            errors += 1
            detail.append({"secid": secid, "status": "error", "error": str(exc)})
    after = con.execute("SELECT count(*) FROM tradable_on_date_universe").fetchone()[0]
    elapsed = (datetime.now(UTC) - started).total_seconds()
    disk = database_path().stat().st_size
    con.execute(
        """INSERT INTO universe_pilot_runs VALUES
        (?,?,?,?,?,?,?,?,?,?,current_timestamp,?)""",
        [run_id, len(candidates), len(candidates), requests_count, received, after - before,
         errors, elapsed, disk, started, json.dumps(detail)],
    )
    return {"run_id": run_id, "securities": len(candidates), "inactive": len(candidates),
            "requests": requests_count, "rows_received": received, "rows_inserted": after - before,
            "errors": errors, "elapsed_seconds": elapsed, "disk_bytes": disk}


def backfill_official_fx(con, date_from: date = date(1992, 1, 1), date_to: date | None = None) -> dict:
    ensure_schema(con)
    date_to = date_to or date.today()
    result = {}
    for code, (series_id, _) in cbr.CURRENCY_NAMES.items():
        before = con.execute(
            "SELECT count(*),min(observation_date),max(observation_date) FROM macro_observations WHERE series_id=?",
            [series_id],
        ).fetchone()
        observations = cbr.download_currency(code, date_from, date_to)
        inserted = upsert_observations(con, observations)
        after = con.execute(
            "SELECT count(*),min(observation_date),max(observation_date) FROM macro_observations WHERE series_id=?",
            [series_id],
        ).fetchone()
        result[series_id] = {"downloaded": len(observations), "inserted": inserted,
                             "before": before, "after": after, "source": observations[0].source if observations else None}
    return result


PORTFOLIO_SECIDS = ("X5", "SBER", "SBERP", "LKOH", "LSNG", "LSNGP", "MTSS", "TRNFP", "TATN", "TATNP", "PHOR", "MOEX")


def backfill_portfolio_dividends(con, client: MoexClient | None = None) -> dict:
    client = client or MoexClient()
    result = {}
    for secid in PORTFOLIO_SECIDS:
        before = con.execute("SELECT count(*) FROM dividends WHERE canonical_secid=?", [secid]).fetchone()[0]
        values = client.dividends(secid)
        insert_dividends(con, values)
        after = con.execute("SELECT count(*) FROM dividends WHERE canonical_secid=?", [secid]).fetchone()[0]
        result[secid] = {"downloaded": len(values), "inserted": after - before}
    return result


def dividend_pair_consistency(frame, ordinary: str, preferred: str) -> dict:
    left = frame[frame["secid"] == ordinary][["record_date", "dps"]]
    right = frame[frame["secid"] == preferred][["record_date", "dps"]]
    joined = left.merge(right, on="record_date", how="outer", suffixes=("_ordinary", "_preferred"))
    mismatches = joined[
        joined.dps_ordinary.isna()
        | joined.dps_preferred.isna()
        | (joined.dps_ordinary != joined.dps_preferred)
    ]
    return {"dates": len(joined), "mismatches": len(mismatches), "consistent": mismatches.empty}


def resolve_external_sources(con) -> dict:
    """Record concrete official resolutions; Brent is a futures proxy, not spot oil."""
    rows = [
        ("cbr_usd_rub", "FX", "Bank of Russia", "https://www.cbr.ru/scripts/XML_dynamic.asp", "official public XML interface", "free", "effective date 00:00 Moscow", "validated", "loaded", "official rate; not market close"),
        ("cbr_eur_rub", "FX", "Bank of Russia", "https://www.cbr.ru/scripts/XML_dynamic.asp", "official public XML interface", "free", "effective date 00:00 Moscow", "validated", "loaded", "official rate; not market close"),
        ("cbr_cny_rub", "FX", "Bank of Russia", "https://www.cbr.ru/scripts/XML_dynamic.asp", "official public XML interface", "free", "effective date 00:00 Moscow", "validated", "loaded", "official rate; not market close"),
        ("brent_moex_futures", "oil", "Moscow Exchange", "https://iss.moex.com/iss/history/engines/futures/markets/forts/securities", "official ISS public interface; redistribution terms require separate verification", "free", "MOEX trading date", "proxy_only", "resolved_not_loaded", "USD per barrel futures; never labelled spot Brent or Urals"),
        ("urals", "oil", "none validated", "", "unresolved", "paid/restricted", "unknown", "missing", "requires_paid_data", "no safe free PIT daily source proven"),
        ("fertilizer_proxy", "commodity", "none validated", "", "unresolved", "paid/restricted", "unknown", "missing", "requires_paid_data", "no safe free PIT daily source proven"),
    ]
    con.executemany(
        """INSERT OR REPLACE INTO external_factor_catalog VALUES
        (?,?,?,?,?,?,?,?,?,?,current_timestamp)""", rows
    )
    return {"resolved": 4, "paid_restricted": 2, "brent_status": "official_moex_futures_proxy_not_spot"}


def backfill_futures_specifications(con, client: MoexClient | None = None) -> dict:
    ensure_schema(con)
    client = client or MoexClient()
    contracts = con.execute("SELECT secid,expiration FROM expired_sber_futures ORDER BY secid").fetchall()
    inserted = validated = errors = 0
    for secid, known_expiration in contracts:
        try:
            path = f"engines/futures/markets/forts/securities/{secid}.json"
            payload = client.get_json(path, {"iss.meta": "off", "iss.only": "securities"})
            client.save_raw(secid, "specification", 0, payload)
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            digest = hashlib.sha256(raw).hexdigest()
            values = _block_rows(payload.get("securities", {}))
            if not values:
                continue
            row = values[0]
            lot = row.get("LOTSIZE") or row.get("LOTVOLUME")
            step = row.get("MINSTEP")
            step_value = row.get("STEPPRICE")
            expiration = row.get("LASTTRADEDATE") or known_expiration
            underlying = row.get("ASSETCODE") or row.get("ASSET")
            currency = row.get("CURRENCYID") or "RUB"
            units_valid = all(value not in (None, "", 0) for value in (lot, step, step_value, expiration, underlying, currency))
            before = con.execute("SELECT count(*) FROM futures_spec_documents WHERE secid=?", [secid]).fetchone()[0]
            con.execute(
                """INSERT OR REPLACE INTO futures_spec_documents VALUES
                (?,?,NULL,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
                [secid, date.today(), underlying, lot, lot, step, step_value, currency, expiration,
                 f"{client.base_url}/{path}", digest, units_valid],
            )
            inserted += 0 if before else 1
            validated += int(units_valid)
        except Exception:
            errors += 1
    return {"contracts": len(contracts), "inserted": inserted, "validated": validated,
            "errors": errors, "basis_enabled": False,
            "reason": "spot/futures scale and underlying-unit equivalence still require validation"}


def backfill_external_and_contracts(con) -> dict:
    started = datetime.now(UTC)
    before = {
        "fx": con.execute("SELECT count(*) FROM macro_observations WHERE series_id IN ('cbr_usd_rub','cbr_eur_rub','cbr_cny_rub')").fetchone()[0],
        "dividends": con.execute("SELECT count(*) FROM dividends").fetchone()[0],
        "specs": con.execute("SELECT count(*) FROM futures_spec_documents").fetchone()[0] if _table_present(con, "futures_spec_documents") else 0,
    }
    ensure_schema(con)
    result = {"fx": backfill_official_fx(con), "dividends": backfill_portfolio_dividends(con),
              "futures": backfill_futures_specifications(con),
              "source_resolution": resolve_external_sources(con)}
    after = {
        "fx": con.execute("SELECT count(*) FROM macro_observations WHERE series_id IN ('cbr_usd_rub','cbr_eur_rub','cbr_cny_rub')").fetchone()[0],
        "dividends": con.execute("SELECT count(*) FROM dividends").fetchone()[0],
        "specs": con.execute("SELECT count(*) FROM futures_spec_documents").fetchone()[0],
    }
    result.update({"before": before, "after": after, "new_rows": sum(after[k] - before[k] for k in after),
                   "started_at": started})
    return result


def _table_present(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [name]).fetchone()[0])
