"""Reproducible coverage audit and conservative historical-data backfill orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.database import database_path

from .schema import DDL

PORTFOLIO_GROUPS = {
    "X5": ("X5", "FIVE"),
    "SBER": ("SBER", "SBERP"),
    "LKOH": ("LKOH",),
    "LSNG": ("LSNG", "LSNGP"),
    "MTSS": ("MTSS",),
    "TRNFP": ("TRNFP",),
    "TATN": ("TATN", "TATNP"),
    "PHOR": ("PHOR",),
    "MOEX": ("MOEX",),
}
DATASET_FAMILIES = (
    "EOD prices", "total return", "dividends", "corporate actions",
    "fundamentals RAS", "fundamentals IFRS", "operating metrics",
    "sector membership", "sector indices", "broad universe", "delisted universe",
    "FX", "oil", "commodity", "rates", "ZCYC", "RUONIA", "futures",
    "futures OI", "basis", "options", "implied volatility", "consensus",
    "fund flows", "news/events", "liquidity", "intraday", "order log/order book",
)
HORIZONS = (1, 5, 20, 60, 120, 250)
VERSION = "historical-coverage-v1"


def ensure_schema(con) -> None:
    con.execute(DDL)


def table_exists(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [name]).fetchone()[0])


def _table_stats(con, table: str, date_column: str, where: str = "", params=None) -> dict:
    if not table_exists(con, table):
        return {"count": 0, "earliest": None, "latest": None}
    clause = f" WHERE {where}" if where else ""
    count, earliest, latest = con.execute(
        f"SELECT count(*), min({date_column}), max({date_column}) FROM {table}{clause}", params or []
    ).fetchone()
    return {"count": int(count), "earliest": earliest, "latest": latest}


def priority_score(*, relevance: int, depth_gain: int, pit: int, predictive_value: int,
                   cross_section: int, cost: int, complexity: int, license_risk: int) -> dict:
    """Ordinal score: inputs are transparent 0..3 judgements, not false precision."""
    values = (relevance, depth_gain, pit, predictive_value, cross_section, cost, complexity, license_risk)
    if any(not 0 <= value <= 3 for value in values):
        raise ValueError("priority inputs must be integers from 0 to 3")
    positive = relevance * 3 + depth_gain * 2 + pit * 3 + predictive_value * 3 + cross_section * 2
    penalty = cost * 2 + complexity + license_risk * 3
    score = positive - penalty
    status = "critical" if score >= 27 else "high" if score >= 20 else "medium" if score >= 12 else "low"
    if cost >= 2 and status in {"critical", "high"}:
        status = "paid_optional"
    return {"score": score, "status": status, "components": values}


def pit_integrity_score(*, has_available_from: bool, publication_order_valid: bool,
                        revision_support: bool, duplicates: int, impossible_dates: int,
                        stale_ratio: float, frequency_match: bool) -> float:
    score = 100.0
    score -= 25 if not has_available_from else 0
    score -= 30 if not publication_order_valid else 0
    score -= 10 if not revision_support else 0
    score -= min(15, duplicates * 3)
    score -= min(15, impossible_dates * 5)
    score -= min(10, max(0.0, stale_ratio) * 20)
    score -= 10 if not frequency_match else 0
    return max(0.0, round(score, 1))


def validate_futures_units(spec: dict) -> dict:
    required = ("spot_scale", "futures_scale", "multiplier", "lot", "currency", "expiration")
    valid = all(spec.get(key) not in (None, "", 0) for key in required)
    return {**spec, "units_validated": valid, "basis_enabled": valid}


def detect_dividend_duplicates(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    keys = ["secid", "record_date"]
    return frame[frame.duplicated(keys, keep=False)].sort_values(keys)


def validate_sector_membership(frame: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    for secid, group in frame.groupby("secid"):
        ordered = group.sort_values("valid_from")
        previous_end = None
        for row in ordered.itertuples():
            if row.valid_to is not None and pd.Timestamp(row.valid_to) < pd.Timestamp(row.valid_from):
                issues.append(f"{secid}: invalid interval")
            if previous_end is not None and pd.Timestamp(row.valid_from) <= pd.Timestamp(previous_end):
                issues.append(f"{secid}: overlapping intervals")
            previous_end = row.valid_to
    return issues


def same_sample_ablation(baseline: pd.DataFrame, candidate: pd.DataFrame, key_columns: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Force both research arms onto identical dated observations."""
    common = baseline[key_columns].merge(candidate[key_columns], on=key_columns).drop_duplicates()
    return baseline.merge(common, on=key_columns), candidate.merge(common, on=key_columns)


