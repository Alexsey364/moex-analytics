"""Read-only facts plus immutable explanations over existing analytics."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS transparency_inventory_snapshots(
 snapshot_id VARCHAR PRIMARY KEY,cutoff TIMESTAMP,totals_json JSON,storage_json JSON,
 freshness_json JSON,created_at TIMESTAMP,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS transparency_update_receipts(
 update_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,update_type VARCHAR,
 cutoff DATE,duration_seconds DOUBLE,sources_json JSON,totals_json JSON,status VARCHAR,
 created_at TIMESTAMP,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS transparency_decision_traces(
 decision_id VARCHAR PRIMARY KEY,cutoff DATE,secid VARCHAR,final_status VARCHAR,
 source_snapshot_id VARCHAR,summary_json JSON,created_at TIMESTAMP,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS transparency_decision_blocks(
 decision_id VARCHAR,block_name VARCHAR,block_status VARCHAR,score DOUBLE,confidence VARCHAR,
 direction VARCHAR,used_in_final BOOLEAN,weight_if_applicable DOUBLE,freshness VARCHAR,
 reason VARCHAR,evidence_ids_json JSON,PRIMARY KEY(decision_id,block_name));
CREATE TABLE IF NOT EXISTS transparency_evidence(
 evidence_id VARCHAR PRIMARY KEY,decision_id VARCHAR,block_name VARCHAR,source VARCHAR,
 period_from VARCHAR,period_to VARCHAR,last_value VARCHAR,observations BIGINT,used BOOLEAN,
 exclusion_reason VARCHAR,source_url VARCHAR,document VARCHAR,content_hash VARCHAR,
 retrieved_at TIMESTAMP,available_from TIMESTAMP,parser_version VARCHAR);
"""

DATASETS = {
    "Акции EOD": ("canonical_daily_prices", "trade_date", "MOEX ISS", "daily"),
    "Оборот и объём": ("canonical_daily_prices", "trade_date", "MOEX ISS", "daily"),
    "Ширина рынка": ("market_breadth_daily", "trade_date", "MOEX universe", "daily"),
    "Состояние рынка": ("market_state_daily", "trade_date", "calculated", "daily"),
    "Индексы": ("index_history", "trade_date", "MOEX ISS", "daily"),
    "FX": ("macro_observations", "observation_date", "CBR/MOEX", "daily"),
    "Фундаментал": ("issuer_fundamental_values", "period_end", "issuer reports", "quarterly"),
    "Дивиденды": ("dividends", "registry_close_date", "MOEX/issuers", "event"),
    "Фьючерсы": ("deep_sber_futures_daily", "trade_date", "MOEX ISS", "daily"),
    "Опционы": ("moex_options_audit", "last_trade", "MOEX ISS", "event"),
    "Intraday": ("intraday_candles", "begin", "MOEX ISS", "intraday"),
    "Прогнозы": ("forecast_registry", "cutoff", "internal", "daily"),
}


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _tables(con: Any) -> set[str]:
    return {r[0] for r in con.execute("SHOW TABLES").fetchall()}


def _columns(con: Any, table: str) -> set[str]:
    if table not in _tables(con):
        return set()
    return {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}


def _count(con: Any, table: str, where: str = "") -> int:
    if table not in _tables(con):
        return 0
    return int(con.execute(f"SELECT count(*) FROM {table} {where}").fetchone()[0])


def _range(con: Any, table: str, column: str, where: str = "") -> tuple[Any, Any, int]:
    if table not in _tables(con) or column not in _columns(con, table):
        return None, None, 0
    return con.execute(f"SELECT min({column}),max({column}),count(*) FROM {table} {where}").fetchone()


def freshness_inventory(con: Any, as_of: date | None = None) -> list[dict[str, Any]]:
    today = as_of or date.today()
    rows = []
    for name, (table, column, source, frequency) in DATASETS.items():
        first, latest, count = _range(con, table, column)
        latest_date = latest.date() if isinstance(latest, datetime) else latest
        age = (today - latest_date).days if latest_date else None
        limit = {"intraday": 2, "daily": 5, "quarterly": 150, "event": 400}.get(frequency, 30)
        status = (
            "⚪ данных нет"
            if age is None
            else "🟢 актуально"
            if age <= limit
            else "🟡 немного устарело"
            if age <= limit * 2
            else "🟠 устарело"
        )
        rows.append(
            {
                "dataset": name,
                "table": table,
                "source": source,
                "first": first,
                "latest": latest,
                "observations": count,
                "frequency": frequency,
                "age_days": age,
                "status": status,
            }
        )
    return rows


