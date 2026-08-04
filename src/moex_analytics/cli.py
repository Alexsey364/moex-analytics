"""Command-line interface for MOEX ingestion."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Any

from .config import load_instruments
from .data_quality import record_issues
from .database import (
    connection,
    finish_load,
    init_database,
    insert_daily_prices,
    latest_date,
    row_counts,
    start_load,
    upsert_instruments,
)
from .moex_client import MoexClient


def instrument_by_id(secid: str) -> dict[str, Any]:
    try:
        return next(item for item in load_instruments() if item["secid"] == secid)
    except StopIteration as exc:
        raise SystemExit(f"Unknown ticker: {secid}") from exc


def resolve_start(con: Any, instrument: dict[str, Any], requested: str | None) -> date:
    if requested:
        return date.fromisoformat(requested)
    current = latest_date(con, instrument["secid"], instrument["board"])
    return (
        current + timedelta(days=1)
        if current
        else date.fromisoformat(str(instrument["history_from"]))
    )


def download_one(secid: str, from_date: str | None, to_date: str | None) -> tuple[int, int]:
    instrument = instrument_by_id(secid)
    client = MoexClient()
    init_database()
    with connection() as con:
        upsert_instruments(con, load_instruments())
        start = resolve_start(con, instrument, from_date)
        end = date.fromisoformat(to_date) if to_date else date.today()
        if start > end:
            return 0, 0
        load_id = start_load(con, secid, start, end)
        rows: list[dict[str, Any]] = []
        try:
            for payload, _, source in client.history_pages(instrument, str(start), str(end)):
                rows.extend(client.normalize_history(payload, secid, instrument["board"], source))
            inserted = insert_daily_prices(con, rows)
            finish_load(con, load_id, len(rows), inserted, "success")
            return len(rows), inserted
        except Exception as exc:
            finish_load(con, load_id, len(rows), 0, "failed", str(exc))
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moex-analytics")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    sub.add_parser("init-db")
    for name in ("download", "download-all"):
        command = sub.add_parser(name)
        if name == "download":
            command.add_argument("--ticker", required=True)
        command.add_argument("--from-date")
        command.add_argument("--to-date")
    sub.add_parser("quality-check")
    sub.add_parser("status")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "discover":
        client = MoexClient()
        for configured in load_instruments():
            print(client.discover(configured["secid"]))
    elif args.command == "init-db":
        init_database()
        with connection() as con:
            upsert_instruments(con, load_instruments())
        print("Database initialized")
    elif args.command == "download":
        print(args.ticker, download_one(args.ticker, args.from_date, args.to_date))
    elif args.command == "download-all":
        for item in load_instruments():
            print(item["secid"], download_one(item["secid"], args.from_date, args.to_date))
    elif args.command == "quality-check":
        with connection() as con:
            print({"issues": record_issues(con)})
    elif args.command == "status":
        with connection() as con:
            print({"rows": row_counts(con)})


if __name__ == "__main__":
    main()
