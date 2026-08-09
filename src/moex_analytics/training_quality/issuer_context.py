"""Point-in-time issuer fundamentals and official MOEX sector context (Stage 35)."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.predictive_expansion.fundamentals import deepen_pit_fundamentals

from .schema import DDL

ISSUERS = {
    "LKOH": (("LKOH",), "moex_oil_gas"),
    "TATN": (("TATN", "TATNP"), "moex_oil_gas"),
    "TRNFP": (("TRNFP",), "moex_transport"),
    "LSNG": (("LSNG", "LSNGP"), "moex_power"),
    "MTSS": (("MTSS",), "moex_consumer"),
    "PHOR": (("PHOR",), "moex_metals"),
    "MOEX": (("MOEX",), "moex_finance"),
    "SBER": (("SBER", "SBERP"), "moex_finance"),
    "X5": (("X5",), "moex_consumer"),
    "FIVE": (("FIVE",), "moex_consumer"),
}
REQUIRED_METRICS = {
    "LKOH": [
        "revenue", "ebitda", "profit", "ocf", "fcf", "capex", "net_debt",
        "production", "refining", "dividends",
    ],
    "TATN": ["revenue", "ebitda", "profit", "fcf", "capex", "net_debt", "production", "preferred_payout"],
    "TRNFP": [
        "revenue", "ebitda", "profit", "ocf", "capex", "debt", "transport_volumes",
        "tariffs", "preferred_rights",
    ],
    "LSNG": ["ras_profit", "capex", "debt", "transmission", "useful_supply", "tariffs", "preferred_formula"],
    "MTSS": ["revenue", "oibda", "margin", "net_debt", "capex", "fcf", "profit", "dividends"],
    "PHOR": ["production", "sales", "revenue", "ebitda", "margin", "net_debt", "fcf", "dividends"],
    "MOEX": [
        "fee_income", "interest_income", "expenses", "profit", "equity", "client_balances",
        "trading_volumes", "dividends",
    ],
    "SBER": [
        "assets", "equity", "net_profit", "roe", "net_interest_income", "fees",
        "cost_of_risk", "dividends",
    ],
    "X5": ["revenue", "ebitda", "margin", "net_debt", "capex", "fcf", "profit", "dividends"],
}


def _fundamental_states(con) -> int:
    con.execute("DELETE FROM issuer_pit_fundamental_states")
    for issuer, (secids, _) in ISSUERS.items():
        marks = ",".join("?" for _ in secids)
        con.execute(
            f"""INSERT INTO issuer_pit_fundamental_states
            WITH calendar AS (
              SELECT DISTINCT trade_date FROM research_adjusted_prices
            ), values AS (
              SELECT coalesce(nullif(issuer,''),?) issuer_group,secid,metric,period_end,
              publication_date,available_from,normalized_value metric_value,unit,source,
              validation_status,row_number() OVER(PARTITION BY secid,metric,available_from
              ORDER BY revision DESC,period_end DESC) revision_rank
              FROM issuer_fundamental_values
              WHERE validation_status='validated' AND (issuer=? OR secid IN ({marks}))
              AND available_from IS NOT NULL
            ), intervals AS (
              SELECT *,lead(available_from) OVER(PARTITION BY secid,metric
              ORDER BY available_from) next_available FROM values WHERE revision_rank=1
            ) SELECT c.trade_date,?,v.secid,v.metric,v.period_end,v.publication_date,
            v.available_from,v.metric_value,v.unit,v.source,v.validation_status
            FROM intervals v JOIN calendar c ON c.trade_date>=CAST(v.available_from AS DATE)
            AND (v.next_available IS NULL OR c.trade_date<CAST(v.next_available AS DATE))""",
            [issuer, issuer, *secids, issuer],
        )
    return con.execute("SELECT count(*) FROM issuer_pit_fundamental_states").fetchone()[0]


def _derived(con) -> int:
    con.execute("DELETE FROM issuer_derived_fundamental_features")
    con.execute(
        """INSERT INTO issuer_derived_fundamental_features
        WITH x AS (
          SELECT *,lag(value) OVER(PARTITION BY issuer_group,metric ORDER BY trade_date) prior_value,
          lag(value,252) OVER(PARTITION BY issuer_group,metric ORDER BY trade_date) prior_year
          FROM issuer_pit_fundamental_states
        ) SELECT trade_date,issuer_group,count(DISTINCT period_end),
        avg(value/nullif(prior_year,0)-1) FILTER(
          WHERE regexp_matches(lower(metric),'revenue|profit|ebitda|oibda|production|sales')),
        avg(value-prior_value) FILTER(WHERE regexp_matches(lower(metric),'margin')),
        avg(value/nullif(prior_year,0)-1) FILTER(WHERE regexp_matches(lower(metric),'free_cash|fcf')),
        -avg(value/nullif(prior_year,0)-1) FILTER(WHERE regexp_matches(lower(metric),'net_debt|debt')),
        avg(value-prior_value) FILTER(WHERE regexp_matches(lower(metric),'roe')),
        avg(value/nullif(prior_year,0)-1) FILTER(WHERE regexp_matches(lower(metric),'dividend|payout')),
        NULL,CASE WHEN count(DISTINCT period_end)>=5 THEN 'validated_multi_period'
                  ELSE 'insufficient_sample' END
        FROM x GROUP BY trade_date,issuer_group"""
    )
    return con.execute("SELECT count(*) FROM issuer_derived_fundamental_features").fetchone()[0]


def _sector(con) -> int:
    con.execute("DELETE FROM issuer_sector_context_daily")
    for issuer, (secids, series) in ISSUERS.items():
        for secid in secids:
            con.execute(
                """INSERT INTO issuer_sector_context_daily
                WITH sector AS (
                  SELECT CAST(available_from AS DATE) available_date,value,
                  value/lag(value,20) OVER(ORDER BY observation_date)-1 r20,
                  value/lag(value,60) OVER(ORDER BY observation_date)-1 r60,
                  stddev_samp(ln(value/lag_value)) OVER(ORDER BY observation_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) vol60,
                  value/max(value) OVER(ORDER BY observation_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)-1 dd
                  FROM (SELECT *,lag(value) OVER(ORDER BY observation_date) lag_value
                    FROM macro_observations WHERE series_id=?)
                ), asset AS (
                  SELECT trade_date,close price,
                  close/lag(close,20)
                    OVER(ORDER BY trade_date)-1 r20,
                  close/lag(close,60)
                    OVER(ORDER BY trade_date)-1 r60
                  FROM moex_equity_eod WHERE secid=? AND close>0
                  QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY value DESC NULLS LAST)=1
                ) SELECT a.trade_date,?,?,?,s.value,s.r20,s.r60,s.vol60,s.dd,
                a.r20-s.r20,a.r60-s.r60,'MOEX ISS','available_after_session_close'
                FROM asset a ASOF JOIN sector s ON a.trade_date>=s.available_date""",
                [series, secid, issuer, secid, series],
            )
    return con.execute("SELECT count(*) FROM issuer_sector_context_daily").fetchone()[0]


def build_issuer_context(con, *, download: bool = True) -> dict:
    con.execute(DDL)
    started = datetime.now(UTC)
    clock = time.perf_counter()
    fundamentals = deepen_pit_fundamentals(con, download=download)
    states = _fundamental_states(con)
    derived = _derived(con)
    sector = _sector(con)
    coverage = con.execute(
        """SELECT issuer,validated_periods,coverage_status
        FROM stage30_fundamental_coverage ORDER BY issuer"""
    ).fetchall()
    five = sum(int(row[1] >= 5) for row in coverage)
    run_id = hashlib.sha256(f"stage35:{started.isoformat()}".encode()).hexdigest()[:20]
    runtime = time.perf_counter() - clock
    details = {"coverage": coverage, "x5_five_merged": False,
               "future_reports_used": False, "sector_membership_reconstructed": False,
               "fundamental_download": fundamentals}
    review_path = PROJECT_ROOT / "data" / "review" / "stage35_issuer_fundamentals.local.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps({
        "generated_at": started.isoformat(),
        "policy": "official PIT documents only; no synthetic values",
        "issuers": [{"issuer": issuer, "validated_periods": periods, "status": status,
                     "required_metrics": REQUIRED_METRICS.get(issuer, []),
                     "review_action": (
                         "locate and parse archived official reports"
                         if periods < 5 else "verify revisions"
                     )}
                    for issuer, periods, status in coverage],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    details["manual_review_path"] = str(review_path.relative_to(PROJECT_ROOT))
    con.execute(
        """INSERT INTO issuer_context_runs VALUES
        (?,?,current_timestamp,?,?,?,?,?,?,?,?,0,?)""",
        [run_id, started, fundamentals["status"], len(ISSUERS), states, derived, sector,
         five, 0, runtime, json.dumps(details, default=str)],
    )
    return {"run_id": run_id, "status": fundamentals["status"], "coverage": coverage,
            "fundamental_state_rows": states, "derived_rows": derived,
            "sector_rows": sector, "issuers_five_periods": five, "requests": 0,
            "runtime_seconds": runtime, "production_changes": 0, **details}


def issuer_context_status(con) -> dict:
    con.execute(DDL)
    return {"latest": con.execute(
        "SELECT * FROM issuer_context_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone(), "coverage": con.execute(
        "SELECT issuer,validated_periods,coverage_status FROM stage30_fundamental_coverage ORDER BY 1"
    ).fetchall(), "sector": con.execute(
        """SELECT issuer_group,sector_series,count(*),min(trade_date),max(trade_date)
        FROM issuer_sector_context_daily GROUP BY 1,2 ORDER BY 1"""
    ).fetchall()}
