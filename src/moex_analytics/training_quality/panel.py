"""Frozen survivorship-safe high-quality historical training panel (Stage 32)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .corporate_actions import POLICY_VERSION
from .schema import DDL

HORIZON_MINIMUM = {5: 252, 20: 504, 60: 756, 120: 1000, 250: 1500}
LIQUIDITY_MINIMUM_RUB = 100_000.0
PANEL_VERSION = "clean-panel-v1"


def ensure_schema(con) -> None:
    con.execute(DDL)


def _issuer_case() -> str:
    return """CASE WHEN secid IN ('SBER','SBERP') THEN 'SBER'
    WHEN secid IN ('TATN','TATNP') THEN 'TATN'
    WHEN secid IN ('LSNG','LSNGP') THEN 'LSNG'
    WHEN secid IN ('X5','FIVE') THEN secid ELSE secid END"""


def _eligibility(con, version: str) -> int:
    con.execute("DELETE FROM historical_training_eligibility WHERE dataset_version=?", [version])
    for horizon, minimum in HORIZON_MINIMUM.items():
        con.execute(
            f"""INSERT INTO historical_training_eligibility
            WITH base AS (
              SELECT p.trade_date,p.secid,q.training_tier,l.turnover_20,
              count(*) OVER(PARTITION BY p.secid ORDER BY p.trade_date) history_rows,
              median(l.turnover_20) OVER(PARTITION BY p.secid ORDER BY p.trade_date
                ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) med_turnover,
              EXISTS(SELECT 1 FROM corporate_action_candidate_episodes e
                WHERE e.secid=p.secid AND e.review_status!='auto_validated'
                AND p.trade_date BETWEEN e.date_from-INTERVAL {horizon} DAY
                                     AND e.date_to+INTERVAL {horizon} DAY) uncertain
              FROM research_adjusted_prices p JOIN historical_quality_v2 q USING(secid)
              JOIN stage30_liquidity_daily l USING(trade_date,secid,boardid)
              WHERE p.version=? AND q.training_tier IN ('A','B') AND p.raw_price>0
            ) SELECT ?,trade_date,secid,?,training_tier,
            CASE WHEN med_turnover>=10000000 THEN 'high'
                 WHEN med_turnover>=? THEN 'eligible' ELSE 'micro_liquid' END,
            history_rows,med_turnover,
            history_rows>=? AND med_turnover>=? AND NOT uncertain,
            CASE WHEN history_rows<? THEN 'minimum_history'
                 WHEN med_turnover<? THEN 'excluded_low_liquidity'
                 WHEN uncertain THEN 'unresolved_corporate_action_window' ELSE NULL END,
            {_issuer_case()} FROM base""",
            [POLICY_VERSION, version, horizon, LIQUIDITY_MINIMUM_RUB, minimum,
             LIQUIDITY_MINIMUM_RUB, minimum, LIQUIDITY_MINIMUM_RUB],
        )
    return con.execute(
        "SELECT count(*) FROM historical_training_eligibility WHERE dataset_version=?", [version]
    ).fetchone()[0]


def _panel(con, version: str) -> int:
    con.execute("DELETE FROM historical_training_panel WHERE dataset_version=?", [version])
    con.execute(
        """INSERT INTO historical_training_panel
        WITH eligible AS (
          SELECT trade_date,secid,any_value(quality_tier) quality_tier,
          any_value(liquidity_tier) liquidity_tier
          FROM historical_training_eligibility WHERE dataset_version=? AND eligible
          GROUP BY 1,2
        ), base AS (
          SELECT p.trade_date,p.secid,e.quality_tier,e.liquidity_tier,
          NOT u.is_traded currently_inactive,p.research_adjusted_price price,l.return_1d,
          l.turnover_20,l.liquidity_percentile,
          (b.advancers-b.decliners)::DOUBLE/nullif(b.number_tradable,0) breadth_balance,
          ms.state_label market_state,
          lead(p.research_adjusted_price,5) OVER w/p.research_adjusted_price-1 t5,
          lead(p.research_adjusted_price,20) OVER w/p.research_adjusted_price-1 t20,
          lead(p.research_adjusted_price,60) OVER w/p.research_adjusted_price-1 t60,
          lead(p.research_adjusted_price,120) OVER w/p.research_adjusted_price-1 t120,
          lead(p.research_adjusted_price,250) OVER w/p.research_adjusted_price-1 t250
          FROM research_adjusted_prices p JOIN eligible e USING(trade_date,secid)
          JOIN historical_equity_universe u USING(secid)
          JOIN stage30_liquidity_daily l USING(trade_date,secid,boardid)
          LEFT JOIN stage30_breadth_daily b USING(trade_date)
          LEFT JOIN market_state_daily ms USING(trade_date)
          WHERE p.version=? WINDOW w AS(PARTITION BY p.secid ORDER BY p.trade_date)
        ), enriched AS (
          SELECT *,
          (SELECT value FROM macro_observations m WHERE m.series_id='cbr_key_rate'
            AND m.observation_date<=base.trade_date AND CAST(m.available_from AS DATE)<=base.trade_date
            ORDER BY m.observation_date DESC,m.loaded_at DESC LIMIT 1) key_rate,
          (SELECT value FROM macro_observations m WHERE m.series_id='cbr_usd_rub'
            AND m.observation_date<=base.trade_date AND CAST(m.available_from AS DATE)<=base.trade_date
            ORDER BY m.observation_date DESC,m.loaded_at DESC LIMIT 1) usd_rub
          FROM base
        ), ranked AS (
          SELECT *,t5-avg(t5) OVER(PARTITION BY trade_date) x5,
          t20-avg(t20) OVER(PARTITION BY trade_date) x20,
          t60-avg(t60) OVER(PARTITION BY trade_date) x60,
          t120-avg(t120) OVER(PARTITION BY trade_date) x120,
          t250-avg(t250) OVER(PARTITION BY trade_date) x250,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY t5) r5,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY t20) r20,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY t60) r60,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY t120) r120,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY t250) r250 FROM enriched
        ) SELECT ?,trade_date,secid,""" + _issuer_case() + """,quality_tier,liquidity_tier,
        currently_inactive,price,return_1d,turnover_20,liquidity_percentile,breadth_balance,
        market_state,key_rate,usd_rub,NULL,
        EXISTS(SELECT 1 FROM issuer_fundamental_values f WHERE f.secid=ranked.secid
          AND f.validation_status='validated' AND CAST(f.available_from AS DATE)<=ranked.trade_date),
        (SELECT dividend_yield_pit FROM stage30_dividend_pit d WHERE d.secid=ranked.secid
          AND d.available_from<=ranked.trade_date ORDER BY d.available_from DESC LIMIT 1),
        NULL,t5,t20,t60,t120,t250,x5,x20,x60,x120,x250,r5,r20,r60,r120,r250 FROM ranked""",
        [version, POLICY_VERSION, version],
    )
    return con.execute(
        "SELECT count(*) FROM historical_training_panel WHERE dataset_version=?", [version]
    ).fetchone()[0]


def _breadth(con, version: str) -> None:
    con.execute("DELETE FROM breadth4_daily WHERE dataset_version=?", [version])
    for kind, condition in (("all_tradable_valid", "q.training_tier!='excluded'"),
                            ("training_quality", "q.training_tier IN ('A','B')")):
        con.execute(
            f"""INSERT INTO breadth4_daily
            WITH x AS (SELECT p.trade_date,p.secid,p.research_adjusted_price price,l.return_1d,
              l.turnover_20,lag(p.research_adjusted_price,20) OVER(
                PARTITION BY p.secid ORDER BY p.trade_date) p20,
              max(p.research_adjusted_price) OVER(PARTITION BY p.secid ORDER BY p.trade_date) peak
              FROM research_adjusted_prices p JOIN historical_quality_v2 q USING(secid)
              JOIN stage30_liquidity_daily l USING(trade_date,secid,boardid)
              WHERE p.version=? AND {condition})
            SELECT ?,?,trade_date,count(*),count(*) FILTER(return_1d>0),
            count(*) FILTER(return_1d<0),avg(return_1d),stddev_samp(return_1d),
            stddev_samp(price/nullif(p20,0)-1),stddev_samp(ln(nullif(turnover_20,0))),
            count(*) FILTER(price/peak-1<=-.2) FROM x GROUP BY trade_date""",
            [POLICY_VERSION, version, kind],
        )


def build_training_universe(con) -> dict:
    ensure_schema(con)
    created = datetime.now(UTC)
    cutoff = con.execute("SELECT max(trade_date) FROM research_adjusted_prices").fetchone()[0]
    schema_hash = hashlib.sha256(
        json.dumps(con.execute("DESCRIBE historical_training_panel").fetchall(), default=str).encode()
    ).hexdigest()
    quality_hash = hashlib.sha256(
        repr(con.execute(
            """SELECT training_tier,count(*),sum(observations),sum(quality_score)
            FROM historical_quality_v2 GROUP BY 1 ORDER BY 1"""
        ).fetchall()).encode()
    ).hexdigest()
    version = hashlib.sha256(
        f"{PANEL_VERSION}:{cutoff}:{schema_hash}:{quality_hash}:{POLICY_VERSION}".encode()
    ).hexdigest()[:20]
    existing = con.execute(
        "SELECT rows,eligible_securities FROM training_universe_runs WHERE dataset_version=?", [version]
    ).fetchone()
    if existing:
        return {"dataset_version": version, "status": "already_frozen", "rows": existing[0],
                "eligible_securities": existing[1], "production_changes": 0}
    eligibility_rows = _eligibility(con, version)
    rows = _panel(con, version)
    _breadth(con, version)
    stats = con.execute(
        """WITH panel AS (SELECT * FROM historical_training_panel WHERE dataset_version=?),
        daily AS (SELECT trade_date,count(*) n FROM panel GROUP BY trade_date)
        SELECT (SELECT count(DISTINCT secid) FROM panel),count(*),min(trade_date),max(trade_date),
        median(n),quantile_cont(n,.05),quantile_cont(n,.25),quantile_cont(n,.75),
        quantile_cont(n,.95) FROM daily""", [version]
    ).fetchone()
    securities, dates, date_from, date_to, median_n, p05, p25, p75, p95 = stats
    details = {"eligibility_rows": eligibility_rows, "date_from": date_from, "date_to": date_to,
               "median_securities_per_date": median_n, "universe_percentiles": {
                   "p05": p05, "p25": p25, "p50": median_n, "p75": p75, "p95": p95},
               "minimum_history": HORIZON_MINIMUM, "liquidity_minimum_rub": LIQUIDITY_MINIMUM_RUB,
               "future_membership_used": False, "issuer_cluster": True}
    con.execute(
        """INSERT INTO training_universe_runs VALUES
        (?,? ,?,'completed',1002,?,?,?,?,?,?,TRUE,0,?)""",
        [version, created, cutoff, securities, rows, dates, schema_hash, POLICY_VERSION,
         POLICY_VERSION, json.dumps(details, default=str)],
    )
    return {"dataset_version": version, "status": "completed", "eligible_securities": securities,
            "rows": rows, "dates": dates, **details, "production_changes": 0}


def training_universe_status(con) -> dict:
    ensure_schema(con)
    return {"latest": con.execute(
        "SELECT * FROM training_universe_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone(), "tiers": con.execute(
        "SELECT training_tier,count(*) FROM historical_quality_v2 GROUP BY 1 ORDER BY 1"
    ).fetchall()}