def data_inventory(con: Any, database_file: Path | None = None, save: bool = False) -> dict[str, Any]:
    ensure_schema(con)
    tables = _tables(con)
    securities_table = (
        "historical_equity_universe" if "historical_equity_universe" in tables else "instruments"
    )
    active_column = "is_traded" if "is_traded" in _columns(con, securities_table) else "is_active"
    totals = {
        "historical_securities": _count(con, securities_table),
        "active_securities": _count(con, securities_table, f"WHERE coalesce({active_column},TRUE)")
        if active_column in _columns(con, securities_table)
        else _count(con, securities_table),
        "inactive_securities": _count(con, securities_table, f"WHERE NOT coalesce({active_column},TRUE)")
        if active_column in _columns(con, securities_table)
        else 0,
        "eod_rows": _count(con, "canonical_daily_prices"),
        "liquidity_observations": _count(con, "historical_liquidity_features"),
        "breadth_observations": _count(con, "market_breadth_daily"),
        "market_state_observations": _count(con, "market_state_daily"),
        "index_observations": _count(con, "index_history"),
        "macro_observations": _count(con, "macro_observations"),
        "fundamental_documents": _count(con, "issuer_fundamental_documents")
        + _count(con, "fundamental_documents"),
        "fundamental_validated_values": _count(
            con, "issuer_fundamental_values", "WHERE validation_status='validated'"
        )
        if "issuer_fundamental_values" in tables
        else 0,
        "dividend_observations": _count(con, "dividends"),
        "corporate_actions": _count(con, "historical_corporate_actions"),
        "futures_contracts": _count(con, "expired_sber_futures"),
        "futures_observations": _count(con, "deep_sber_futures_daily"),
        "options_observations": _count(con, "moex_options_audit"),
        "intraday_candles": _count(con, "intraday_candles"),
        "events": _count(con, "sber_events"),
        "forecasts": _count(con, "forecast_registry"),
        "forecast_outcomes": _count(con, "forecast_outcomes"),
        "model_versions": _count(con, "adaptive_model_registry"),
    }
    if "forecast_registry" in tables:
        cols = _columns(con, "forecast_registry")
        if "status" in cols:
            totals["pending_forecasts"] = _count(con, "forecast_registry", "WHERE status='pending'")
        elif "forecast_outcomes" in tables:
            matured = con.execute(
                """SELECT count(DISTINCT f.forecast_id) FROM forecast_registry f
                JOIN forecast_outcomes o USING(forecast_id)"""
            ).fetchone()[0]
            totals["matured_forecasts"] = int(matured)
            totals["pending_forecasts"] = max(0, totals["forecasts"] - int(matured))
        else:
            totals["pending_forecasts"] = totals["forecasts"]
    root = database_file.parent.parent if database_file else None

    def directory_size(path: Path | None) -> int:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path and path.exists() else 0

    storage = {
        "duckdb_bytes": database_file.stat().st_size if database_file and database_file.exists() else None,
        "raw_bytes": directory_size(root / "data" / "raw" if root else None),
        "processed_bytes": directory_size(root / "data" / "processed" if root else None),
        "cache_bytes": directory_size(root / ".cache" if root else None),
        "backup_bytes": directory_size(root / "data" / "local" / "portfolio_backups" if root else None),
    }
    storage["total_bytes"] = sum(value or 0 for value in storage.values())
    fresh = freshness_inventory(con)
    payload = {"cutoff": datetime.now().isoformat(), "totals": totals, "storage": storage, "freshness": fresh}
    if save:
        snapshot_id = hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()[
            :24
        ]
        con.execute(
            """INSERT OR IGNORE INTO transparency_inventory_snapshots
            VALUES (?,?,?,?,?,current_timestamp,TRUE)""",
            [
                snapshot_id,
                datetime.now(),
                json.dumps(totals),
                json.dumps(storage),
                json.dumps(fresh, default=str),
            ],
        )
        payload["snapshot_id"] = snapshot_id
    return payload


