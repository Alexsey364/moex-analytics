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
    database_path,
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
from .fundamentals.backtest import run as backtest_sber_fundamentals
from .fundamentals.confidence import calculate_current as calculate_sber_confidence
from .fundamentals.derived import build as build_sber_fundamental_history
from .fundamentals.documents import discover as discover_sber_reports
from .fundamentals.documents import download as download_sber_reports
from .fundamentals.fact_scenarios import calculate as calculate_fact_sber_valuation
from .fundamentals.history import import_validated as import_sber_validated
from .fundamentals.history import parse_downloaded as parse_sber_reports
from .fundamentals.history import validate as validate_sber_reports
from .fundamentals.loader import discover as discover_sber_fundamentals
from .fundamentals.loader import download as download_sber_fundamentals
from .fundamentals.loader import import_report as import_sber_report
from .fundamentals.pipeline import status as sber_history_status
from .fundamentals.pipeline import update as update_sber_fundamentals
from .fundamentals.point_in_time import build_snapshots as build_fundamental_snapshots
from .fundamentals.scenarios import calculate_all as calculate_sber_valuation
from .fundamentals.validation import validate_all as validate_sber_valuation
from .macro.audit import run_audit as run_macro_audit
from .macro.experiment import calculate_forecasts
from .macro.experiment import validate_all as validate_macro_models
from .macro.feature_store import calculate_all as calculate_macro_features
from .macro.loader import discover as discover_macro
from .macro.loader import download as download_macro
from .market_regime import calculate_all as calculate_regimes
from .moex_client import MoexClient
from .predictive_foundation.core import (
    ablate_blocks as ablate_predictive_blocks,
)
from .predictive_foundation.core import (
    audit_coverage as audit_predictive_data,
)
from .predictive_foundation.core import (
    build_breadth as build_market_breadth,
)
from .predictive_foundation.core import (
    build_catalog as discover_predictive_sources,
)
from .predictive_foundation.core import (
    build_lead_lag_diagnostics,
    download_market_universe,
)
from .predictive_foundation.core import (
    build_relative_state as build_sber_relative_state,
)
from .predictive_foundation.core import (
    cross_market_status as download_cross_market_data,
)
from .predictive_foundation.core import (
    derivative_features_status as build_derivative_features,
)
from .predictive_foundation.core import (
    discover_derivatives as download_derivatives,
)
from .predictive_foundation.core import (
    index_history_status as download_index_history,
)
from .predictive_foundation.core import (
    rates_market_status as download_rates_market,
)
from .predictive_foundation.core import (
    status as predictive_data_status,
)
from .predictive_foundation.core import (
    update as update_predictive_foundation,
)
from .returns import calculate_all
from .sber_decision.engine import (
    backtest as backtest_sber_decision,
)
from .sber_decision.engine import (
    build_daily_state,
    calculate_dividend_outlook,
    calculate_ensemble,
)
from .sber_decision.engine import (
    calculate as calculate_sber_decision,
)
from .sber_intelligence.discovery import discover as discover_sber_information
from .sber_intelligence.expectations import calculate_all as calculate_sber_expectations
from .sber_intelligence.impact import build_impacts as calculate_sber_impacts
from .sber_intelligence.loader import update as update_sber_information
from .sber_intelligence.quality import run as validate_sber_events
from .sber_intelligence.repository import (
    build_live_state as build_sber_information_state,
)
from .sber_intelligence.repository import (
    build_studies as build_sber_event_studies,
)
from .sber_intelligence.repository import (
    calculate_reactions as calculate_sber_event_reactions,
)
from .sber_intelligence.repository import (
    status as sber_information_status,
)
from .sber_operational.core import (
    audit_zones,
    calculate_nowcast,
    calculate_operating_state,
    calculate_scorecard,
    import_validated_fundamentals,
    save_live_decision,
    update_outcomes,
)
from .sber_operational.core import (
    discover as discover_sber_operational,
)
from .sber_operational.core import (
    run_daily as run_sber_daily,
)
from .sber_operational.core import (
    status as sber_live_status,
)
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
    sub.add_parser("discover-macro")
    for name in ("download-macro", "update-macro"):
        command = sub.add_parser(name)
        command.add_argument("--from-date")
        command.add_argument("--to-date")
    sub.add_parser("calculate-macro-features")
    sub.add_parser("validate-macro-models")
    sub.add_parser("calculate-forecast-ranges")
    sub.add_parser("macro-status")
    sub.add_parser("audit-macro-model")
    sub.add_parser("discover-sber-fundamentals")
    sub.add_parser("download-sber-fundamentals")
    imported = sub.add_parser("import-sber-report")
    imported.add_argument("path", type=Path)
    sub.add_parser("build-fundamental-snapshots")
    sub.add_parser("calculate-sber-valuation")
    sub.add_parser("validate-sber-valuation")
    sub.add_parser("sber-fundamental-status")
    for name in (
        "discover-sber-reports",
        "download-sber-reports",
        "parse-sber-reports",
        "export-sber-review-template",
        "import-sber-reviewed-data",
        "validate-sber-reports",
        "build-sber-fundamental-history",
        "backtest-sber-fundamentals",
        "sber-fundamental-history-status",
        "update-sber-fundamentals",
    ):
        command = sub.add_parser(name)
        if name == "import-sber-reviewed-data":
            command.add_argument("path", type=Path, nargs="?")
    for name in (
        "update-sber-expanded-data",
        "build-sber-daily-state",
        "calculate-sber-dividend-outlook",
        "calculate-sber-decision",
        "backtest-sber-decision",
        "sber-decision-status",
        "update-sber-decision",
    ):
        sub.add_parser(name)
    for name in (
        "discover-sber-information",
        "update-sber-information",
        "validate-sber-events",
        "calculate-sber-event-reactions",
        "calculate-sber-expectations",
        "build-sber-information-state",
        "recalculate-sber-after-events",
        "sber-information-status",
        "update-sber-intelligence",
    ):
        sub.add_parser(name)
    for name in (
        "discover-sber-operational-data",
        "update-sber-operational-data",
        "calculate-sber-nowcast",
        "calculate-sber-operating-state",
        "audit-sber-price-zones",
        "save-sber-live-decision",
        "update-sber-live-outcomes",
        "calculate-sber-live-scorecard",
        "sber-live-status",
        "run-sber-daily",
    ):
        sub.add_parser(name)
    for name in (
        "discover-predictive-sources",
        "download-market-universe",
        "download-index-history",
        "download-derivatives",
        "download-rates-market",
        "download-cross-market-data",
        "build-market-breadth",
        "build-sber-relative-state",
        "build-derivative-features",
        "build-structural-regimes",
        "audit-predictive-data",
        "ablate-predictive-blocks",
        "predictive-data-status",
        "update-predictive-foundation",
    ):
        sub.add_parser(name)
    for name in (
        "discover-historical-equity-universe",
        "download-historical-equity-universe",
        "build-point-in-time-universe",
        "download-zcyc",
        "build-zcyc-features",
        "download-sber-futures-history",
        "build-sber-continuous-futures",
        "audit-sber-ifrs",
        "prepare-sber-ifrs-review",
        "audit-moex-options",
        "download-sber-intraday",
        "build-intraday-features",
        "validate-critical-predictive-data",
        "rerun-critical-data-ablation",
        "critical-predictive-data-status",
        "complete-sber-critical-data",
    ):
        sub.add_parser(name)
    for name in (
        "backfill-zcyc-history",
        "discover-expired-sber-futures",
        "backfill-sber-futures",
        "rebuild-sber-continuous-futures",
        "backfill-historical-liquid-universe",
        "calculate-survivorship-impact",
        "build-historical-financial-sector",
        "backfill-sber-intraday",
        "validate-sber-ifrs-review",
        "backfill-moex-options-history",
        "build-sber-common-sample",
        "calculate-coverage-tiers",
        "rerun-deep-ablation",
        "sber-model-readiness",
        "complete-sber-deep-backfill",
    ):
        sub.add_parser(name)
    for name in (
        "audit-historical-data-coverage",
        "backfill-issuer-fundamentals",
        "backfill-historical-universe",
        "backfill-sector-history",
        "backfill-external-factors",
        "backfill-futures",
        "audit-options-history",
        "audit-corporate-actions",
        "audit-dividends",
        "calculate-pit-integrity",
        "run-data-value-ablation",
        "historical-data-status",
        "complete-historical-data-audit",
    ):
        sub.add_parser(name)
    for name in (
        "backfill-actual-fundamentals",
        "backfill-actual-universe",
        "backfill-actual-external",
        "run-actual-historical-backfill",
    ):
        sub.add_parser(name)
    for name in (
        "build-modular-sber-samples",
        "validate-sber-futures-specs",
        "calculate-sber-futures-basis",
        "train-sber-experimental-direction",
        "calibrate-sber-direction",
        "evaluate-sber-modular-ablation",
        "calculate-sber-experimental-forecast",
        "evaluate-sber-timing-experiment",
        "save-sber-shadow-forecasts",
        "sber-experimental-model-status",
        "run-sber-unblocked-experiment",
    ):
        sub.add_parser(name)
    for name in (
        "build-feature-registry",
        "calculate-feature-importance",
        "discover-market-regimes",
        "calculate-alpha-decay",
        "evaluate-feature-stability",
        "build-factor-library",
        "update-market-state",
        "research-status",
        "run-alpha-research",
    ):
        sub.add_parser(name)
    for name in (
        "discover-portfolio-instruments",
        "download-portfolio-history",
        "build-portfolio-total-returns",
        "audit-preferred-share-rules",
        "build-cross-instrument-features",
        "run-portfolio-alpha-research",
        "build-portfolio-risk",
        "build-portfolio-dividend-calendar",
        "calculate-portfolio-scenarios",
        "save-portfolio-live-shadow",
        "audit-external-projects",
        "portfolio-status",
        "update-user-portfolio-research",
    ):
        sub.add_parser(name)
    for name in (
        "save-portfolio-reconciliation",
        "backfill-official-fundamentals",
        "build-issuer-valuations",
        "build-regime-risk",
        "build-portfolio-action-map",
        "build-portfolio-alternatives-v15",
        "save-portfolio-intelligence-snapshot",
        "portfolio-intelligence-status",
        "run-portfolio-intelligence",
        "run-daily-intelligence",
        "capture-daily-forecasts",
        "evaluate-matured-forecasts",
        "build-forecast-scorecards",
        "build-decision-scorecards",
        "build-learning-journal",
        "forecast-track-record",
        "forecast-status",
        "update-forecast-scorecards",
        "quick-daily-update",
        "deep-update",
        "model-research-dry-run",
        "model-governance-status",
    ):
        sub.add_parser(name)
    seed_market = sub.add_parser("seed-market-history-jobs")
    seed_market.add_argument("--limit", type=int)
    market_batch = sub.add_parser("backfill-market-history")
    market_batch.add_argument("--jobs", type=int, default=25)
    market_batch.add_argument("--pages-per-job", type=int)
    sub.add_parser("build-trading-statistics")
    sub.add_parser("market-history-status")
    sub.add_parser("backfill-official-market-series")
    sub.add_parser("evaluate-market-factors")
    continuing = sub.add_parser("continue-historical-market-backfill")
    continuing.add_argument("--jobs", type=int, default=100)
    continuing.add_argument("--pages-per-job", type=int, default=5)
    sub.add_parser("historical-market-backfill-status")
    sub.add_parser("historical-market-backfill-pause")
    sub.add_parser("rebuild-breadth-after-backfill")
    sub.add_parser("research-predictive-models")
    sub.add_parser("predictive-learning-status")
    sub.add_parser("run-model-tournament")
    sub.add_parser("model-tournament-status")
    sub.add_parser("data-inventory")
    receipt = sub.add_parser("update-receipt")
    receipt.add_argument("--update-id")
    for command_name in (
        "decision-trace",
        "instrument-data-passport",
        "explain-current-decision",
    ):
        transparency_command = sub.add_parser(command_name)
        transparency_command.add_argument("secid")
    for name in (
        "validate-portfolio-alpha",
        "validate-cross-instrument-factors",
        "compare-okama-metrics",
        "audit-pyportfolioopt",
        "audit-vectorbt",
        "audit-event-driven-backtest",
        "load-local-portfolio",
        "calculate-real-portfolio",
        "calculate-portfolio-alternatives",
        "discover-issuer-fundamentals",
        "build-portfolio-dividend-outlook",
        "calculate-portfolio-scenarios-v2",
        "save-real-portfolio-snapshot",
        "portfolio-validation-status",
        "run-portfolio-validation",
    ):
        sub.add_parser(name)
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
    elif args.command in {"research-predictive-models", "predictive-learning-status"}:
        from .adaptive_learning import research_predictive_models, research_status

        init_database()
        with connection() as con:
            result = (
                research_predictive_models(con)
                if args.command == "research-predictive-models"
                else research_status(con)
            )
            print(json.dumps(result, default=str, ensure_ascii=True))
    elif args.command in {"run-model-tournament", "model-tournament-status"}:
        from .model_tournament import run_tournament, tournament_status

        init_database()
        with connection() as con:
            result = (
                run_tournament(con)
                if args.command == "run-model-tournament"
                else tournament_status(con)
            )
            print(json.dumps(result, default=str, ensure_ascii=True))
    elif args.command in {
        "data-inventory",
        "update-receipt",
        "decision-trace",
        "instrument-data-passport",
        "explain-current-decision",
    }:
        from .transparency import (
            build_decision_trace,
            data_inventory,
            explain_current_decision,
            instrument_data_passport,
            update_receipt,
        )

        init_database()
        with connection() as con:
            if args.command == "data-inventory":
                result = data_inventory(con, database_path(), save=True)
            elif args.command == "update-receipt":
                result = update_receipt(con, args.update_id)
            elif args.command == "decision-trace":
                result = build_decision_trace(con, args.secid)
            elif args.command == "instrument-data-passport":
                result = instrument_data_passport(con, args.secid)
            else:
                result = explain_current_decision(con, args.secid)
            print(json.dumps(result, default=str, ensure_ascii=True))
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
    elif args.command in {
        "discover-macro",
        "download-macro",
        "update-macro",
        "calculate-macro-features",
        "validate-macro-models",
        "calculate-forecast-ranges",
        "macro-status",
        "audit-macro-model",
    }:
        init_database()
        with connection() as con:
            if args.command == "discover-macro":
                print({"series": discover_macro(con)})
            elif args.command in {"download-macro", "update-macro"}:
                end = date.fromisoformat(args.to_date) if args.to_date else date.today()
                start = date.fromisoformat(args.from_date) if args.from_date else date(1992, 1, 1)
                discover_macro(con)
                print(download_macro(con, start, end))
            elif args.command == "calculate-macro-features":
                print({"macro_feature_rows": calculate_macro_features(con)})
            elif args.command == "validate-macro-models":
                print({"model_result_rows": validate_macro_models(con)})
            elif args.command == "calculate-forecast-ranges":
                print({"forecast_rows": calculate_forecasts(con)})
            elif args.command == "audit-macro-model":
                print(run_macro_audit(con))
            else:
                print(
                    {
                        "series": con.execute("SELECT count(*) FROM macro_series").fetchone()[0],
                        "observations": con.execute("SELECT count(*) FROM macro_observations").fetchone()[0],
                        "features": con.execute("SELECT count(*) FROM macro_features").fetchone()[0],
                        "forecasts": con.execute("SELECT count(*) FROM forecast_ranges").fetchone()[0],
                        "latest_load": con.execute("SELECT max(finished_at) FROM macro_load_log").fetchone()[
                            0
                        ],
                    }
                )
    elif args.command in {
        "discover-sber-fundamentals",
        "download-sber-fundamentals",
        "import-sber-report",
        "build-fundamental-snapshots",
        "calculate-sber-valuation",
        "validate-sber-valuation",
        "sber-fundamental-status",
    }:
        init_database()
        if args.command == "discover-sber-fundamentals":
            print(discover_sber_fundamentals())
        elif args.command == "download-sber-fundamentals":
            print(download_sber_fundamentals())
        else:
            with connection() as con:
                if args.command == "import-sber-report":
                    print(import_sber_report(con, args.path))
                elif args.command == "build-fundamental-snapshots":
                    print({"rows": build_fundamental_snapshots(con)})
                elif args.command == "calculate-sber-valuation":
                    print(
                        calculate_sber_valuation(
                            con, Path(__file__).parents[2] / "config" / "sber_valuation.yaml"
                        )
                    )
                elif args.command == "validate-sber-valuation":
                    print(validate_sber_valuation(con))
                else:
                    print(
                        {
                            "observations": con.execute(
                                "SELECT count(*) FROM fundamental_observations"
                            ).fetchone()[0],
                            "releases": con.execute("SELECT count(*) FROM fundamental_releases").fetchone()[
                                0
                            ],
                            "snapshots": con.execute("SELECT count(*) FROM fundamental_snapshots").fetchone()[
                                0
                            ],
                            "valuations": con.execute("SELECT count(*) FROM valuation_results").fetchone()[0],
                        }
                    )
    elif args.command in {
        "discover-sber-reports",
        "download-sber-reports",
        "parse-sber-reports",
        "export-sber-review-template",
        "import-sber-reviewed-data",
        "validate-sber-reports",
        "build-sber-fundamental-history",
        "backtest-sber-fundamentals",
        "sber-fundamental-history-status",
        "update-sber-fundamentals",
    }:
        init_database()
        template = Path(__file__).parents[2] / "data" / "templates" / "sber_fundamentals_import_template.xlsx"
        with connection() as con:
            if args.command == "discover-sber-reports":
                print(discover_sber_reports(con))
            elif args.command == "download-sber-reports":
                raw = Path(__file__).parents[2] / "data" / "raw" / "fundamentals" / "sber"
                print(download_sber_reports(con, raw))
            elif args.command == "parse-sber-reports":
                print(parse_sber_reports(con))
            elif args.command == "export-sber-review-template":
                print({"template": str(template), "exists": template.exists()})
            elif args.command == "import-sber-reviewed-data":
                if not args.path:
                    raise SystemExit("Provide a reviewed CSV/XLSX path")
                print(import_sber_report(con, args.path))
            elif args.command == "validate-sber-reports":
                print(validate_sber_reports(con))
            elif args.command == "build-sber-fundamental-history":
                imported = import_sber_validated(con)
                snapshots = build_fundamental_snapshots(con)
                print(
                    {
                        "imported": imported,
                        "snapshots": snapshots,
                        "derived": build_sber_fundamental_history(con),
                        "confidence": calculate_sber_confidence(con),
                        "valuation": calculate_fact_sber_valuation(
                            con, Path(__file__).parents[2] / "config" / "sber_fundamental_history.yaml"
                        ),
                    }
                )
            elif args.command == "backtest-sber-fundamentals":
                print(backtest_sber_fundamentals(con))
            elif args.command == "sber-fundamental-history-status":
                print(sber_history_status(con))
            else:
                print(update_sber_fundamentals(con))
    elif args.command in {
        "update-sber-expanded-data",
        "build-sber-daily-state",
        "calculate-sber-dividend-outlook",
        "calculate-sber-decision",
        "backtest-sber-decision",
        "sber-decision-status",
        "update-sber-decision",
    }:
        init_database()
        with connection() as con:
            if args.command == "build-sber-daily-state":
                print(build_daily_state(con))
            elif args.command == "calculate-sber-dividend-outlook":
                print(calculate_dividend_outlook(con))
            elif args.command == "calculate-sber-decision":
                print(calculate_sber_decision(con))
            elif args.command == "backtest-sber-decision":
                print(backtest_sber_decision(con))
            elif args.command == "sber-decision-status":
                print(
                    con.execute(
                        "SELECT * FROM sber_decision_results ORDER BY as_of_date DESC LIMIT 1"
                    ).fetchone()
                )
            elif args.command == "update-sber-expanded-data":
                print(update_sber_fundamentals(con))
            else:
                started = time.perf_counter()
                history = update_sber_fundamentals(con)
                state = build_daily_state(con)
                dividend = calculate_dividend_outlook(con)
                valuation = calculate_fact_sber_valuation(
                    con, Path(__file__).parents[2] / "config" / "sber_fundamental_history.yaml"
                )
                ensemble = calculate_ensemble(con)
                decision = calculate_sber_decision(con)
                backtest = backtest_sber_decision(con)
                print(
                    {
                        "history": history,
                        "state": state,
                        "dividend": dividend,
                        "valuation": valuation,
                        "ensemble": ensemble,
                        "decision": decision,
                        "backtest": backtest,
                        "duration_seconds": time.perf_counter() - started,
                    }
                )
    elif args.command in {
        "discover-sber-information",
        "update-sber-information",
        "validate-sber-events",
        "calculate-sber-event-reactions",
        "calculate-sber-expectations",
        "build-sber-information-state",
        "recalculate-sber-after-events",
        "sber-information-status",
        "update-sber-intelligence",
    }:
        init_database()
        with connection() as con:
            if args.command == "discover-sber-information":
                print(discover_sber_information(con))
            elif args.command in {"update-sber-information", "update-sber-intelligence"}:
                print(update_sber_information(con))
            elif args.command == "validate-sber-events":
                print(validate_sber_events(con))
            elif args.command == "calculate-sber-event-reactions":
                print(
                    {
                        "reactions": calculate_sber_event_reactions(con),
                        "studies": build_sber_event_studies(con),
                    }
                )
            elif args.command == "calculate-sber-expectations":
                print(calculate_sber_expectations(con))
            elif args.command == "build-sber-information-state":
                print(build_sber_information_state(con))
            elif args.command == "recalculate-sber-after-events":
                print({"impacts": calculate_sber_impacts(con), "decision_influence": "weight_zero"})
            else:
                print(sber_information_status(con))
    elif args.command in {
        "discover-sber-operational-data",
        "update-sber-operational-data",
        "calculate-sber-nowcast",
        "calculate-sber-operating-state",
        "audit-sber-price-zones",
        "save-sber-live-decision",
        "update-sber-live-outcomes",
        "calculate-sber-live-scorecard",
        "sber-live-status",
        "run-sber-daily",
    }:
        init_database()
        with connection() as con:
            actions = {
                "discover-sber-operational-data": discover_sber_operational,
                "update-sber-operational-data": import_validated_fundamentals,
                "calculate-sber-nowcast": calculate_nowcast,
                "calculate-sber-operating-state": calculate_operating_state,
                "audit-sber-price-zones": audit_zones,
                "save-sber-live-decision": save_live_decision,
                "update-sber-live-outcomes": update_outcomes,
                "calculate-sber-live-scorecard": calculate_scorecard,
                "sber-live-status": sber_live_status,
                "run-sber-daily": run_sber_daily,
            }
            print(actions[args.command](con))
    elif args.command in {
        "discover-predictive-sources",
        "download-market-universe",
        "download-index-history",
        "download-derivatives",
        "download-rates-market",
        "download-cross-market-data",
        "build-market-breadth",
        "build-sber-relative-state",
        "build-derivative-features",
        "build-structural-regimes",
        "audit-predictive-data",
        "ablate-predictive-blocks",
        "predictive-data-status",
        "update-predictive-foundation",
    }:
        init_database()
        from .predictive_foundation.core import detect_structural_regimes

        with connection() as con:
            actions = {
                "discover-predictive-sources": discover_predictive_sources,
                "download-market-universe": download_market_universe,
                "download-index-history": download_index_history,
                "download-derivatives": download_derivatives,
                "download-rates-market": download_rates_market,
                "download-cross-market-data": download_cross_market_data,
                "build-market-breadth": build_market_breadth,
                "build-sber-relative-state": build_sber_relative_state,
                "build-derivative-features": build_derivative_features,
                "build-structural-regimes": detect_structural_regimes,
                "audit-predictive-data": audit_predictive_data,
                "ablate-predictive-blocks": ablate_predictive_blocks,
                "predictive-data-status": predictive_data_status,
                "update-predictive-foundation": update_predictive_foundation,
            }
            result = actions[args.command](con)
            if args.command == "update-predictive-foundation":
                result["lead_lag"] = build_lead_lag_diagnostics(con)
                result["ablation"] = ablate_predictive_blocks(con)
            print(result)
    elif args.command in {
        "discover-historical-equity-universe",
        "download-historical-equity-universe",
        "build-point-in-time-universe",
        "download-zcyc",
        "build-zcyc-features",
        "download-sber-futures-history",
        "build-sber-continuous-futures",
        "audit-sber-ifrs",
        "prepare-sber-ifrs-review",
        "audit-moex-options",
        "download-sber-intraday",
        "build-intraday-features",
        "validate-critical-predictive-data",
        "rerun-critical-data-ablation",
        "critical-predictive-data-status",
        "complete-sber-critical-data",
    }:
        from .critical_data import core as critical

        init_database()
        with connection() as con:
            actions = {
                "discover-historical-equity-universe": critical.discover_historical_equity_universe,
                "download-historical-equity-universe": critical.discover_historical_equity_universe,
                "build-point-in-time-universe": critical.validate_critical_predictive_data,
                "download-zcyc": critical.download_zcyc,
                "build-zcyc-features": critical.build_zcyc_features,
                "download-sber-futures-history": critical.download_sber_futures_history,
                "build-sber-continuous-futures": critical.build_sber_continuous_futures,
                "audit-sber-ifrs": critical.audit_sber_ifrs,
                "prepare-sber-ifrs-review": critical.audit_sber_ifrs,
                "audit-moex-options": critical.audit_moex_options,
                "download-sber-intraday": critical.download_sber_intraday,
                "build-intraday-features": critical.build_intraday_features,
                "validate-critical-predictive-data": critical.validate_critical_predictive_data,
                "rerun-critical-data-ablation": critical.rerun_critical_data_ablation,
                "critical-predictive-data-status": critical.status,
                "complete-sber-critical-data": critical.complete_critical_data,
            }
            print(actions[args.command](con))
    elif args.command in {
        "backfill-zcyc-history",
        "discover-expired-sber-futures",
        "backfill-sber-futures",
        "rebuild-sber-continuous-futures",
        "backfill-historical-liquid-universe",
        "calculate-survivorship-impact",
        "build-historical-financial-sector",
        "backfill-sber-intraday",
        "validate-sber-ifrs-review",
        "backfill-moex-options-history",
        "build-sber-common-sample",
        "calculate-coverage-tiers",
        "rerun-deep-ablation",
        "sber-model-readiness",
        "complete-sber-deep-backfill",
    }:
        from .deep_backfill import core as deep

        init_database()
        with connection() as con:
            actions = {
                "backfill-zcyc-history": deep.backfill_zcyc_history,
                "discover-expired-sber-futures": deep.discover_expired_sber_futures,
                "backfill-sber-futures": deep.backfill_sber_futures,
                "rebuild-sber-continuous-futures": deep.rebuild_continuous_futures,
                "backfill-historical-liquid-universe": deep.backfill_historical_liquid_universe,
                "calculate-survivorship-impact": deep.calculate_survivorship_impact,
                "build-historical-financial-sector": deep.build_historical_financial_sector,
                "backfill-sber-intraday": deep.record_intraday_coverage,
                "validate-sber-ifrs-review": deep.validate_sber_ifrs_review,
                "backfill-moex-options-history": deep.backfill_options_history,
                "build-sber-common-sample": deep.build_common_sample,
                "calculate-coverage-tiers": deep.calculate_coverage_tiers,
                "rerun-deep-ablation": deep.rerun_deep_ablation,
                "sber-model-readiness": deep.model_readiness,
                "complete-sber-deep-backfill": deep.complete_deep_backfill,
            }
            print(actions[args.command](con))
    elif args.command in {
        "audit-historical-data-coverage",
        "backfill-issuer-fundamentals",
        "backfill-historical-universe",
        "backfill-sector-history",
        "backfill-external-factors",
        "backfill-futures",
        "audit-options-history",
        "audit-corporate-actions",
        "audit-dividends",
        "calculate-pit-integrity",
        "run-data-value-ablation",
        "historical-data-status",
        "complete-historical-data-audit",
    }:
        from .historical_data import core as historical

        init_database()
        with connection() as con:
            actions = {
                "audit-historical-data-coverage": historical.build_coverage_matrix,
                "backfill-issuer-fundamentals": historical.backfill_issuer_fundamentals,
                "backfill-historical-universe": historical.backfill_historical_universe,
                "backfill-sector-history": historical.backfill_sector_history,
                "backfill-external-factors": historical.backfill_external_factors,
                "backfill-futures": historical.backfill_futures,
                "audit-options-history": historical.audit_options_history,
                "audit-corporate-actions": historical.audit_corporate_actions,
                "audit-dividends": historical.audit_dividends,
                "calculate-pit-integrity": historical.calculate_pit_integrity,
                "run-data-value-ablation": historical.run_data_value_ablation,
                "historical-data-status": historical.historical_data_status,
                "complete-historical-data-audit": historical.complete_historical_data_audit,
            }
            print(actions[args.command](con))
    elif args.command in {
        "backfill-actual-fundamentals",
        "backfill-actual-universe",
        "backfill-actual-external",
        "run-actual-historical-backfill",
    }:
        from .actual_backfill import core as actual

        init_database()
        with connection() as con:
            if args.command == "backfill-actual-fundamentals":
                result = actual.backfill_historical_fundamentals(con)
            elif args.command == "backfill-actual-universe":
                result = actual.backfill_universe_pilot(con)
            elif args.command == "backfill-actual-external":
                result = actual.backfill_external_and_contracts(con)
            else:
                result = {
                    "fundamentals": actual.backfill_historical_fundamentals(con),
                    "universe": actual.backfill_universe_pilot(con),
                    "external": actual.backfill_external_and_contracts(con),
                }
            print(result)
    elif args.command in {
        "build-modular-sber-samples",
        "validate-sber-futures-specs",
        "calculate-sber-futures-basis",
        "train-sber-experimental-direction",
        "calibrate-sber-direction",
        "evaluate-sber-modular-ablation",
        "calculate-sber-experimental-forecast",
        "evaluate-sber-timing-experiment",
        "save-sber-shadow-forecasts",
        "sber-experimental-model-status",
        "run-sber-unblocked-experiment",
    }:
        from .unblocked_experiment import core as experiment

        init_database()
        with connection() as con:
            actions = {
                "build-modular-sber-samples": lambda c: {
                    "targets": experiment.build_targets(c),
                    "samples": experiment.build_modular_samples(c),
                },
                "validate-sber-futures-specs": experiment.validate_futures_specs,
                "calculate-sber-futures-basis": experiment.calculate_futures_basis,
                "train-sber-experimental-direction": experiment.train_direction,
                "calibrate-sber-direction": experiment.calibrate_direction,
                "evaluate-sber-modular-ablation": experiment.evaluate_ablation,
                "calculate-sber-experimental-forecast": experiment.calculate_forecast,
                "evaluate-sber-timing-experiment": experiment.timing_experiment,
                "save-sber-shadow-forecasts": experiment.save_shadow_forecasts,
                "sber-experimental-model-status": experiment.experimental_status,
                "run-sber-unblocked-experiment": experiment.run_unblocked_experiment,
            }
            print(actions[args.command](con))
    elif args.command in {
        "build-feature-registry",
        "calculate-feature-importance",
        "discover-market-regimes",
        "calculate-alpha-decay",
        "evaluate-feature-stability",
        "build-factor-library",
        "update-market-state",
        "research-status",
        "run-alpha-research",
    }:
        from .alpha_research import core as alpha

        init_database()
        with connection() as con:
            actions = {
                "build-feature-registry": alpha.build_feature_registry,
                "calculate-feature-importance": alpha.calculate_feature_importance,
                "discover-market-regimes": alpha.discover_market_regimes,
                "calculate-alpha-decay": alpha.calculate_alpha_decay,
                "evaluate-feature-stability": alpha.evaluate_feature_stability,
                "build-factor-library": alpha.build_factor_library,
                "update-market-state": alpha.update_market_state,
                "research-status": alpha.research_status,
                "run-alpha-research": alpha.run_alpha_research,
            }
            print(actions[args.command](con))
    elif args.command in {
        "discover-portfolio-instruments",
        "download-portfolio-history",
        "build-portfolio-total-returns",
        "audit-preferred-share-rules",
        "build-cross-instrument-features",
        "run-portfolio-alpha-research",
        "build-portfolio-risk",
        "build-portfolio-dividend-calendar",
        "calculate-portfolio-scenarios",
        "save-portfolio-live-shadow",
        "audit-external-projects",
        "portfolio-status",
        "update-user-portfolio-research",
    }:
        from .portfolio_research import core as portfolio

        init_database()
        with connection() as con:
            actions = {
                "discover-portfolio-instruments": portfolio.discover_portfolio_instruments,
                "download-portfolio-history": portfolio.download_portfolio_history,
                "build-portfolio-total-returns": portfolio.build_portfolio_total_returns,
                "audit-preferred-share-rules": portfolio.audit_preferred_share_rules,
                "build-cross-instrument-features": portfolio.build_cross_instrument_features,
                "run-portfolio-alpha-research": portfolio.run_portfolio_alpha_research,
                "build-portfolio-risk": portfolio.build_portfolio_risk,
                "build-portfolio-dividend-calendar": portfolio.build_portfolio_dividend_calendar,
                "calculate-portfolio-scenarios": portfolio.calculate_portfolio_scenarios,
                "save-portfolio-live-shadow": portfolio.save_portfolio_live_shadow,
                "audit-external-projects": portfolio.audit_external_projects,
                "portfolio-status": portfolio.portfolio_status,
                "update-user-portfolio-research": portfolio.update_user_portfolio_research,
            }
            print(actions[args.command](con))
    elif args.command in {
        "validate-portfolio-alpha",
        "validate-cross-instrument-factors",
        "compare-okama-metrics",
        "audit-pyportfolioopt",
        "audit-vectorbt",
        "audit-event-driven-backtest",
        "load-local-portfolio",
        "calculate-real-portfolio",
        "calculate-portfolio-alternatives",
        "discover-issuer-fundamentals",
        "build-portfolio-dividend-outlook",
        "calculate-portfolio-scenarios-v2",
        "save-real-portfolio-snapshot",
        "portfolio-validation-status",
        "run-portfolio-validation",
    }:
        from .portfolio_research import external_methods, issuers, portfolio_v14, validation

        init_database()
        with connection() as con:

            def audit(c):
                return external_methods.audit_external_methods(c)

            actions = {
                "validate-portfolio-alpha": validation.validate_portfolio_alpha,
                "validate-cross-instrument-factors": validation.validate_cross_instrument_factors,
                "compare-okama-metrics": external_methods.compare_okama_metrics,
                "audit-pyportfolioopt": audit,
                "audit-vectorbt": audit,
                "audit-event-driven-backtest": audit,
                "load-local-portfolio": lambda c: portfolio_v14.parse_local_portfolio(),
                "calculate-real-portfolio": portfolio_v14.calculate_real_portfolio,
                "calculate-portfolio-alternatives": portfolio_v14.calculate_portfolio_alternatives,
                "discover-issuer-fundamentals": issuers.discover_issuer_fundamentals,
                "build-portfolio-dividend-outlook": portfolio_v14.build_portfolio_dividend_outlook,
                "calculate-portfolio-scenarios-v2": portfolio_v14.calculate_portfolio_scenarios_v2,
                "save-real-portfolio-snapshot": portfolio_v14.save_real_portfolio_snapshot,
                "portfolio-validation-status": portfolio_v14.portfolio_validation_status,
                "run-portfolio-validation": portfolio_v14.run_portfolio_validation,
            }
            print(actions[args.command](con))
    elif args.command in {
        "seed-market-history-jobs",
        "backfill-market-history",
        "build-trading-statistics",
        "market-history-status",
        "backfill-official-market-series",
        "evaluate-market-factors",
        "continue-historical-market-backfill",
        "historical-market-backfill-status",
        "historical-market-backfill-pause",
        "rebuild-breadth-after-backfill",
    }:
        from . import market_history

        init_database()
        with connection() as con:
            if args.command == "seed-market-history-jobs":
                result = market_history.seed_jobs(con, limit=args.limit)
            elif args.command == "backfill-market-history":
                result = market_history.run_batch(con, jobs=args.jobs, pages_per_job=args.pages_per_job)
            elif args.command == "build-trading-statistics":
                result = market_history.build_trading_statistics(con)
            elif args.command == "backfill-official-market-series":
                result = market_history.backfill_official_market_series(con)
            elif args.command == "evaluate-market-factors":
                result = market_history.evaluate_market_factors(con)
            elif args.command == "continue-historical-market-backfill":
                market_history.set_pause(con, False)
                result = market_history.run_batch(con, jobs=args.jobs, pages_per_job=args.pages_per_job)
            elif args.command == "historical-market-backfill-pause":
                result = market_history.set_pause(con, True)
            elif args.command == "rebuild-breadth-after-backfill":
                result = market_history.build_trading_statistics(con)
            elif args.command == "historical-market-backfill-status":
                result = market_history.coverage(con, save=True)
            else:
                result = market_history.coverage(con, save=True)
            print(result)
    elif args.command in {
        "save-portfolio-reconciliation",
        "backfill-official-fundamentals",
        "build-issuer-valuations",
        "build-regime-risk",
        "build-portfolio-action-map",
        "build-portfolio-alternatives-v15",
        "save-portfolio-intelligence-snapshot",
        "portfolio-intelligence-status",
        "run-portfolio-intelligence",
        "run-daily-intelligence",
        "capture-daily-forecasts",
        "evaluate-matured-forecasts",
        "build-forecast-scorecards",
        "build-decision-scorecards",
        "build-learning-journal",
        "forecast-track-record",
        "forecast-status",
        "update-forecast-scorecards",
        "quick-daily-update",
        "deep-update",
        "model-research-dry-run",
        "model-governance-status",
    }:
        from .portfolio_research import (
            daily_governance,
            forecast_scorecards,
            human_intelligence,
            intelligence,
        )

        init_database()
        with connection() as con:
            actions = {
                "save-portfolio-reconciliation": intelligence.save_reconciliation,
                "backfill-official-fundamentals": intelligence.backfill_official_fundamentals,
                "build-issuer-valuations": intelligence.build_valuation_states,
                "build-regime-risk": intelligence.build_regime_risk,
                "build-portfolio-action-map": intelligence.build_action_map,
                "build-portfolio-alternatives-v15": intelligence.build_alternatives,
                "save-portfolio-intelligence-snapshot": intelligence.save_intelligence_snapshot,
                "portfolio-intelligence-status": intelligence.intelligence_status,
                "run-portfolio-intelligence": intelligence.run_intelligence,
                "run-daily-intelligence": human_intelligence.run_daily_intelligence,
                "capture-daily-forecasts": forecast_scorecards.capture_daily_forecasts,
                "evaluate-matured-forecasts": forecast_scorecards.evaluate_matured_forecasts,
                "build-forecast-scorecards": forecast_scorecards.build_forecast_scorecards,
                "build-decision-scorecards": forecast_scorecards.build_decision_scorecards,
                "build-learning-journal": forecast_scorecards.build_learning_journal,
                "forecast-track-record": forecast_scorecards.forecast_track_record,
                "forecast-status": forecast_scorecards.forecast_status,
                "update-forecast-scorecards": forecast_scorecards.update_forecast_scorecards,
                "quick-daily-update": daily_governance.run_daily_update,
                "deep-update": lambda con: daily_governance.run_daily_update(con, mode="deep"),
                "model-research-dry-run": lambda con: daily_governance.run_daily_update(
                    con, mode="retrain", dry_run=True
                ),
                "model-governance-status": daily_governance.governance_status,
            }
            print(actions[args.command](con))
    elif args.command == "dashboard":
        from .dashboard.launcher import mark_process

        app = Path(__file__).parent / "dashboard" / "app.py"
        process = subprocess.Popen(
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
        )
        mark_process(process.pid)
        if process.wait() != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args)


if __name__ == "__main__":
    main()
