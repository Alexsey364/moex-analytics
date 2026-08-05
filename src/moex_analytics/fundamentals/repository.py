"""Idempotent persistence and point-in-time reads."""

from collections.abc import Iterable
from datetime import date, datetime

import duckdb

from .models import FundamentalObservation


def upsert_observations(con: duckdb.DuckDBPyConnection, rows: Iterable[FundamentalObservation]) -> int:
    before = con.execute("SELECT count(*) FROM fundamental_observations").fetchone()[0]
    now = datetime.now()
    for row in rows:
        con.execute(
            """INSERT INTO fundamental_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(secid,metric_id,period_end,accounting_standard,revision_id) DO UPDATE SET
        publication_date=excluded.publication_date,available_from=excluded.available_from,value=excluded.value,
        unit=excluded.unit,source=excluded.source,source_document=excluded.source_document,loaded_at=excluded.loaded_at""",
            [
                row.secid,
                row.metric_id,
                row.period_start,
                row.period_end,
                row.report_type,
                row.accounting_standard,
                row.publication_date,
                row.available_from,
                row.value,
                row.unit,
                row.source,
                row.source_document,
                row.revision_id,
                now,
            ],
        )
        rid = ":".join((row.secid, row.accounting_standard, str(row.period_end), row.revision_id))
        con.execute(
            """INSERT INTO fundamental_releases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id) DO UPDATE SET publication_date=excluded.publication_date,
        available_from=excluded.available_from,source_document=excluded.source_document,loaded_at=excluded.loaded_at""",
            [
                rid,
                row.secid,
                row.period_start,
                row.period_end,
                row.report_type,
                row.accounting_standard,
                row.publication_date,
                row.available_from,
                row.source,
                row.source_document,
                row.revision_id,
                "controlled_import",
                now,
            ],
        )
    return int(con.execute("SELECT count(*) FROM fundamental_observations").fetchone()[0] - before)


def available_as_of(con: duckdb.DuckDBPyConnection, cutoff: date, standard: str | None = None):
    return con.execute(
        """SELECT * FROM fundamental_observations WHERE secid='SBER'
    AND CAST(available_from AS DATE)<=? AND (? IS NULL OR accounting_standard=?)
    QUALIFY row_number() OVER(PARTITION BY metric_id,period_end,accounting_standard
    ORDER BY available_from DESC,revision_id DESC)=1 ORDER BY metric_id,period_end""",
        [cutoff, standard, standard],
    ).df()