def _coverage_source(con, instrument: str, family: str) -> tuple[dict, str, str, str]:
    secids = PORTFOLIO_GROUPS[instrument]
    marks = ",".join("?" for _ in secids)
    if family == "EOD prices":
        return _table_stats(con, "canonical_daily_prices", "trade_date", f"canonical_secid IN ({marks})", list(secids)), "MOEX ISS", "official/free", "daily"
    if family == "total return":
        return _table_stats(con, "daily_returns", "trade_date", f"canonical_secid IN ({marks})", list(secids)), "derived from MOEX/dividends", "derived/free", "daily"
    if family == "dividends":
        return _table_stats(con, "dividends", "registry_close_date", f"canonical_secid IN ({marks})", list(secids)), "MOEX ISS", "official/free", "event"
    if family in {"fundamentals RAS", "fundamentals IFRS", "operating metrics"}:
        standard = "RAS" if family.endswith("RAS") else "IFRS" if family.endswith("IFRS") else None
        where = f"secid IN ({marks})" + (" AND upper(accounting_standard)=?" if standard else "")
        params = [*secids, standard] if standard else list(secids)
        return _table_stats(con, "fundamental_observations", "period_end", where, params), "issuer disclosures", "official/free", "quarterly"
    if family == "intraday":
        return _table_stats(con, "intraday_candles", "begin", f"secid IN ({marks})", list(secids)), "MOEX ISS", "official/free", "intraday"
    if family == "futures":
        return _table_stats(con, "deep_sber_futures_daily", "trade_date"), "MOEX ISS", "official/free", "daily"
    if family == "futures OI":
        return _table_stats(con, "deep_sber_futures_daily", "trade_date", "open_interest IS NOT NULL"), "MOEX ISS", "official/free", "daily"
    if family == "ZCYC":
        return _table_stats(con, "deep_zcyc_archive", "observation_date"), "Bank of Russia", "official/free", "daily"
    if family == "broad universe":
        return _table_stats(con, "historical_universe_membership", "trade_date"), "MOEX ISS", "official/free", "daily"
    if family == "delisted universe":
        return _table_stats(con, "historical_equity_universe", "last_trade", "is_traded=false"), "MOEX ISS", "official/free", "event"
    if family == "liquidity":
        return _table_stats(con, "canonical_daily_prices", "trade_date", f"canonical_secid IN ({marks}) AND value IS NOT NULL", list(secids)), "derived from MOEX", "derived/free", "daily"
    return {"count": 0, "earliest": None, "latest": None}, _family_source(family), _family_access(family), "unknown"


def _family_source(family: str) -> str:
    if family in {"FX", "rates", "RUONIA"}: return "Bank of Russia / MOEX"
    if family in {"oil", "commodity"}: return "provider catalog required"
    if family in {"options", "implied volatility", "futures", "futures OI", "basis"}: return "MOEX ISS"
    if family in {"consensus", "fund flows", "order log/order book"}: return "licensed provider required"
    return "official issuer/MOEX source not yet validated"


def _family_access(family: str) -> str:
    return "paid/restricted" if family in {"consensus", "fund flows", "order log/order book", "implied volatility"} else "unknown/free-candidate"


