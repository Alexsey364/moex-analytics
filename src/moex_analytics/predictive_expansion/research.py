"""Frozen cross-sectional samples and evidence-only Stage 30 data-value ledger."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .schema import DDL

FAMILIES = (
    "broad_universe", "liquidity", "numtrades", "breadth_3", "fundamentals",
    "sector", "rates", "fx", "commodities", "futures", "options_pilot",
    "intraday_pilot",
)


def ensure_schema(con) -> None:
    con.execute(DDL)


def build_cross_sectional_dataset(con, *, minimum_securities: int = 1000) -> dict:
    """Freeze date-by-instrument rows only after the mandated universe checkpoint."""
    ensure_schema(con)
    securities = con.execute("SELECT count(DISTINCT secid) FROM moex_equity_eod").fetchone()[0]
    created = datetime.now(UTC)
    run_id = hashlib.sha256(f"cross-section:{created.isoformat()}".encode()).hexdigest()[:20]
    if securities < minimum_securities:
        details = {"required": minimum_securities, "available": securities,
                   "reason": "universe_checkpoint_not_reached"}
        con.execute(
            """INSERT INTO stage30_cross_sectional_runs VALUES
            (?,?,'gated',?,0,NULL,NULL,'stage30-market-v1','forward-returns-v1',TRUE,0,?)""",
            [run_id, created, securities, json.dumps(details)],
        )
        return {"run_id": run_id, "status": "gated", **details, "production_changes": 0}
    con.execute(
        """INSERT INTO stage30_cross_sectional_dataset
        WITH base AS (
          SELECT l.trade_date,l.secid,e.close,l.turnover_20,l.liquidity_percentile,
          lead(e.close,5) OVER(PARTITION BY l.secid ORDER BY l.trade_date)/e.close-1 r5,
          lead(e.close,20) OVER(PARTITION BY l.secid ORDER BY l.trade_date)/e.close-1 r20,
          lead(e.close,60) OVER(PARTITION BY l.secid ORDER BY l.trade_date)/e.close-1 r60,
          lead(e.close,120) OVER(PARTITION BY l.secid ORDER BY l.trade_date)/e.close-1 r120
          FROM stage30_liquidity_daily l JOIN moex_equity_eod e
          ON e.trade_date=l.trade_date AND e.secid=l.secid AND e.boardid=l.boardid
        ), ranked AS (
          SELECT *,r5-avg(r5) OVER(PARTITION BY trade_date) x5,
          r20-avg(r20) OVER(PARTITION BY trade_date) x20,
          r60-avg(r60) OVER(PARTITION BY trade_date) x60,
          r120-avg(r120) OVER(PARTITION BY trade_date) x120,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY r5) q5,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY r20) q20,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY r60) q60,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY r120) q120 FROM base
        ) SELECT ?,trade_date,secid,close,turnover_20,liquidity_percentile,
        r5,r20,r60,r120,x5,x20,x60,x120,q5,q20,q60,q120 FROM ranked""", [run_id]
    )
    rows, start, end = con.execute(
        "SELECT count(*),min(trade_date),max(trade_date) FROM stage30_cross_sectional_dataset WHERE run_id=?",
        [run_id],
    ).fetchone()
    details = {"tradable_on_date": True, "future_membership_used": False,
               "labels_are_forward_only": True}
    con.execute(
        """INSERT INTO stage30_cross_sectional_runs VALUES
        (?,?,'completed',?,?,?,?,?,'forward-returns-v1',TRUE,0,?)""",
        [run_id, created, securities, rows, start, end, "stage30-market-v1", json.dumps(details)],
    )
    return {"run_id": run_id, "status": "completed", "securities": securities,
            "rows": rows, "date_from": start, "date_to": end, **details,
            "production_changes": 0}


def _existing_evidence(con, family: str) -> list[tuple]:
    """Read only existing OOS evidence; absence remains insufficient."""
    mapping = {
        "futures": "futures", "options_pilot": "options",
        "intraday_pilot": "intraday", "rates": "zcyc", "breadth_3": "breadth",
    }
    block = mapping.get(family, family)
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='predictive_ablation_results'"
    ).fetchone()[0]:
        return []
    return con.execute(
        """SELECT horizon,improvement,bootstrap_low,bootstrap_high,status
        FROM predictive_ablation_results WHERE lower(block_name)=?""", [block]
    ).fetchall()


def measure_data_value(con) -> dict:
    """Populate the ledger without treating availability as predictive value."""
    ensure_schema(con)
    created = datetime.now(UTC)
    run_id = hashlib.sha256(f"ablation:{created.isoformat()}".encode()).hexdigest()[:20]
    securities = con.execute("SELECT count(DISTINCT secid) FROM moex_equity_eod").fetchone()[0]
    counts = {"useful": 0, "experimental": 0, "rejected": 0, "insufficient_sample": 0}
    rows = []
    for family in FAMILIES:
        evidence = _existing_evidence(con, family)
        confirmed = [row for row in evidence if row[4] == "useful" and row[2] is not None and row[2] > 0]
        rejected = evidence and all(row[1] is not None and row[1] <= 0 for row in evidence)
        status = "useful" if confirmed else "rejected" if rejected else (
            "experimental" if evidence else "insufficient_sample"
        )
        counts[status] += 1
        effect = max((float(row[1]) for row in evidence if row[1] is not None), default=None)
        horizons = [int(row[0]) for row in evidence if row[1] is not None and row[1] > 0]
        evidence_text = "existing same-sample OOS result" if evidence else "no same-sample OOS result"
        con.execute(
            """INSERT OR REPLACE INTO stage30_data_value_ledger VALUES
            (?,?,?,?,?,?,?,?,?, ?,current_timestamp)""",
            [run_id, family, 0, 0, 0.0, effect, json.dumps(horizons), json.dumps([]),
             status, evidence_text],
        )
        rows.append({"family": family, "oos_effect": effect, "horizons": horizons,
                     "status": status, "evidence": evidence_text})
    overall = "completed" if securities >= 1000 else "partial_universe_below_target"
    con.execute(
        """INSERT INTO stage30_ablation_runs VALUES
        (?,? ,?,?,?,?,?,?,?,?,?)""",
        [run_id, created, overall, securities, len(rows), counts["useful"],
         counts["experimental"], counts["rejected"], counts["insufficient_sample"], 0,
         json.dumps({"families": rows})],
    )
    return {"run_id": run_id, "status": overall, "securities": securities,
            "families": rows, **counts, "production_changes": 0}


def research_status(con) -> dict:
    ensure_schema(con)
    return {
        "cross_sectional": con.execute(
            "SELECT * FROM stage30_cross_sectional_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone(),
        "ablation": con.execute(
            "SELECT * FROM stage30_ablation_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone(),
        "ledger": con.execute(
            "SELECT dataset_family,oos_effect,status,evidence FROM stage30_data_value_ledger "
            "WHERE run_id=(SELECT run_id FROM stage30_ablation_runs ORDER BY created_at DESC LIMIT 1)"
        ).fetchall(),
    }
