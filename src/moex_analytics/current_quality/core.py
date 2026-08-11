"""Separate historical audit volume from issues affecting today's immutable snapshot."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

import yaml

from moex_analytics.config import PROJECT_ROOT

VERSION = "current-quality-v1"
PORTFOLIO = ("SBERP", "LKOH", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX", "LSNGP", "X5")
FAMILIES = {
    "prices": ("canonical_daily_prices", "trade_date", "daily"),
    "trading statistics": ("trading_statistics_daily", "trade_date", "daily"),
    "breadth": ("market_breadth_daily", "trade_date", "daily"),
    "indices": ("canonical_daily_prices", "trade_date", "daily"),
    "FX": ("cross_market_daily", "trade_date", "daily"),
    "rates": ("macro_observations", "observation_date", "daily"),
    "fundamentals": ("issuer_fundamental_values", "period_end", "quarterly"),
    "news/events": ("news_items", "published_at", "event"),
    "portfolio analytics": ("human_daily_reports", "analysis_cutoff", "daily"),
    "ranking": ("current_portfolio_ranking", "cutoff", "daily"),
    "forecasts": ("forecast_registry", "cutoff", "daily"),
}
DDL = """
CREATE TABLE IF NOT EXISTS current_quality_runs(
 run_id VARCHAR PRIMARY KEY,as_of DATE,expected_market_date DATE,market_session_closed BOOLEAN,
 status VARCHAR,critical INTEGER,warnings INTEGER,created_at TIMESTAMP,version VARCHAR);
CREATE TABLE IF NOT EXISTS dataset_freshness_current(
 run_id VARCHAR,dataset_family VARCHAR,latest_data_date DATE,expected_latest_date DATE,status VARCHAR,
 reason VARCHAR,PRIMARY KEY(run_id,dataset_family));
CREATE TABLE IF NOT EXISTS current_quality_issues(
 run_id VARCHAR,dataset VARCHAR,instrument VARCHAR,date_from DATE,date_to DATE,severity VARCHAR,
 status VARCHAR,affects_current_snapshot BOOLEAN,affects_training BOOLEAN,affects_prediction BOOLEAN,
 reason VARCHAR,source_issue_id VARCHAR,PRIMARY KEY(run_id,dataset,instrument,source_issue_id));