def instrument_data_passport(con: Any, secid: str) -> dict[str, Any]:
    secid = secid.upper()
    price_table = "canonical_daily_prices"
    price_key = "canonical_secid" if "canonical_secid" in _columns(con, price_table) else "secid"
    price_where = f"WHERE {price_key}='{secid.replace(chr(39), '')}'"
    first, latest, rows = _range(con, price_table, "trade_date", price_where)
    secid_where = f"WHERE secid='{secid.replace(chr(39), '')}'"
    fundamentals = _range(con, "issuer_fundamental_values", "period_end", secid_where)
    dividend_cols = _columns(con, "dividends")
    dividend_key = "canonical_secid" if "canonical_secid" in dividend_cols else "secid"
    dividend_date = "registry_close_date" if "registry_close_date" in dividend_cols else "record_date"
    dividend_where = f"WHERE {dividend_key}='{secid.replace(chr(39), '')}'"
    dividends = _range(con, "dividends", dividend_date, dividend_where)
    forecasts = _count(con, "forecast_registry", secid_where)
    return {
        "secid": secid,
        "price": {"first": first, "latest": latest, "rows": rows},
        "turnover_rows": rows,
        "volume_rows": rows,
        "numtrades_rows": rows,
        "fundamentals": {"first": fundamentals[0], "latest": fundamentals[1], "rows": fundamentals[2]},
        "dividends": {"first": dividends[0], "latest": dividends[1], "rows": dividends[2]},
        "forecasts": forecasts,
        "macro": "shared point-in-time market context",
    }


BLOCKS = (
    "Technical",
    "Market regime",
    "Breadth",
    "Relative strength",
    "Liquidity",
    "Volatility",
    "Drawdown",
    "Rates",
    "Yield curve",
    "FX",
    "Sector",
    "Futures",
    "Fundamentals",
    "Valuation",
    "Dividend",
    "Events",
    "Portfolio concentration",
    "Portfolio risk contribution",
    "Live model quality",
)


def build_decision_trace(con: Any, secid: str) -> dict[str, Any]:
    ensure_schema(con)
    secid = secid.upper()
    row = (
        con.execute(
            "SELECT * FROM portfolio_action_map WHERE secid=? ORDER BY snapshot_id DESC LIMIT 1", [secid]
        ).fetchone()
        if "portfolio_action_map" in _tables(con)
        else None
    )
    values = dict(zip([d[0] for d in con.description], row, strict=False)) if row else {}
    cutoff = date.today()
    final = str(values.get("target_status") or "insufficient_data")
    source_snapshot = str(values.get("snapshot_id") or "none")
    decision_id = hashlib.sha256(f"{cutoff}:{secid}:{source_snapshot}:{final}".encode()).hexdigest()[:24]
    positive = json.loads(values.get("evidence_for_json") or "[]") if row else []
    negative = json.loads(values.get("evidence_against_json") or "[]") if row else []
    summary = {
        "positive": positive,
        "negative": negative,
        "neutral": [],
        "main_limitation": "limited or absent matured live forecasts",
        "rule_based": True,
        "probability_disclosed": False,
    }
    con.execute(
        "INSERT OR IGNORE INTO transparency_decision_traces VALUES (?,?,?,?,?,?,current_timestamp,TRUE)",
        [decision_id, cutoff, secid, final, source_snapshot, json.dumps(summary, default=str)],
    )
    passport = instrument_data_passport(con, secid)
    used_map = {
        "Technical": passport["price"]["rows"] > 0,
        "Liquidity": passport["price"]["rows"] > 0,
        "Fundamentals": passport["fundamentals"]["rows"] > 0,
        "Dividend": passport["dividends"]["rows"] > 0,
        "Portfolio concentration": bool(row),
        "Portfolio risk contribution": bool(row),
    }
    for block in BLOCKS:
        used = used_map.get(
            block, block in {"Market regime", "Breadth", "Volatility", "Drawdown", "Rates", "FX", "Sector"}
        )
        reason = (
            "used by existing rule-based intelligence"
            if used
            else "excluded: unavailable, insufficient or not validated"
        )
        status = (
            "supporting"
            if used and block in {"Fundamentals", "Dividend"}
            else "opposing"
            if used and block in {"Market regime", "Volatility"}
            else "neutral"
            if used
            else "excluded"
        )
        evidence_id = hashlib.sha256(f"{decision_id}:{block}".encode()).hexdigest()[:24]
        con.execute(
            "INSERT OR IGNORE INTO transparency_decision_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                decision_id,
                block,
                status,
                None,
                "limited" if used else "insufficient",
                "up" if status == "supporting" else "down" if status == "opposing" else "neutral",
                used,
                None,
                "see dataset freshness",
                reason,
                json.dumps([evidence_id]),
            ],
        )
        con.execute(
            "INSERT OR IGNORE INTO transparency_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                evidence_id,
                decision_id,
                block,
                "MOEX/CBR/issuer/internal" if used else "not available",
                str(passport["price"]["first"] or ""),
                str(passport["price"]["latest"] or ""),
                None,
                passport["price"]["rows"] if used else 0,
                used,
                None if used else reason,
                None,
                None,
                None,
                datetime.now(),
                datetime.now(),
                "transparency-v1",
            ],
        )
    return {
        "decision_id": decision_id,
        "cutoff": cutoff,
        "secid": secid,
        "final_status": final,
        "blocks_checked": len(BLOCKS),
        "blocks_used": sum(
            used_map.get(
                b, b in {"Market regime", "Breadth", "Volatility", "Drawdown", "Rates", "FX", "Sector"}
            )
            for b in BLOCKS
        ),
        "summary": summary,
    }


