"""Evidence-gated corporate-action episodes and historical quality policy 2.0."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.historical_data.schema import DDL as HISTORICAL_DDL

from .schema import DDL

PORTFOLIO = {"SBER", "SBERP", "LKOH", "MTSS", "TRNFP", "MOEX", "PHOR", "TATN",
             "TATNP", "LSNG", "LSNGP", "X5", "FIVE"}
RATIOS = (0.001, 0.01, 0.1, 0.2, 1 / 3, 0.5, 2.0, 3.0, 5.0, 10.0, 100.0, 1000.0)
POLICY_VERSION = "historical-quality-v2.0"


def ensure_schema(con) -> None:
    con.execute(HISTORICAL_DDL)
    con.execute(DDL)


def _episodes(con) -> int:
    con.execute("DELETE FROM corporate_action_candidate_episodes")
    ratios = ",".join(f"({value})" for value in RATIOS)
    con.execute(
        f"""INSERT INTO corporate_action_candidate_episodes
        WITH chain AS (
          SELECT e.trade_date,e.secid,e.boardid,e.close,
          lag(e.close) OVER(PARTITION BY e.secid ORDER BY e.trade_date) prior_close,
          lag(e.trade_date) OVER(PARTITION BY e.secid ORDER BY e.trade_date) prior_date
          FROM moex_equity_eod e JOIN equity_board_history b USING(secid,boardid)
          WHERE b.selected_for_chain AND e.close>0
        ), flags AS (
          SELECT *,close/prior_close ratio,
          sum(CASE WHEN date_diff('day',prior_date,trade_date)>5 THEN 1 ELSE 0 END)
          OVER(PARTITION BY secid ORDER BY trade_date) episode_no
          FROM chain WHERE prior_close>0 AND abs(ln(close)-ln(prior_close))>ln(1.5)
        ), grouped AS (
          SELECT secid,episode_no,min(trade_date) date_from,max(trade_date) date_to,count(*) n,
          list(DISTINCT boardid) boards,first(prior_close ORDER BY trade_date) before_px,
          last(close ORDER BY trade_date) after_px,
          exp(avg(ln(ratio))) observed FROM flags GROUP BY secid,episode_no
        ), nearest AS (
          SELECT grouped.*,r.candidate,
          abs(ln(observed)-ln(r.candidate)) ratio_error,
          row_number() OVER(PARTITION BY secid,episode_no
            ORDER BY abs(ln(observed)-ln(r.candidate))) rn
          FROM grouped CROSS JOIN (VALUES {ratios}) AS r(candidate)
        ), quality AS (
          SELECT q.secid,q.training_tier,
          coalesce((SELECT median(turnover_20) FROM stage30_liquidity_daily l
                    WHERE l.secid=q.secid),0) median_turnover
          FROM stage30_security_quality q
        )
        SELECT sha256(secid||':'||date_from::VARCHAR),secid,date_from,date_to,
        CASE WHEN secid IN ({','.join(repr(x) for x in sorted(PORTFOLIO))}) THEN 'P1'
             WHEN quality.training_tier IN ('A','B') THEN 'P2'
             WHEN quality.median_turnover>=1000000 THEN 'P3'
             WHEN quality.training_tier='C' THEN 'P4' ELSE 'P5' END,
        n,to_json(boards),before_px,after_px,observed,candidate,ratio_error,
        CASE WHEN ratio_error<=.03 THEN 'ratio_candidate' ELSE 'unknown_large_move' END,
        'no_official_evidence','unresolved',current_timestamp
        FROM nearest LEFT JOIN quality USING(secid) WHERE rn=1"""
    )
    return con.execute("SELECT count(*) FROM corporate_action_candidate_episodes").fetchone()[0]


def _link_evidence(con) -> int:
    """Only pre-existing validated official records can auto-validate an episode."""
    con.execute("DELETE FROM corporate_action_evidence")
    con.execute(
        """INSERT INTO corporate_action_evidence
        SELECT sha256(e.episode_id||':'||a.action_id),e.episode_id,a.source,a.source,
        current_timestamp,a.document_hash,CAST(a.announced_at AS DATE),a.effective_date,
        a.action_type,a.ratio,a.secid_before,a.secid_after,a.validation_status,
        json_object('action_id',a.action_id,'notes',a.notes)
        FROM corporate_action_candidate_episodes e JOIN historical_corporate_actions a
        ON (e.secid=a.secid_before OR e.secid=a.secid_after)
        AND a.effective_date BETWEEN e.date_from-INTERVAL 5 DAY AND e.date_to+INTERVAL 5 DAY
        WHERE a.validation_status='validated'"""
    )
    con.execute(
        """UPDATE corporate_action_candidate_episodes e SET
        evidence_status='official_validated',review_status='auto_validated'
        FROM corporate_action_evidence v WHERE v.episode_id=e.episode_id
        AND v.validation_status='validated'"""
    )
    con.execute(
        """UPDATE corporate_action_candidate_episodes SET review_status='manual_review_required'
        WHERE review_status='unresolved' AND priority IN ('P1','P2')"""
    )
    return con.execute("SELECT count(*) FROM corporate_action_evidence").fetchone()[0]


def _adjusted_prices(con) -> int:
    con.execute("DELETE FROM research_price_adjustments")
    con.execute(
        """INSERT INTO research_price_adjustments
        SELECT sha256(e.episode_id||':adjustment'),e.secid,v.effective_date,v.action_type,
        1/nullif(v.ratio,0),v.evidence_id,
        greatest(v.retrieved_at,coalesce(v.publication_date::TIMESTAMP,v.retrieved_at)),
        v.validation_status,current_timestamp
        FROM corporate_action_candidate_episodes e JOIN corporate_action_evidence v USING(episode_id)
        WHERE e.review_status='auto_validated' AND v.ratio>0"""
    )
    con.execute("DELETE FROM research_adjusted_prices WHERE version=?", [POLICY_VERSION])
    con.execute(
        """INSERT INTO research_adjusted_prices
        SELECT e.trade_date,e.secid,e.boardid,e.close,e.close,
        e.close*coalesce((SELECT product(a.adjustment_factor) FROM research_price_adjustments a
          WHERE a.secid=e.secid AND e.trade_date<a.effective_date
          AND a.validation_status='validated'),1),
        coalesce((SELECT product(a.adjustment_factor) FROM research_price_adjustments a
          WHERE a.secid=e.secid AND e.trade_date<a.effective_date
          AND a.validation_status='validated'),1),
        json_object('raw_table','moex_equity_eod','validated_adjustments_only',TRUE),?
        FROM moex_equity_eod e JOIN equity_board_history b USING(secid,boardid)
        WHERE b.selected_for_chain""", [POLICY_VERSION]
    )
    return con.execute(
        "SELECT count(*) FROM research_adjusted_prices WHERE version=?", [POLICY_VERSION]
    ).fetchone()[0]


def _quality(con) -> dict:
    con.execute("DELETE FROM historical_quality_v2")
    con.execute(
        """INSERT INTO historical_quality_v2
        WITH episodes AS (
          SELECT secid,count(*) total,count(*) FILTER(review_status='auto_validated') resolved,
          count(*) FILTER(review_status!='auto_validated') unresolved
          FROM corporate_action_candidate_episodes GROUP BY secid
        ), base AS (
          SELECT q.*,coalesce(e.total,0) episodes,coalesce(e.resolved,0) resolved,
          coalesce(e.unresolved,0) unresolved,
          CASE WHEN q.secid IN ('SBER','SBERP','LKOH','MTSS','TRNFP','MOEX','PHOR','TATN',
          'TATNP','LSNG','LSNGP','X5','FIVE') THEN 'P1'
          WHEN q.training_tier IN ('A','B') THEN 'P2' ELSE 'P4' END priority
          FROM stage30_security_quality q LEFT JOIN episodes e USING(secid)
        )
        SELECT secid,priority,history_years,observations,
        CASE WHEN board_continuity IN ('single_board','resolved_primary_chain') THEN 1 ELSE .5 END,
        CASE WHEN episodes=0 THEN 1 ELSE resolved::DOUBLE/episodes END,
        volume_coverage,numtrades_coverage,CASE WHEN board_count=1 THEN 1 ELSE .8 END,
        missing_ohlc,.8,least(fundamental_periods/5.0,1),
        100*(.20*least(history_years/10,1)+.15*(1-missing_ohlc)+.15*volume_coverage+
        .10*numtrades_coverage+.10*(CASE WHEN board_count=1 THEN 1 ELSE .8 END)+
        .15*(CASE WHEN episodes=0 THEN 1 ELSE resolved::DOUBLE/episodes END)+.10*.8+
        .05*least(fundamental_periods/5.0,1)),
        CASE WHEN history_years>=8 AND observations>=1200 AND missing_ohlc<=.03
                  AND volume_coverage>=.9 AND numtrades_coverage>=.7
                  AND unresolved<=greatest(1,observations*.001) THEN 'A'
             WHEN history_years>=4 AND observations>=600 AND missing_ohlc<=.08
                  AND volume_coverage>=.7 AND unresolved<=greatest(3,observations*.005) THEN 'B'
             WHEN observations>=250 AND missing_ohlc<=.15 THEN 'C' ELSE 'excluded' END,
        CASE WHEN observations<250 THEN 'short_history'
             WHEN missing_ohlc>.15 THEN 'excessive_missingness'
             WHEN unresolved>greatest(3,observations*.005) THEN 'corporate_action_uncertainty'
             ELSE NULL END,?,current_timestamp FROM base""", [POLICY_VERSION]
    )
    return dict(con.execute(
        "SELECT training_tier,count(*) FROM historical_quality_v2 GROUP BY 1"
    ).fetchall())


def _review_file(con, path: Path) -> int:
    rows = con.execute(
        """SELECT secid,date_from,date_to,raw_price_before,raw_price_after,observed_ratio,
        candidate_type,evidence_status,review_status,priority FROM corporate_action_candidate_episodes
        WHERE review_status!='auto_validated' AND priority IN ('P1','P2')
        ORDER BY priority,secid,date_from"""
    ).fetchall()
    payload = {"version": POLICY_VERSION, "generated_at": datetime.now(UTC).isoformat(),
               "policy": "candidate ratios are not validation", "episodes": [
                   dict(zip(("security","date_from","date_to","raw_price_before","raw_price_after",
                             "ratio","candidate_type","official_evidence","review_status","priority"),
                            row, strict=True)) for row in rows]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return len(rows)


def build_corporate_action_quality(con, review_path: Path | None = None) -> dict:
    ensure_schema(con)
    started = datetime.now(UTC)
    run_id = hashlib.sha256(f"stage31:{started.isoformat()}".encode()).hexdigest()[:20]
    flags = con.execute(
        """SELECT count(*) FROM market_history_quality_issues
        WHERE issue_type='large_return_corporate_action_review'"""
    ).fetchone()[0]
    episodes = _episodes(con)
    _link_evidence(con)
    adjusted = _adjusted_prices(con)
    tiers = _quality(con)
    review_path = review_path or PROJECT_ROOT / "data" / "review" / "stage31_corporate_actions.local.yaml"
    review_rows = _review_file(con, review_path)
    review_location = str(
        review_path.relative_to(PROJECT_ROOT)
        if review_path.is_relative_to(PROJECT_ROOT)
        else review_path
    )
    statuses = dict(con.execute(
        "SELECT review_status,count(*) FROM corporate_action_candidate_episodes GROUP BY 1"
    ).fetchall())
    details = {"raw_rewritten": False, "thresholds_tuned_on_model_performance": False,
               "review_rows": review_rows, "policy_version": POLICY_VERSION}
    con.execute(
        """INSERT INTO corporate_action_runs VALUES
        (?, ?,current_timestamp,'completed',?,?,?,?,?,?,?,?,?,?,?,0,?)""",
        [run_id, started, flags, episodes, statuses.get("auto_validated", 0),
         statuses.get("manual_review_required", 0), statuses.get("unresolved", 0), adjusted,
         tiers.get("A", 0), tiers.get("B", 0), tiers.get("C", 0), tiers.get("excluded", 0),
         review_location, json.dumps(details)],
    )
    return {"run_id": run_id, "flags_before": flags, "episodes": episodes,
            "auto_validated": statuses.get("auto_validated", 0),
            "manual_review": statuses.get("manual_review_required", 0),
            "unresolved": statuses.get("unresolved", 0), "adjusted_rows": adjusted,
            "tiers": tiers, "review_rows": review_rows, "review_path": str(review_path),
            "production_changes": 0, **details}


def corporate_action_status(con) -> dict:
    ensure_schema(con)
    return {"latest": con.execute(
        "SELECT * FROM corporate_action_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone(), "tiers": con.execute(
        "SELECT training_tier,count(*) FROM historical_quality_v2 GROUP BY 1 ORDER BY 1"
    ).fetchall()}