CREATE TABLE IF NOT EXISTS portfolio_quality_current(
 run_id VARCHAR,secid VARCHAR,price_data VARCHAR,market_context VARCHAR,ranking VARCHAR,
 fundamentals VARCHAR,corporate_actions VARCHAR,overall VARCHAR,reason VARCHAR,
 PRIMARY KEY(run_id,secid));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _tables(con: Any) -> set[str]:
    return {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _columns(con: Any, table: str) -> set[str]:
    return {row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()}


def _expected_market_date(con: Any, as_of: date, session_closed: bool) -> date:
    limit = as_of if session_closed else as_of - timedelta(days=1)
    if "trading_calendar" in _tables(con):
        found = con.execute("SELECT max(trade_date) FROM trading_calendar WHERE is_trading_day "
                            "AND trade_date<=?", [limit]).fetchone()[0]
        if found and (limit - found).days <= 3:
            return found
    while limit.weekday() >= 5:
        limit -= timedelta(days=1)
    return limit


def _latest(con: Any, table: str, column: str) -> date | None:
    if table not in _tables(con) or column not in _columns(con, table):
        return None
    value = con.execute(f"SELECT max({column}) FROM {table}").fetchone()[0]
    return value.date() if isinstance(value, datetime) else value


def _portfolio_from_local() -> tuple[str, ...]:
    path = PROJECT_ROOT / "config" / "portfolio_positions.local.yaml"
    if not path.exists():
        return PORTFOLIO
    rows = yaml.safe_load(path.read_text(encoding="utf-8")).get("positions", [])
    return tuple(row["secid"] for row in rows) or PORTFOLIO


def audit_current_quality(con: Any, as_of: date | None = None,
                          session_closed: bool = False) -> dict[str, Any]:
    ensure_schema(con)
    as_of = as_of or date.today()
    expected = _expected_market_date(con, as_of, session_closed)
    run_id = hashlib.sha256(f"{VERSION}|{as_of}|{expected}|{session_closed}".encode()).hexdigest()[:20]
    con.execute("DELETE FROM dataset_freshness_current WHERE run_id=?", [run_id])
    con.execute("DELETE FROM current_quality_issues WHERE run_id=?", [run_id])
    con.execute("DELETE FROM portfolio_quality_current WHERE run_id=?", [run_id])
    family_states = {}
    for family, (table, column, frequency) in FAMILIES.items():
        latest = _latest(con, table, column)
        family_expected = expected if frequency == "daily" else as_of
        if latest is None:
            status, reason = "unavailable", "dataset or dated observations unavailable"
        elif frequency == "quarterly":
            status = "fresh" if (as_of - latest).days <= 180 else "stale"
            reason = "quarterly publication window"
        elif frequency == "event":
            status = "fresh" if (as_of - latest).days <= 7 else "stale"
            reason = ("event stream checked within seven days" if status == "fresh"
                      else "event feed not recent")
        else:
            status = "fresh" if latest >= expected else "stale"
            reason = "meets trading-calendar cutoff" if status == "fresh" else "behind expected market cutoff"
        family_states[family] = status
        con.execute("INSERT INTO dataset_freshness_current (run_id,dataset_family,latest_data_date,"
            "expected_latest_date,status,reason) VALUES (?,?,?,?,?,?)",
            [run_id, family, latest, family_expected, status, reason])
        if family != "prices" and status in {"stale", "unavailable"}:
            blocking = family in {"trading statistics", "breadth", "indices", "FX", "rates",
                                  "ranking", "forecasts"}
            con.execute("INSERT INTO current_quality_issues (run_id,dataset,instrument,date_from,date_to,"
                "severity,status,affects_current_snapshot,affects_training,affects_prediction,reason,"
                "source_issue_id) VALUES (?,?, 'MARKET',?,?, 'warning','active',true,?,?,?,?)",
                [run_id, family, latest, latest, blocking, blocking, reason,
                 f"family-{family}-{latest}"])
    portfolio = _portfolio_from_local()
    price_columns = _columns(con, "canonical_daily_prices")
    for secid in portfolio:
        price = (con.execute("SELECT max(trade_date) FROM canonical_daily_prices WHERE "
                             "canonical_secid=?", [secid]).fetchone()[0]
                 if "canonical_secid" in price_columns else _latest(
                     con, "canonical_daily_prices", "trade_date"))
        price_state = "fresh" if price and price >= expected else "stale"
        ranking_state = family_states["ranking"]
        fundamental = "partial" if family_states["fundamentals"] != "unavailable" else "unavailable"
        context = "fresh" if all(family_states[x] == "fresh" for x in ("breadth", "indices")) else "partial"
        corporate = "available" if "historical_corporate_actions" in _tables(con) else "unavailable"
        overall = "critical" if price_state == "stale" else (
            "warning" if "unavailable" in (ranking_state, fundamental) or context == "partial" else "good")
        con.execute("INSERT INTO portfolio_quality_current (run_id,secid,price_data,market_context,"
            "ranking,fundamentals,corporate_actions,overall,reason) VALUES (?,?,?,?,?,?,?,?,?)",
            [run_id, secid, price_state, context, ranking_state, fundamental, corporate, overall,
             "current compatible cutoff only"])
        if price_state == "stale":
            con.execute("INSERT INTO current_quality_issues (run_id,dataset,instrument,date_from,date_to,"
                "severity,status,affects_current_snapshot,affects_training,affects_prediction,reason,"
                "source_issue_id) VALUES (?,?,?,?,?,'critical','active',true,false,true,?,?)",
                [run_id, "prices", secid, price, price, "portfolio price behind expected cutoff",
                 f"price-{secid}-{price}"])
    critical, warnings = con.execute("SELECT count(*) FILTER(WHERE severity='critical'),"
        "count(*) FILTER(WHERE severity='warning') FROM current_quality_issues WHERE run_id=?",
        [run_id]).fetchone()
    status = "red" if critical else "yellow" if warnings else "green"
    con.execute("INSERT OR REPLACE INTO current_quality_runs (run_id,as_of,expected_market_date,"
        "market_session_closed,status,critical,warnings,created_at,version) VALUES (?,?,?,?,?,?,?,"
        "current_timestamp,?)",
        [run_id, as_of, expected, session_closed, status, critical, warnings, VERSION])
    return quality_summary(con, run_id)


def quality_summary(con: Any, run_id: str | None = None) -> dict[str, Any]:
    ensure_schema(con)
    if run_id is None:
        row = con.execute("SELECT run_id FROM current_quality_runs ORDER BY created_at DESC "
                          "LIMIT 1").fetchone()
        if not row:
            return {"status": "unavailable", "critical": 0, "warnings": 0}
        run_id = row[0]
    run = con.execute("SELECT as_of,expected_market_date,status,critical,warnings FROM "
                      "current_quality_runs WHERE run_id=?", [run_id]).fetchone()
    total = con.execute("SELECT count(*) FROM data_quality_issues").fetchone()[0] \
        if "data_quality_issues" in _tables(con) else 0
    current = con.execute("SELECT count(*),count(*) FILTER(WHERE affects_current_snapshot),"
        "count(*) FILTER(WHERE instrument IN ('SBERP','LKOH','MTSS','TRNFP','TATNP','PHOR','MOEX',"
        "'LSNGP','X5')),count(*) FILTER(WHERE affects_prediction),"
        "count(*) FILTER(WHERE NOT affects_prediction) "
        "FROM current_quality_issues WHERE run_id=?", [run_id]).fetchone()
    return {"run_id": run_id, "today": run[0], "market_cutoff": run[1], "status": run[2],
            "critical": run[3], "warnings": run[4], "total_historical": total,
            "unresolved": total, "active": current[0], "current_snapshot_relevant": current[1],
            "portfolio_relevant": current[2], "prediction_blocking": current[3],
            "informational": current[4]}
