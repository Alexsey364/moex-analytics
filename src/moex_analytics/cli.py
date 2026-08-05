"""Command-line interface for MOEX ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .analogues import calculate_all as calculate_analogues
from .calendar import rebuild_calendar
from .canonical import build_canonical
from .config import load_instruments, load_segments, load_settings
from .data_quality import record_issues
from .database import (
    connection,
    finish_load,
    init_database,
    insert_daily_prices,
    insert_dividends,
    latest_date,
    row_counts,
    start_load,
    upsert_instruments,
    upsert_segments,
)
from .features import calculate_all as calculate_features
from .forward_returns import calculate_all as calculate_forward_returns
from .market_regime import calculate_all as calculate_regimes
from .moex_client import MoexClient
from .returns import calculate_all
from .scoring import calculate_all as calculate_scores


def instrument_by_id(secid: str) -> dict[str, Any]:
    try:
        return next(item for item in load_instruments() if item["secid"] == secid)
    except StopIteration as exc:
        raise SystemExit(f"Unknown ticker: {secid}") from exc


def resolve_start(con: Any, instrument: dict[str, Any], requested: str | None) -> date:
    if requested:
        return date.fromisoformat(requested)
    current = latest_date(con, instrument["secid"], instrument["board"])
    return current + timedelta(days=1) if current else date.fromisoformat(str(instrument["history_from"]))


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


def download_segment(segment: dict[str, Any], from_date: str | None, to_date: str | None):
    client = MoexClient()
    init_database()
    with connection() as con:
        segment_start = date.fromisoformat(str(segment["date_from"]))
        start = max(date.fromisoformat(from_date), segment_start) if from_date else segment_start
        end_limit = date.fromisoformat(str(segment["date_to"]))
        end = min(date.fromisoformat(to_date) if to_date else date.today(), end_limit)
        if start > end:
            return 0, 0
        load_id = start_load(con, segment["source_secid"], start, end)
        rows = []
        try:
            for payload, page, source in client.history_pages(segment, str(start), str(end)):
                batch = client.normalize_history(payload, segment["source_secid"], segment["board"], source)
                rows.extend(batch)
                print(f"{segment['canonical_secid']} {segment['board']} page {page}: {len(batch)}")
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
    sub.add_parser("discover-history")
    sub.add_parser("init-db")
    for name in ("download", "download-all"):
        command = sub.add_parser(name)
        if name == "download":
            command.add_argument("--ticker", required=True)
        command.add_argument("--from-date")
        command.add_argument("--to-date")
    sub.add_parser("quality-check")
    history = sub.add_parser("download-history")
    history.add_argument("--ticker", required=True)
    history.add_argument("--from-date")
    history.add_argument("--to-date")
    history_all = sub.add_parser("download-history-all")
    history_all.add_argument("--from-date")
    history_all.add_argument("--to-date")
    sub.add_parser("build-canonical")
    sub.add_parser("download-dividends")
    sub.add_parser("calculate-returns")
    sub.add_parser("status")
    sub.add_parser("dashboard")
    sub.add_parser("calculate-features")
    sub.add_parser("calculate-regimes")
    sub.add_parser("calculate-forward-returns")
    sub.add_parser("calculate-analytics")
    sub.add_parser("analytics-status")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "discover":
        client = MoexClient()
        for configured in load_instruments():
            print(client.discover(configured["secid"]))
    elif args.command == "discover-history":
        client = MoexClient()
        for item in load_instruments():
            print(item["secid"], client.discover_history(item["secid"]))
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
    elif args.command in {"download-history", "download-history-all"}:
        segments = load_segments()
        if args.command == "download-history":
            segments = [s for s in segments if s["canonical_secid"] == args.ticker]
        init_database()
        with connection() as con:
            upsert_segments(con, segments)
        for segment in segments:
            print(
                segment["canonical_secid"],
                segment["board"],
                download_segment(segment, args.from_date, args.to_date),
            )
    elif args.command == "build-canonical":
        init_database()
        with connection() as con:
            upsert_segments(con, load_segments())
            print({"canonical_rows": build_canonical(con), "calendar_rows": rebuild_calendar(con)})
    elif args.command == "download-dividends":
        client = MoexClient()
        init_database()
        with connection() as con:
            for item in load_instruments():
                rows = client.dividends(item["secid"])
                print(item["secid"], {"received": len(rows), "inserted": insert_dividends(con, rows)})
    elif args.command == "calculate-returns":
        init_database()
        with connection() as con:
            print({"return_rows": calculate_all(con)})
    elif args.command == "status":
        with connection() as con:
            details = con.execute(
                """SELECT i.secid,
                   (SELECT min(trade_date) FROM daily_prices WHERE secid=i.secid),
                   (SELECT max(trade_date) FROM daily_prices WHERE secid=i.secid),
                   (SELECT count(*) FROM daily_prices WHERE secid=i.secid),
                   (SELECT count(*) FROM canonical_daily_prices WHERE canonical_secid=i.secid),
                   (SELECT string_agg(DISTINCT board,',' ORDER BY board)
                      FROM instrument_history_segments WHERE canonical_secid=i.secid),
                   (SELECT count(*) FROM dividends WHERE canonical_secid=i.secid),
                   (SELECT count(*) FROM data_quality_issues WHERE secid=i.secid),
                   (SELECT max(finished_at) FROM load_log WHERE secid=i.secid),
                   (SELECT max(calculated_at) FROM daily_returns WHERE canonical_secid=i.secid)
                   FROM instruments i ORDER BY i.secid"""
            ).fetchall()
            print({"rows": row_counts(con), "details": details})
    elif args.command in {
        "calculate-features",
        "calculate-regimes",
        "calculate-forward-returns",
        "calculate-analytics",
        "analytics-status",
    }:
        init_database()
        with connection() as con:
            if args.command == "analytics-status":
                print(
                    con.execute("""SELECT run_type,calculation_version,finished_at,
                    duration_seconds,rows_written,status FROM analytics_runs
                    ORDER BY id DESC LIMIT 10""").fetchall()
                )
                return
            actions = {
                "calculate-features": [("features", calculate_features)],
                "calculate-regimes": [("regimes", calculate_regimes)],
                "calculate-forward-returns": [("forward_returns", calculate_forward_returns)],
                "calculate-analytics": [
                    ("features", calculate_features),
                    ("regimes", calculate_regimes),
                    ("forward_returns", calculate_forward_returns),
                    ("analogues", calculate_analogues),
                    ("scores", calculate_scores),
                ],
            }[args.command]
            started = time.perf_counter()
            rows = {}
            settings = load_settings()["analytics"]
            config_hash = hashlib.sha256(
                json.dumps(settings, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            if args.command == "calculate-analytics":
                source_state = con.execute(
                    """SELECT count(*), max(trade_date), max(loaded_at)
                    FROM canonical_daily_prices"""
                ).fetchone()
                latest_run = con.execute(
                    """SELECT finished_at FROM analytics_runs
                    WHERE run_type='calculate-analytics' AND calculation_version=?
                      AND config_hash=? AND status='success'
                    ORDER BY id DESC LIMIT 1""",
                    [settings["calculation_version"], config_hash],
                ).fetchone()
                feature_state = con.execute(
                    """SELECT count(*), max(trade_date) FROM daily_features
                    WHERE calculation_version=?""",
                    [settings["calculation_version"]],
                ).fetchone()
                source_is_unchanged = (
                    latest_run
                    and source_state[0] == feature_state[0]
                    and source_state[1] == feature_state[1]
                    and (source_state[2] is None or latest_run[0] >= source_state[2])
                )
                if source_is_unchanged:
                    duration = time.perf_counter() - started
                    details = {
                        "mode": "incremental-no-change",
                        "source_rows": source_state[0],
                        "source_max_date": str(source_state[1]),
                    }
                    con.execute(
                        """INSERT INTO analytics_runs(run_type,calculation_version,config_hash,
                        started_at,finished_at,duration_seconds,rows_written,status,details_json)
                        VALUES (?, ?, ?, current_timestamp, current_timestamp, ?, 0, 'success', ?)""",
                        [
                            args.command,
                            settings["calculation_version"],
                            config_hash,
                            duration,
                            json.dumps(details),
                        ],
                    )
                    print({"duration_seconds": duration, "rows": {}, **details})
                    return
            for name, action in actions:
                rows[name] = action(con)
                print(name, rows[name])
            duration = time.perf_counter() - started
            con.execute(
                """INSERT INTO analytics_runs(run_type,calculation_version,config_hash,
                started_at,finished_at,duration_seconds,rows_written,status,details_json)
                VALUES (?, ?, ?, current_timestamp, current_timestamp, ?, ?, 'success', ?)""",
                [
                    args.command,
                    settings["calculation_version"],
                    config_hash,
                    duration,
                    sum(rows.values()),
                    json.dumps(rows),
                ],
            )
            print({"duration_seconds": duration, "rows": rows, "config_hash": config_hash})
    elif args.command == "dashboard":
        app = Path(__file__).parent / "dashboard" / "app.py"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app),
                "--server.address",
                "localhost",
                "--server.port",
                "8501",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
