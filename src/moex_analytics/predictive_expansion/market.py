"""PIT-safe liquidity, breadth 3.0 and training-quality builders."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

from moex_analytics.market_history import quality_audit, survivorship_diagnostic

from .schema import DDL

HORIZONS = (5, 20, 60, 120)


def ensure_schema(con) -> None:
    con.execute(DDL)


def _selected_chain(con) -> None:
    con.execute("DELETE FROM equity_board_history")
    con.execute(
        """INSERT INTO equity_board_history
        SELECT secid,boardid,min(trade_date),max(trade_date),count(*),
        sum(coalesce(value,0)),FALSE,NULL,current_timestamp
        FROM moex_equity_eod GROUP BY 1,2"""
    )
    con.execute(
        """UPDATE equity_board_history b SET selected_for_chain=TRUE FROM (
        SELECT secid,boardid,row_number() OVER(PARTITION BY secid ORDER BY
        total_value DESC,observations DESC,boardid) rank FROM equity_board_history) ranked
        WHERE b.secid=ranked.secid AND b.boardid=ranked.boardid AND ranked.rank=1"""
    )
    con.execute(
        """UPDATE equity_board_history SET exclusion_reason='lower_turnover_duplicate_board'
        WHERE NOT selected_for_chain"""
    )


def _build_liquidity(con) -> int:
    con.execute("DELETE FROM stage30_liquidity_daily")
    con.execute(
        """INSERT INTO stage30_liquidity_daily
        WITH raw AS (
          SELECT e.*,lag(close) OVER(PARTITION BY secid ORDER BY trade_date) previous_close
          FROM moex_equity_eod e JOIN equity_board_history b USING(secid,boardid)
          WHERE b.selected_for_chain
        ), base AS (
          SELECT *,close/previous_close-1 ret,
          stddev_samp(close/previous_close-1) OVER(
            PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) vol20
          FROM raw
        ), rolling AS (
          SELECT *,
          avg(value) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) t5,
          avg(value) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) t20,
          avg(value) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) t60,
          avg(value) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) t120,
          avg(value) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) t250,
          avg(volume) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) v20,
          avg(volume) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 39 PRECEDING AND 20 PRECEDING) vprev,
          avg(num_trades) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) n20,
          avg(num_trades) OVER(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 39 PRECEDING AND 20 PRECEDING) nprev
          FROM base
        ), ranked AS (
          SELECT *,percent_rank() OVER(PARTITION BY trade_date ORDER BY t20) lp,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY value) tp,
          percent_rank() OVER(PARTITION BY trade_date ORDER BY volume) vp
          FROM rolling
        )
        SELECT trade_date,secid,boardid,ret,value,volume,num_trades,
        value/nullif(num_trades,0),volume/nullif(num_trades,0),
        abs(ret)/nullif(value,0),coalesce(volume,0)=0,value,t5,t20,t60,t120,t250,
        v20,n20,v20/nullif(vprev,0)-1,n20/nullif(nprev,0)-1,
        abs(ret)/nullif(volume,0),value/nullif(vol20,0),lp,tp,vp,
        CASE WHEN lp>=.8 THEN 'high' WHEN lp<.2 THEN 'low' ELSE 'normal' END,
        current_timestamp FROM ranked"""
    )
    return con.execute("SELECT count(*) FROM stage30_liquidity_daily").fetchone()[0]


def _build_breadth(con, minimum_constituents: int = 30) -> int:
    con.execute("DELETE FROM stage30_breadth_daily")
    con.execute(
        """INSERT INTO stage30_breadth_daily
        WITH base AS (
          SELECT e.trade_date,e.secid,e.close,e.value,e.volume,
          close/lag(close) OVER(PARTITION BY e.secid ORDER BY trade_date)-1 ret,
          max(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) hi20,
          max(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) hi60,
          max(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) hi250,
          min(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) lo20,
          min(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) lo60,
          min(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) lo250,
          avg(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) sma20,
          avg(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) sma50,
          avg(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 99 PRECEDING AND CURRENT ROW) sma100,
          avg(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) sma200,
          lag(close,5) OVER(PARTITION BY e.secid ORDER BY trade_date) p5,
          lag(close,20) OVER(PARTITION BY e.secid ORDER BY trade_date) p20,
          lag(close,60) OVER(PARTITION BY e.secid ORDER BY trade_date) p60,
          lag(close,120) OVER(PARTITION BY e.secid ORDER BY trade_date) p120,
          max(close) OVER(PARTITION BY e.secid ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) peak
          FROM moex_equity_eod e JOIN equity_board_history b USING(secid,boardid)
          WHERE b.selected_for_chain
        ), joined AS (
          SELECT base.*,l.liquidity_percentile FROM base
          LEFT JOIN stage30_liquidity_daily l USING(trade_date,secid)
        )
        SELECT trade_date,count(*),count(*) FILTER(WHERE ret>0),count(*) FILTER(WHERE ret<0),
        count(*) FILTER(WHERE ret=0),count(*) FILTER(WHERE close>=hi20),
        count(*) FILTER(WHERE close>=hi60),count(*) FILTER(WHERE close>=hi250),
        count(*) FILTER(WHERE close<=lo20),count(*) FILTER(WHERE close<=lo60),
        count(*) FILTER(WHERE close<=lo250),count(*) FILTER(WHERE close>sma20),
        count(*) FILTER(WHERE close>sma50),count(*) FILTER(WHERE close>sma100),
        count(*) FILTER(WHERE close>sma200),count(*) FILTER(WHERE close>p5),
        count(*) FILTER(WHERE close>p20),count(*) FILTER(WHERE close>p60),
        count(*) FILTER(WHERE close>p120),median(ret),avg(ret),stddev_samp(ret),
        quantile_cont(ret,.75)-quantile_cont(ret,.25),
        sum(value) FILTER(WHERE ret>0)/nullif(sum(value),0),
        sum(volume) FILTER(WHERE ret>0)/nullif(sum(volume),0),avg(liquidity_percentile),
        count(*) FILTER(WHERE close/peak-1<=-.10),count(*) FILTER(WHERE close/peak-1<=-.20),
        count(*) FILTER(WHERE close/peak-1<=-.30),count(*),
        CASE WHEN count(*)>=? THEN 'sufficient' ELSE 'insufficient_constituents' END,
        current_timestamp FROM joined GROUP BY trade_date""",
        [minimum_constituents],
    )
    return con.execute("SELECT count(*) FROM stage30_breadth_daily").fetchone()[0]


def _build_quality(con) -> int:
    con.execute("DELETE FROM stage30_security_quality")
    con.execute(
        """INSERT INTO stage30_security_quality
        WITH history AS (
          SELECT e.secid,min(e.trade_date) first_trade,max(e.trade_date) last_trade,count(*) observations,
          date_diff('day',min(e.trade_date),max(e.trade_date))/365.25 history_years,
          avg((open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL)::INTEGER) missing_ohlc,
          avg((volume IS NOT NULL)::INTEGER) volume_coverage,
          avg((num_trades IS NOT NULL)::INTEGER) trades_coverage,count(DISTINCT e.boardid) boards
          FROM moex_equity_eod e GROUP BY e.secid
        ), flags AS (
          SELECT secid,count(*) flags FROM market_history_quality_issues
          WHERE issue_type='large_return_corporate_action_review' GROUP BY secid
        ), fundamentals AS (
          SELECT secid,count(DISTINCT period_end) periods FROM issuer_fundamental_values
          WHERE validation_status='validated' GROUP BY secid
        )
        SELECT h.secid,first_trade,last_trade,observations,history_years,missing_ohlc,volume_coverage,
        trades_coverage,boards,CASE WHEN boards=1 THEN 'single_board' ELSE 'resolved_primary_chain' END,
        coalesce(flags,0),coalesce(periods,0),
        CASE WHEN coalesce(periods,0)>=5 THEN 'validated_multi_period'
             WHEN coalesce(periods,0)>0 THEN 'partial' ELSE 'missing' END,
        (1-missing_ohlc+volume_coverage+trades_coverage)/3,
        CASE WHEN history_years>=10 AND observations>=1500 AND missing_ohlc<=.02 AND volume_coverage>=.95
                  AND trades_coverage>=.8 AND coalesce(flags,0)=0 THEN 'A'
             WHEN history_years>=5 AND observations>=750 AND missing_ohlc<=.05 AND volume_coverage>=.8 THEN 'B'
             WHEN observations>=250 AND missing_ohlc<=.15 THEN 'C' ELSE 'excluded' END,
        CASE WHEN observations<250 THEN 'short_history'
             WHEN missing_ohlc>.15 THEN 'excessive_missingness'
             WHEN coalesce(flags,0)>0 THEN 'corporate_action_review' ELSE NULL END,
        current_timestamp FROM history h LEFT JOIN flags USING(secid)
        LEFT JOIN fundamentals USING(secid)"""
    )
    return con.execute("SELECT count(*) FROM stage30_security_quality").fetchone()[0]


def _build_training_quality(con) -> int:
    con.execute("DELETE FROM stage30_training_sample_quality")
    for horizon in HORIZONS:
        con.execute(
            """INSERT INTO stage30_training_sample_quality
            SELECT q.secid,?,greatest(q.observations-?,0),q.history_years,
            greatest(q.observations-?,0)::DOUBLE/nullif(?,0),
            CASE WHEN q.history_years>=10 THEN 4 WHEN q.history_years>=5 THEN 2 ELSE 1 END,
            1-q.feature_coverage,q.corporate_action_flags,
            (SELECT median(turnover_20) FROM stage30_liquidity_daily l WHERE l.secid=q.secid),
            q.feature_coverage,q.training_tier IN ('A','B') AND q.observations>=?*10,
            q.training_tier,current_timestamp FROM stage30_security_quality q""",
            [horizon, horizon, horizon, horizon, horizon],
        )
    return con.execute("SELECT count(*) FROM stage30_training_sample_quality").fetchone()[0]


def _capture_survivorship(con, securities: int) -> None:
    for threshold in (750, 1000, 1500):
        if securities < threshold:
            continue
        exists = con.execute(
            "SELECT count(*) FROM stage30_survivorship_diagnostics WHERE threshold=?", [threshold]
        ).fetchone()[0]
        if exists:
            continue
        diagnostic = survivorship_diagnostic(con)
        con.execute(
            """INSERT INTO stage30_survivorship_diagnostics VALUES
            (?,?,current_timestamp,?,?,?,?,?,?,?,'captured')""",
            [
                threshold,
                securities,
                diagnostic.get("days"),
                diagnostic.get("mean_difference"),
                diagnostic.get("median_difference"),
                diagnostic.get("p95_absolute_difference"),
                con.execute("SELECT count(*) FROM stage30_breadth_daily").fetchone()[0],
                json.dumps({"status": "pending_same_sample_ablation"}),
                json.dumps({"status": "not_yet_rerun"}),
            ],
        )


def build_market_features(con) -> dict:
    """Rebuild research features from observed tradable-on-date rows only."""
    started = time.perf_counter()
    ensure_schema(con)
    run_id = hashlib.sha256(f"market:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    _selected_chain(con)
    audit = quality_audit(con)
    liquidity = _build_liquidity(con)
    breadth = _build_breadth(con)
    quality = _build_quality(con)
    training = _build_training_quality(con)
    securities = con.execute("SELECT count(DISTINCT secid) FROM moex_equity_eod").fetchone()[0]
    _capture_survivorship(con, securities)
    runtime = time.perf_counter() - started
    details = {"quality_audit": audit, "training_rows": training, "pit_safe": True}
    con.execute(
        "INSERT INTO stage30_market_feature_runs VALUES (?,current_timestamp,'completed',?,?,?,?,?,0,?)",
        [run_id, securities, liquidity, breadth, quality, runtime, json.dumps(details)],
    )
    return {
        "run_id": run_id,
        "securities": securities,
        "liquidity_rows": liquidity,
        "breadth_days": breadth,
        "quality_rows": quality,
        "training_rows": training,
        "runtime_seconds": runtime,
        "production_changes": 0,
    }


def expansion_market_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,securities,liquidity_rows,breadth_days,quality_rows,
        runtime_seconds,production_changes FROM stage30_market_feature_runs
        ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    tiers = con.execute(
        "SELECT training_tier,count(*) FROM stage30_security_quality GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {"latest": latest, "tiers": tiers}
