"""Controlled imports with audit metadata."""

from pathlib import Path

import duckdb

from .parser import parse_file
from .quality import inspect
from .repository import upsert_observations
from .sources.cbr import discover as discover_cbr
from .sources.sber import discover as discover_sber


def discover() -> list[dict]:
    return discover_sber() + discover_cbr()


def import_report(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    rows = parse_file(path)
    issues = inspect(rows)
    errors = [x for x in issues if x["severity"] == "error"]
    if errors:
        raise ValueError(f"Import rejected by quality checks: {errors}")
    return {"received": len(rows), "inserted": upsert_observations(con, rows), "issues": issues}


def download() -> dict:
    return {
        "status": "controlled-import-required",
        "sources": discover(),
        "reason": (
            "Official report layouts are not a stable machine API; use import-sber-report "
            "with verified CSV/XLSX."
        ),
    }