def build_coverage_matrix(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM historical_data_coverage")
    rows = []
    for instrument in PORTFOLIO_GROUPS:
        for family in DATASET_FAMILIES:
            stats, source, access, frequency = _coverage_source(con, instrument, family)
            count = stats["count"]
            status = "missing" if count == 0 else "complete" if family == "EOD prices" and count >= 1000 else "partial"
            paid = access == "paid/restricted"
            priority = priority_score(
                relevance=3, depth_gain=3 if count == 0 else 1, pit=3 if "official" in access else 1,
                predictive_value=3 if family in {"fundamentals IFRS", "EOD prices", "liquidity", "FX", "oil"} else 2,
                cross_section=3 if family not in {"operating metrics"} else 1,
                cost=3 if paid else 0, complexity=2 if family in {"options", "intraday", "order log/order book"} else 1,
                license_risk=3 if paid else 0,
            )["status"]
            pit = "validated" if count and family in {"EOD prices", "total return", "ZCYC"} else "unverified" if count else "missing"
            integrity = pit_integrity_score(has_available_from=pit == "validated", publication_order_valid=True,
                                            revision_support=family in {"fundamentals RAS", "fundamentals IFRS", "ZCYC"},
                                            duplicates=0, impossible_dates=0, stale_ratio=0, frequency_match=frequency != "unknown")
            rows.append((instrument, family, f"{instrument}:{family}", source, access, "source-specific; verify before redistribution",
                         stats["earliest"], stats["latest"], count, frequency, 1.0 if status == "complete" else 0.5 if count else 0.0,
                         pit, family in {"fundamentals RAS", "fundamentals IFRS", "ZCYC"}, family in {"EOD prices", "total return"}, status,
                         priority, "high" if family in {"fundamentals IFRS", "EOD prices", "liquidity", "FX", "oil"} else "uncertain",
                         "paid/unknown" if paid else "free", "license/access" if paid else "not loaded" if not count else "",
                         "catalog_only" if paid else "backfill_official" if not count else "validate_and_extend", integrity, datetime.now(UTC)))
    con.executemany("INSERT INTO historical_data_coverage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return {"rows": len(rows), "complete": sum(r[14] == "complete" for r in rows), "partial": sum(r[14] == "partial" for r in rows), "missing": sum(r[14] == "missing" for r in rows)}


def audit_dividends(con) -> dict:
    ensure_schema(con)
    if not table_exists(con, "dividends"):
        return {"rows": 0, "duplicates": 0}
    frame = con.execute("SELECT canonical_secid secid, registry_close_date record_date, dividend_per_share dps, currency, source FROM dividends").df()
    duplicates = detect_dividend_duplicates(frame)
    invalid = frame[(frame.dps <= 0) | frame.record_date.isna()]
    return {"rows": len(frame), "duplicates": len(duplicates), "invalid": len(invalid)}


def audit_corporate_actions(con) -> dict:
    ensure_schema(con)
    x5 = con.execute("SELECT count(*) FROM historical_corporate_actions WHERE issuer_group='X5' AND validation_status='validated'").fetchone()[0]
    return {"validated_actions": int(con.execute("SELECT count(*) FROM historical_corporate_actions WHERE validation_status='validated'").fetchone()[0]), "five_x5_mapping_validated": bool(x5), "warning": "FIVE and X5 are never mechanically joined"}


def backfill_issuer_fundamentals(con) -> dict:
    ensure_schema(con)
    result = {}
    for group, secids in PORTFOLIO_GROUPS.items():
        marks = ",".join("?" for _ in secids)
        stats = _table_stats(con, "fundamental_observations", "period_end", f"secid IN ({marks})", list(secids))
        result[group] = stats
    return {"downloaded_rows": 0, "reason": "only already validated official documents retained; no synthetic backfill", "issuers": result}


def backfill_historical_universe(con) -> dict:
    ensure_schema(con)
    universe = _table_stats(con, "historical_equity_universe", "first_trade")
    membership = _table_stats(con, "historical_universe_membership", "trade_date")
    return {"securities": universe["count"], "membership_rows": membership["count"], "survivorship_eliminated": False, "reason": "archive completeness is not proven"}


def backfill_sector_history(con) -> dict:
    ensure_schema(con)
    rows = con.execute("SELECT count(*) FROM historical_sector_membership").fetchone()[0]
    return {"rows": int(rows), "status": "partial" if rows else "missing", "rule": "current classification is not back-propagated"}


def backfill_external_factors(con) -> dict:
    ensure_schema(con)
    catalog = [
        ("usd_rub", "FX", "Bank of Russia", "official CBR series", "official/free", "free", "publication timestamp", "requires_validation", "catalogued", "RUB/USD"),
        ("rub_cny", "FX", "Bank of Russia", "official CBR series", "official/free", "free", "publication timestamp", "requires_validation", "catalogued", "RUB/CNY"),
        ("brent", "oil", "MOEX/provider", "contract series", "source-specific", "free-candidate", "market close", "requires_validation", "catalogued", "continuous units must be validated"),
        ("urals", "oil", "licensed provider", "not selected", "unknown", "paid/restricted", "unknown", "missing", "requires_paid_data", "no safe free PIT source proven"),
        ("fertilizer_proxy", "commodity", "licensed provider", "not selected", "unknown", "paid/restricted", "unknown", "missing", "requires_paid_data", "PHOR proxy"),
    ]
    con.executemany("INSERT OR REPLACE INTO external_factor_catalog VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)", catalog)
    return {"catalogued": len(catalog), "downloaded_rows": 0, "paid_gaps": 2}


def backfill_futures(con) -> dict:
    ensure_schema(con)
    rows = _table_stats(con, "deep_sber_futures_daily", "trade_date")
    validated = con.execute("SELECT count(*) FROM futures_contract_validation WHERE units_validated").fetchone()[0]
    return {"rows": rows["count"], "earliest": rows["earliest"], "latest": rows["latest"], "validated_contracts": int(validated), "basis_policy": "disabled unless units_validated"}


def audit_options_history(con) -> dict:
    ensure_schema(con)
    stats = _table_stats(con, "options_history_coverage", "date_from")
    return {"coverage_rows": stats["count"], "purchase_performed": False, "policy": "catalog_only when deep history is restricted"}


def calculate_pit_integrity(con) -> dict:
    ensure_schema(con)
    rows = con.execute("SELECT dataset_id,pit_integrity_score FROM historical_data_coverage").fetchall()
    return {"datasets": len(rows), "mean_score": round(sum(float(r[1] or 0) for r in rows) / len(rows), 1) if rows else 0.0,
            "validated": sum(float(r[1] or 0) >= 80 for r in rows)}


def run_data_value_ablation(con) -> dict:
    """Register only evidence-bearing existing OOS results; never manufacture metrics."""
    ensure_schema(con)
    existing = con.execute("SELECT count(*) FROM data_value_ablation_results").fetchone()[0]
    return {"rows": int(existing), "measurable_oos_value": [], "status": "insufficient_evidence" if not existing else "available", "production_promotion": False}


def storage_audit(con) -> dict:
    ensure_schema(con)
    def size(path: Path) -> int:
        if not path.exists(): return 0
        if path.is_file(): return path.stat().st_size
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    db_path = database_path()
    values = (size(db_path), size(PROJECT_ROOT / "data/raw"), size(PROJECT_ROOT / "data/processed"), size(PROJECT_ROOT / "data/cache"))
    estimates = (values[0] + values[1] * 2, max(values[0] * 5, 500_000_000), max(values[0] * 10, 1_000_000_000))
    policy = "retain provenance and hashes; deduplicate caches; never delete validated raw sources automatically"
    con.execute("INSERT INTO historical_storage_audit VALUES (current_timestamp,?,?,?,?,?,?,?,?)", [*values, *estimates, policy])
    return {"duckdb_bytes": values[0], "raw_bytes": values[1], "processed_bytes": values[2], "cache_bytes": values[3], "backfill_estimate_bytes": estimates[0], "intraday_estimate_bytes": estimates[1], "options_estimate_bytes": estimates[2], "retention_policy": policy}


def historical_data_status(con) -> dict:
    ensure_schema(con)
    counts = dict(con.execute("SELECT current_status,count(*) FROM historical_data_coverage GROUP BY current_status").fetchall())
    critical = con.execute("SELECT count(*) FROM historical_data_coverage WHERE analytical_priority='critical' AND current_status!='complete'").fetchone()[0]
    paid = con.execute("SELECT count(*) FROM historical_data_coverage WHERE access_class='paid/restricted'").fetchone()[0]
    return {"coverage": counts, "critical_gaps": int(critical), "paid_gaps": int(paid), "production_models_changed": False}


def complete_historical_data_audit(con) -> dict:
    ensure_schema(con)
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"{VERSION}:{started.isoformat()}".encode()).hexdigest()[:16]
    details = {
        "coverage": build_coverage_matrix(con),
        "fundamentals": backfill_issuer_fundamentals(con),
        "universe": backfill_historical_universe(con),
        "sectors": backfill_sector_history(con),
        "external": backfill_external_factors(con),
        "futures": backfill_futures(con),
        "options": audit_options_history(con),
        "corporate_actions": audit_corporate_actions(con),
        "dividends": audit_dividends(con),
        "pit": calculate_pit_integrity(con),
        "ablation": run_data_value_ablation(con),
        "storage": storage_audit(con),
    }
    downloaded = sum(int(block.get("downloaded_rows", 0)) for block in details.values() if isinstance(block, dict))
    con.execute("INSERT INTO historical_audit_runs VALUES (?,?,current_timestamp,?,?,?)", [run_id, started, "completed", downloaded, json.dumps(details, default=str)])
    return {"run_id": run_id, "status": "completed", "downloaded_rows": downloaded, **details, "summary": historical_data_status(con), "production_models_changed": False}