def explain_current_decision(con: Any, secid: str) -> dict[str, Any]:
    trace = build_decision_trace(con, secid)
    rows = con.execute(
        """SELECT block_name,block_status,used_in_final,reason
        FROM transparency_decision_blocks WHERE decision_id=? ORDER BY block_name""",
        [trace["decision_id"]],
    ).fetchall()
    trace["used"] = [r[0] for r in rows if r[2]]
    trace["excluded"] = [{"block": r[0], "reason": r[3]} for r in rows if not r[2]]
    return trace


def update_receipt(con: Any, update_id: str | None = None) -> dict[str, Any]:
    ensure_schema(con)
    tables = _tables(con)
    source = None
    for table in ("daily_update_runs", "actual_update_runs", "load_runs"):
        if table in tables:
            source = table
            break
    if not source:
        return {"status": "no_update_history", "message": "История обновлений пока отсутствует"}
    where = " WHERE run_id=?" if update_id and "run_id" in _columns(con, source) else ""
    params = [update_id] if where else []
    row = con.execute(f"SELECT * FROM {source}{where} ORDER BY 1 DESC LIMIT 1", params).fetchone()
    names = [d[0] for d in con.description]
    payload = dict(zip(names, row, strict=False)) if row else {}
    if row and source == "daily_update_runs":
        request_rows = (
            con.execute(
                """SELECT dataset,source,requests,rows_inserted,rows_revised,status,error,
                duration_seconds FROM daily_update_requests WHERE run_id=? ORDER BY step""",
                [payload["run_id"]],
            ).fetchall()
            if "daily_update_requests" in tables
            else []
        )
        sources = [
            dict(
                zip(
                    (
                        "dataset",
                        "source",
                        "requests",
                        "new_rows",
                        "revised_rows",
                        "status",
                        "error",
                        "duration_seconds",
                    ),
                    item,
                    strict=False,
                )
            )
            for item in request_rows
        ]
        con.execute(
            """INSERT OR IGNORE INTO transparency_update_receipts VALUES
            (?,?,?,?,?,?,?,?,?,current_timestamp,TRUE)""",
            [
                payload["run_id"],
                payload.get("started_at"),
                payload.get("finished_at"),
                payload.get("update_type"),
                payload.get("finished_at").date() if payload.get("finished_at") else None,
                payload.get("duration_seconds"),
                json.dumps(sources, default=str),
                json.dumps(
                    {
                        "sources_checked": payload.get("sources_checked", 0),
                        "http_requests": payload.get("http_requests", 0),
                        "new_rows": payload.get("rows_inserted", 0),
                        "revised_rows": payload.get("rows_revised", 0),
                        "errors": payload.get("errors", 0),
                        "forecasts_created": payload.get("new_forecasts", 0),
                        "forecasts_matured": payload.get("matured_forecasts", 0),
                    }
                ),
                payload.get("status"),
            ],
        )
        payload["sources"] = sources
    return {
        "status": "available" if row else "no_update_history",
        "source_table": source,
        "receipt": payload,
    }
