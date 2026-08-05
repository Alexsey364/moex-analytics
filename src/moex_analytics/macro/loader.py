"""Orchestration for reproducible macro discovery and downloads."""

from __future__ import annotations

import json
from datetime import date, datetime

import duckdb

from .repository import upsert_observations, upsert_series
from .sources import cbr, external, moex, rosstat


def discover(con: duckdb.DuckDBPyConnection) -> int:
    return upsert_series(
        con, [*cbr.definitions(), *moex.definitions(), *rosstat.definitions(), *external.definitions()]
    )


def download(con: duckdb.DuckDBPyConnection, date_from: date, date_to: date) -> dict[str, int]:
    """Load only sources with verified machine-readable availability rules."""
    started = datetime.now()
    received = inserted = 0
    details: dict[str, int] = {}
    try:
        for code, (series_id, _) in cbr.CURRENCY_NAMES.items():
            rows = cbr.download_currency(code, date_from, date_to)
            details[series_id] = upsert_observations(con, rows)
            received += len(rows)
            inserted += details[series_id]
        rate_rows = cbr.download_rates(max(date_from, date(2013, 9, 17)), date_to)
        for series_id in ("cbr_key_rate", "cbr_ruonia"):
            selected = [row for row in rate_rows if row.series_id == series_id]
            details[series_id] = upsert_observations(con, selected)
            received += len(selected)
            inserted += details[series_id]
        for row in [item for item in rate_rows if item.series_id == "cbr_key_rate"]:
            con.execute(
                """INSERT INTO event_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET actual_release_at=excluded.actual_release_at,
                status=excluded.status,loaded_at=excluded.loaded_at""",
                [
                    f"cbr-rate-{row.observation_date}",
                    "key_rate_decision",
                    "RU",
                    None,
                    row.observation_date,
                    row.available_from,
                    row.source,
                    "released",
                    "high",
                    datetime.now(),
                    "Official CBR rate change; no expectation surprise calculated",
                ],
            )
        dividends = con.execute("""SELECT canonical_secid,registry_close_date,source
            FROM dividends""").fetchall()
        for secid, event_date, source in dividends:
            con.execute(
                """INSERT INTO event_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id) DO NOTHING""",
                [
                    f"dividend-{secid}-{event_date}",
                    "dividend_registry_close",
                    "RU",
                    secid,
                    event_date,
                    None,
                    source,
                    "known",
                    "medium",
                    datetime.now(),
                    "Registry close date from MOEX ISS",
                ],
            )
        for series_id in moex.INSTRUMENTS:
            rows = moex.download(series_id, str(date_from), str(date_to))
            details[series_id] = upsert_observations(con, rows)
            received += len(rows)
            inserted += details[series_id]
    except Exception as exc:
        con.execute(
            """INSERT INTO macro_load_log(run_type,started_at,finished_at,
            rows_received,rows_inserted,status,error_message,details_json)
            VALUES ('download',?,current_timestamp,?,?,'failed',?,?)""",
            [started, received, inserted, str(exc), json.dumps(details)],
        )
        raise
    con.execute(
        """INSERT INTO macro_load_log(run_type,started_at,finished_at,
        rows_received,rows_inserted,status,details_json)
        VALUES ('download',?,current_timestamp,?,?,'success',?)""",
        [started, received, inserted, json.dumps(details)],
    )
    return details
