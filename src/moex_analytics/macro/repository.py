"""Idempotent DuckDB persistence for macro records."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import duckdb

from .models import Observation, SeriesDefinition


def upsert_series(con: duckdb.DuckDBPyConnection, items: Iterable[SeriesDefinition]) -> int:
    count = 0
    for item in items:
        con.execute(
            """INSERT INTO macro_series VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(series_id) DO UPDATE SET name=excluded.name,unit=excluded.unit,
            frequency=excluded.frequency,source=excluded.source,endpoint=excluded.endpoint,
            start_date=excluded.start_date,publication_rule=excluded.publication_rule,
            revision_rule=excluded.revision_rule,is_point_in_time_safe=excluded.is_point_in_time_safe,
            notes=excluded.notes,updated_at=excluded.updated_at""",
            [*item.__dict__.values(), datetime.now()],
        )
        count += 1
    return count


def upsert_observations(con: duckdb.DuckDBPyConnection, rows: Iterable[Observation]) -> int:
    before = con.execute("SELECT count(*) FROM macro_observations").fetchone()[0]
    now = datetime.now()
    for row in rows:
        values = [*row.__dict__.values(), now]
        con.execute(
            """INSERT INTO macro_observations
            (series_id,observation_date,release_date,available_from,value,vintage,source,loaded_at)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(series_id,observation_date,vintage) DO UPDATE SET
            release_date=excluded.release_date,available_from=excluded.available_from,
            value=excluded.value,loaded_at=excluded.loaded_at,source=excluded.source""",
            values,
        )
        con.execute(
            """INSERT INTO macro_releases VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(series_id,observation_date,vintage) DO UPDATE SET
            release_date=excluded.release_date,available_from=excluded.available_from,
            loaded_at=excluded.loaded_at""",
            [
                row.series_id,
                row.observation_date,
                row.release_date,
                row.available_from,
                row.vintage,
                row.source,
                now,
            ],
        )
    after = con.execute("SELECT count(*) FROM macro_observations").fetchone()[0]
    return int(after - before)


def available_observations(con: duckdb.DuckDBPyConnection, cutoff) -> list[tuple]:
    """Return the latest vintage actually known by a cutoff, never by observation date alone."""
    return con.execute(
        """SELECT series_id,observation_date,release_date,available_from,value,vintage
        FROM macro_observations WHERE available_from<=?
        QUALIFY row_number() OVER(PARTITION BY series_id,observation_date
        ORDER BY available_from DESC,vintage DESC)=1 ORDER BY series_id,observation_date""",
        [cutoff],
    ).fetchall()
