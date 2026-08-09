"""Truthful PIT fundamental and dividend coverage for Stage 30 research."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from moex_analytics.fundamentals.generic import ensure_generic_schema
from moex_analytics.portfolio_research.intelligence import backfill_official_fundamentals

from .schema import DDL

ISSUER_GROUPS = {
    "SBER": ("SBER", "SBERP"),
    "LKOH": ("LKOH",),
    "MTSS": ("MTSS",),
    "MOEX": ("MOEX",),
    "PHOR": ("PHOR",),
    "TATN": ("TATN", "TATNP"),
    "TRNFP": ("TRNFP",),
    "LSNG": ("LSNG", "LSNGP"),
    "X5": ("X5", "FIVE"),
}


def ensure_schema(con) -> None:
    con.execute(DDL)
    ensure_generic_schema(con)


def _build_coverage(con) -> list[dict]:
    con.execute("DELETE FROM stage30_fundamental_coverage")
    result = []
    for issuer, secids in ISSUER_GROUPS.items():
        marks = ",".join("?" for _ in secids)
        values = con.execute(
            f"""SELECT count(*),count(DISTINCT period_end),min(period_end),max(period_end),
            max(publication_date) FROM issuer_fundamental_values
            WHERE validation_status='validated' AND (issuer=? OR secid IN ({marks}))""",
            [issuer, *secids],
        ).fetchone()
        documents = con.execute(
            "SELECT count(*) FROM issuer_fundamental_documents WHERE issuer=?", [issuer]
        ).fetchone()[0]
        observations, periods, earliest, latest, publication = values
        status = (
            "validated_deep" if periods >= 10 else "validated_minimum" if periods >= 5
            else "insufficient_sample" if periods else "source_gap"
        )
        limitation = None if periods >= 5 else (
            "fewer than five validated PIT periods" if periods else
            "no parsed official document; values were not synthesized"
        )
        row = {
            "issuer": issuer,
            "secids": secids,
            "documents": int(documents),
            "observations": int(observations),
            "periods": int(periods),
            "earliest": earliest,
            "latest": latest,
            "publication": publication,
            "status": status,
            "limitation": limitation,
        }
        con.execute(
            """INSERT INTO stage30_fundamental_coverage VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
            [issuer, json.dumps(secids), documents, observations, periods, earliest, latest,
             publication, "measured", "validated" if observations else "missing", status,
             limitation],
        )
        result.append(row)
    return result


def _build_dividend_pit(con) -> int:
    """Derive yields only once the announcement is observable; never backdate knowledge."""
    con.execute("DELETE FROM stage30_dividend_pit")
    con.execute(
        """INSERT INTO stage30_dividend_pit
        WITH events AS (
          SELECT canonical_secid secid,registry_close_date record_date,
          coalesce(declared_date,registry_close_date) publication_date,
          coalesce(declared_date,registry_close_date)::TIMESTAMP available_from,
          dividend_per_share dps,currency,payment_date,source,
          lag(dividend_per_share) OVER(PARTITION BY canonical_secid ORDER BY registry_close_date) prior_dps
          FROM dividends WHERE dividend_per_share>0 AND registry_close_date IS NOT NULL
        ), priced AS (
          SELECT e.*,(SELECT close FROM daily_prices p WHERE p.secid=e.secid
          AND p.trade_date>=e.publication_date ORDER BY p.trade_date LIMIT 1) px
          FROM events e
        )
        SELECT secid,record_date,publication_date,available_from,dps,currency,px,
        dps/nullif(px,0),dps/nullif(prior_dps,0)-1,
        prior_dps IS NOT NULL AND dps<prior_dps,
        CASE WHEN payment_date IS NOT NULL AND payment_date<=current_date THEN 'paid'
             WHEN payment_date IS NOT NULL THEN 'scheduled' ELSE 'unknown' END,
        CASE WHEN right(secid,1)='P' THEN 'preferred' ELSE 'ordinary' END,
        source,CASE WHEN publication_date<=record_date THEN 'pit_valid'
                    ELSE 'invalid_date_order' END,current_timestamp FROM priced"""
    )
    return con.execute("SELECT count(*) FROM stage30_dividend_pit").fetchone()[0]


def deepen_pit_fundamentals(con, *, download: bool = True) -> dict:
    """Run validated adapters, preserve failures, then measure usable coverage."""
    ensure_schema(con)
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"fundamentals:{started.isoformat()}".encode()).hexdigest()[:20]
    errors = {}
    downloads = {"status": "skipped"}
    if download:
        try:
            downloads = backfill_official_fundamentals(con)
        except Exception as exc:  # source failure is evidence, never a reason to invent data
            errors["fundamental_backfill"] = str(exc)
    coverage = _build_coverage(con)
    dividends = _build_dividend_pit(con)
    documents = sum(row["documents"] for row in coverage)
    observations = sum(row["observations"] for row in coverage)
    periods = sum(row["periods"] for row in coverage)
    status = "completed_with_source_gaps" if errors or any(
        row["status"] == "source_gap" for row in coverage
    ) else "completed"
    details = {"downloads": downloads, "coverage": coverage, "errors": errors,
               "five_x5_merged": False, "pit_required": True}
    con.execute(
        """INSERT INTO stage30_fundamental_runs VALUES
        (?, ?,current_timestamp,?,?,?,?,?,?,?,?,0)""",
        [run_id, started, status, len(coverage), documents, observations, periods,
         dividends, len(errors), json.dumps(details, default=str)],
    )
    return {"run_id": run_id, "status": status, "issuers": len(coverage),
            "documents": documents, "validated_observations": observations,
            "validated_periods": periods, "dividends": dividends, "coverage": coverage,
            "errors": errors, "production_changes": 0}


def fundamental_status(con) -> dict:
    ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,issuers,documents,validated_observations,validated_periods,
        dividends,errors,production_changes FROM stage30_fundamental_runs
        ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    return {"latest": latest, "coverage": con.execute(
        "SELECT issuer,validated_observations,validated_periods,coverage_status "
        "FROM stage30_fundamental_coverage ORDER BY issuer"
    ).fetchall()}
