"""Immutable live forecast capture, outcome evaluation and learning scorecards."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import numpy as np

VERSION = "forecast-scorecard-v1"
HORIZONS = (1, 5, 20, 60, 120, 250)

DDL = """
CREATE TABLE IF NOT EXISTS forecast_registry(
 forecast_id VARCHAR PRIMARY KEY,generated_at TIMESTAMP,cutoff DATE,secid VARCHAR,
 horizon_sessions INTEGER,model_family VARCHAR,model_version VARCHAR,decision_version VARCHAR,
 input_hash VARCHAR,current_price DOUBLE,qualitative_direction VARCHAR,probability_value DOUBLE,
 probability_allowed BOOLEAN,median_return DOUBLE,median_price DOUBLE,range_50_low DOUBLE,
 range_50_high DOUBLE,range_80_low DOUBLE,range_80_high DOUBLE,range_90_low DOUBLE,
 range_90_high DOUBLE,expected_max_adverse_excursion DOUBLE,expected_max_favorable_excursion DOUBLE,
 touch_up_3 DOUBLE,touch_up_5 DOUBLE,touch_up_10 DOUBLE,touch_down_3 DOUBLE,touch_down_5 DOUBLE,
 touch_down_10 DOUBLE,valuation_status VARCHAR,regime_status VARCHAR,portfolio_status VARCHAR,
 confidence DOUBLE,evidence_for_json JSON,evidence_against_json JSON,limitations_json JSON,
 immutable BOOLEAN,created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS forecast_outcomes(
 forecast_id VARCHAR PRIMARY KEY,outcome_status VARCHAR,maturity_trade_date DATE,actual_close DOUBLE,
 actual_return DOUBLE,actual_excess_return_vs_imoex DOUBLE,max_adverse_excursion DOUBLE,
 max_favorable_excursion DOUBLE,touched_up_3 BOOLEAN,touched_up_5 BOOLEAN,touched_up_10 BOOLEAN,
 touched_down_3 BOOLEAN,touched_down_5 BOOLEAN,touched_down_10 BOOLEAN,direction_correct BOOLEAN,
 neutral_hit BOOLEAN,inside_50_interval BOOLEAN,inside_80_interval BOOLEAN,inside_90_interval BOOLEAN,
 days_to_maturity INTEGER,evaluated_at TIMESTAMP,data_version VARCHAR);
CREATE TABLE IF NOT EXISTS decision_outcomes(
 forecast_id VARCHAR,strategy VARCHAR,entry_trade_date DATE,entry_price DOUBLE,entered BOOLEAN,
 lower_entry BOOLEAN,missed_upside BOOLEAN,subsequent_return DOUBLE,max_drawdown DOUBLE,
 waiting_sessions INTEGER,hypothetical BOOLEAN,calculated_at TIMESTAMP,
 PRIMARY KEY(forecast_id,strategy));
CREATE TABLE IF NOT EXISTS forecast_scorecards(
 dimension VARCHAR,dimension_value VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,
 forecasts INTEGER,effective_n DOUBLE,hit_rate DOUBLE,balanced_accuracy DOUBLE,roc_auc DOUBLE,
 brier DOUBLE,log_loss DOUBLE,mean_return DOUBLE,median_return DOUBLE,mae DOUBLE,rmse DOUBLE,
 sign_accuracy DOUBLE,pearson DOUBLE,spearman DOUBLE,coverage_50 DOUBLE,coverage_80 DOUBLE,
 coverage_90 DOUBLE,waiting_advantage DOUBLE,drawdown_reduction DOUBLE,missed_upside_cost DOUBLE,
 live_status VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(dimension,dimension_value,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS model_version_scorecards(
 model_family VARCHAR,model_version VARCHAR,active_from DATE,active_to DATE,forecasts INTEGER,
 matured_forecasts INTEGER,score DOUBLE,status VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(model_family,model_version));
CREATE TABLE IF NOT EXISTS forecast_learning_journal(
 forecast_id VARCHAR PRIMARY KEY,secid VARCHAR,horizon_sessions INTEGER,forecast_text VARCHAR,
 outcome_text VARCHAR,correct_text VARCHAR,incorrect_text VARCHAR,evidence_for_json JSON,
 evidence_against_json JSON,misleading_factors_json JSON,regime VARCHAR,data_quality VARCHAR,
 model_version VARCHAR,error_category VARCHAR,causality_warning VARCHAR,created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS factor_live_scorecards(
 factor VARCHAR,horizon_sessions INTEGER,regime VARCHAR,uses INTEGER,hits INTEGER,hit_rate DOUBLE,
 average_return DOUBLE,sign_stability DOUBLE,status VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(factor,horizon_sessions,regime));
CREATE TABLE IF NOT EXISTS portfolio_decision_scorecards(
 report_id VARCHAR PRIMARY KEY,cutoff DATE,portfolio_value DOUBLE,portfolio_risk DOUBLE,status_map_json JSON,
 hypothetical_allocated DOUBLE,reserve_amount DOUBLE,hypothetical BOOLEAN,evaluated_at TIMESTAMP);
"""


def ensure_schema(con) -> None:
    con.execute(DDL)


def _loads(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _forecast_id(report_id: str, secid: str, horizon: int, version: str) -> str:
    return hashlib.sha256(f"{report_id}|{secid}|{horizon}|{version}".encode()).hexdigest()[:24]


def capture_daily_forecasts(con) -> dict:
    """Capture only the latest real immutable report; never manufacture historical forecasts."""
    ensure_schema(con)
    report = con.execute(
        "SELECT report_id,analysis_cutoff,created_at,input_hash,market_regime,methodology_version "
        "FROM human_daily_reports WHERE immutable ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not report:
        return {"status": "no_report", "inserted": 0, "existing": 0}
    report_id, cutoff, generated_at, input_hash, regime, version = report
    rows = con.execute(
        "SELECT h.secid,h.horizon,h.status,h.view_text,h.confidence,s.current_price,s.valuation_view,"
        "s.action_group,s.confidence_score,s.evidence_for_json,s.evidence_against_json,s.data_status "
        "FROM human_horizon_views h JOIN human_instrument_synthesis s USING(report_id,secid) "
        "WHERE h.report_id=? ORDER BY h.secid,h.horizon",
        [report_id],
    ).fetchall()
    inserted = 0
    for row in rows:
        (secid, horizon, direction, _text, _hconf, price, valuation, portfolio_status,
         confidence, evidence_for, evidence_against, data_status) = row
        forecast_id = _forecast_id(report_id, secid, horizon, version)
        limitations = ["live sample accumulating", "probability hidden by policy"]
        if data_status not in {"sufficient", "validated_current"}:
            limitations.append("insufficient data")
        before = con.execute("SELECT count(*) FROM forecast_registry WHERE forecast_id=?", [forecast_id]).fetchone()[0]
        con.execute(
            "INSERT OR IGNORE INTO forecast_registry(forecast_id,generated_at,cutoff,secid,"
            "horizon_sessions,model_family,model_version,decision_version,input_hash,current_price,"
            "qualitative_direction,probability_value,probability_allowed,valuation_status,regime_status,"
            "portfolio_status,confidence,evidence_for_json,evidence_against_json,limitations_json,"
            "immutable,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [forecast_id, generated_at, cutoff, secid, horizon, "human_portfolio_direction", version,
             "visual-status-v1", input_hash, price, direction, None, False, valuation, regime,
             portfolio_status, confidence, evidence_for, evidence_against,
             json.dumps(limitations, ensure_ascii=False), True, datetime.now()],
        )
        inserted += int(before == 0)
    return {"status": "captured" if inserted else "no_change", "inserted": inserted,
            "existing": len(rows) - inserted, "cutoff": cutoff, "model_version": version}


def trading_maturity(con, secid: str, cutoff, horizon: int):
    rows = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>? "
        "ORDER BY trade_date LIMIT ?", [secid, cutoff, horizon]
    ).fetchall()
    return rows[-1] if len(rows) == horizon else None


def direction_result(direction: str, actual_return: float, neutral_band: float = 0.01):
    if direction in {"small_positive", "↑"}:
        return actual_return > 0, None
    if direction in {"small_negative", "↓"}:
        return actual_return < 0, None
    if direction in {"neutral", "→"}:
        return None, abs(actual_return) <= neutral_band
    return None, None


def _touch(path: np.ndarray, level: float, up: bool) -> bool:
    return bool(np.max(path) >= level) if up else bool(np.min(path) <= -level)


def _inside(value, low, high):
    return None if low is None or high is None else low <= value <= high


def evaluate_matured_forecasts(con) -> dict:
    ensure_schema(con)
    forecasts = con.execute(
        "SELECT forecast_id,cutoff,secid,horizon_sessions,current_price,qualitative_direction,"
        "range_50_low,range_50_high,range_80_low,range_80_high,range_90_low,range_90_high,model_version "
        "FROM forecast_registry ORDER BY cutoff,secid,horizon_sessions"
    ).fetchall()
    matured = pending = existing = 0
    for row in forecasts:
        (fid, cutoff, secid, horizon, start, direction, low50, high50, low80, high80,
         low90, high90, version) = row
        previous = con.execute(
            "SELECT outcome_status FROM forecast_outcomes WHERE forecast_id=?", [fid]
        ).fetchone()
        if previous and previous[0] != "pending":
            existing += 1
            continue
        maturity = trading_maturity(con, secid, cutoff, horizon)
        if maturity is None:
            pending += 1
            con.execute(
                "INSERT OR IGNORE INTO forecast_outcomes(forecast_id,outcome_status,evaluated_at,data_version) "
                "VALUES (?,'pending',current_timestamp,?)", [fid, VERSION]
            )
            continue
        maturity_date, close = maturity
        path_rows = con.execute(
            "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? "
            "AND trade_date>? AND trade_date<=? ORDER BY trade_date", [secid, cutoff, maturity_date]
        ).fetchall()
        returns = np.array([float(value) / start - 1 for _, value in path_rows])
        actual_return = float(close / start - 1)
        direction_correct, neutral_hit = direction_result(direction, actual_return)
        benchmark = trading_maturity(con, "IMOEX", cutoff, horizon)
        benchmark_start = con.execute(
            "SELECT close FROM canonical_daily_prices WHERE canonical_secid='IMOEX' AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 1", [cutoff]
        ).fetchone()
        excess = None
        if benchmark and benchmark_start:
            excess = actual_return - (benchmark[1] / benchmark_start[0] - 1)
        con.execute("DELETE FROM forecast_outcomes WHERE forecast_id=? AND outcome_status='pending'", [fid])
        con.execute(
            "INSERT INTO forecast_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [fid, "matured", maturity_date, close, actual_return, excess, float(returns.min()),
             float(returns.max()), _touch(returns, .03, True), _touch(returns, .05, True),
             _touch(returns, .10, True), _touch(returns, .03, False), _touch(returns, .05, False),
             _touch(returns, .10, False), direction_correct, neutral_hit,
             _inside(actual_return, low50, high50), _inside(actual_return, low80, high80),
             _inside(actual_return, low90, high90), len(path_rows), datetime.now(), VERSION]
        )
        _evaluate_decision(con, fid, cutoff, secid, horizon, start, maturity_date)
        matured += 1
    return {"matured": matured, "pending": pending, "already_evaluated": existing}


def _evaluate_decision(con, fid, cutoff, secid, horizon, start, maturity_date):
    path = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>? "
        "AND trade_date<=? ORDER BY trade_date", [secid, cutoff, maturity_date]
    ).fetchall()
    if not path:
        return
    final = float(path[-1][1])
    strategies = [("buy_now", cutoff, start, 0)]
    if len(path) >= 5:
        strategies.append(("wait_5", path[4][0], float(path[4][1]), 5))
    for threshold in (.03, .05):
        hit = next(((d, float(p), i + 1) for i, (d, p) in enumerate(path) if p <= start * (1 - threshold)), None)
        strategies.append((f"wait_down_{int(threshold*100)}", *(hit or (None, None, len(path)))))
    for strategy, entry_date, entry, waited in strategies:
        entered = entry is not None
        subsequent = final / entry - 1 if entered else None
        later = [float(p) / entry - 1 for d, p in path if entered and d >= entry_date]
        drawdown = min(later) if later else None
        con.execute(
            "INSERT OR IGNORE INTO decision_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,TRUE,current_timestamp)",
            [fid, strategy, entry_date, entry, entered, entered and entry < start,
             not entered and final > start, subsequent, drawdown, waited]
        )


def _live_status(n: int, hit_rate: float | None) -> str:
    if n < 20:
        return "insufficient_live_sample"
    if n < 50:
        return "accumulating_live_evidence"
    if hit_rate is not None and hit_rate >= .55:
        return "promising_live_candidate"
    if hit_rate is not None and hit_rate < .45:
        return "degraded"
    return "accumulating_live_evidence"


def build_forecast_scorecards(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM forecast_scorecards")
    groups = con.execute(
        "SELECT r.secid,r.horizon_sessions,r.model_version,count(*) n,"
        "avg(CASE WHEN o.direction_correct THEN 1.0 WHEN o.direction_correct=false THEN 0.0 END),"
        "avg(o.actual_return),median(o.actual_return),avg(abs(o.actual_return-coalesce(r.median_return,0))),"
        "sqrt(avg(power(o.actual_return-coalesce(r.median_return,0),2))),"
        "avg(CASE WHEN o.inside_50_interval THEN 1.0 WHEN o.inside_50_interval=false THEN 0.0 END),"
        "avg(CASE WHEN o.inside_80_interval THEN 1.0 WHEN o.inside_80_interval=false THEN 0.0 END),"
        "avg(CASE WHEN o.inside_90_interval THEN 1.0 WHEN o.inside_90_interval=false THEN 0.0 END) "
        "FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured' GROUP BY 1,2,3"
    ).fetchall()
    for secid, horizon, version, n, hit, mean, median, mae, rmse, cov50, cov80, cov90 in groups:
        con.execute(
            "INSERT INTO forecast_scorecards VALUES ('secid',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?,current_timestamp)",
            [secid, horizon, version, n, n, hit, None, None, None, None, mean, median,
             mae, rmse, hit, None, None, cov50, cov80, cov90, None, None, None, _live_status(n, hit)]
        )
    con.execute("DELETE FROM model_version_scorecards")
    con.execute(
        "INSERT INTO model_version_scorecards SELECT model_family,model_version,min(cutoff),max(cutoff),"
        "count(*),count(*) FILTER(WHERE o.outcome_status='matured'),NULL,"
        "CASE WHEN count(*) FILTER(WHERE o.outcome_status='matured')<20 THEN 'insufficient_live_sample' "
        "ELSE 'accumulating_live_evidence' END,current_timestamp FROM forecast_registry r "
        "LEFT JOIN forecast_outcomes o USING(forecast_id) GROUP BY 1,2"
    )
    return {"scorecards": len(groups), "models": con.execute("SELECT count(*) FROM model_version_scorecards").fetchone()[0]}


def build_learning_journal(con) -> dict:
    ensure_schema(con)
    rows = con.execute(
        "SELECT r.forecast_id,r.secid,r.horizon_sessions,r.qualitative_direction,o.actual_return,"
        "o.direction_correct,o.neutral_hit,r.evidence_for_json,r.evidence_against_json,r.regime_status,"
        "r.model_version,r.limitations_json FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured'"
    ).fetchall()
    inserted = 0
    for row in rows:
        fid, secid, horizon, direction, actual, correct, neutral, evidence_for, evidence_against, regime, version, limits = row
        if correct is False:
            category = "false_positive" if direction == "small_positive" else "false_negative"
        elif direction == "neutral" and not neutral:
            category = "failed_neutral"
        else:
            category = "no_direction_error"
        misleading = _loads(evidence_for, []) if correct is False else []
        before = con.execute("SELECT count(*) FROM forecast_learning_journal WHERE forecast_id=?", [fid]).fetchone()[0]
        con.execute(
            "INSERT OR IGNORE INTO forecast_learning_journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [fid, secid, horizon, direction, f"Фактическая доходность {actual:.4f}",
             "Знак совпал" if correct else "Нейтраль оценена отдельно" if direction == "neutral" else "",
             "Знак не совпал" if correct is False else "", evidence_for, evidence_against,
             json.dumps(misleading, ensure_ascii=False), regime, json.dumps(_loads(limits, []), ensure_ascii=False),
             version, category, "Категория описывает совпадение, а не доказанную причинность"]
        )
        inserted += int(before == 0)
    return {"journal_entries": len(rows), "inserted": inserted}


def build_decision_scorecards(con) -> dict:
    """Persist explicitly hypothetical report-level decision snapshots."""
    ensure_schema(con)
    reports = con.execute(
        "SELECT report_id,analysis_cutoff,total_value,portfolio_snapshot_id FROM human_daily_reports"
    ).fetchall()
    for report_id, cutoff, value, snapshot_id in reports:
        statuses = dict(con.execute(
            "SELECT secid,action_group FROM human_instrument_synthesis WHERE report_id=?", [report_id]
        ).fetchall())
        risk = con.execute(
            "SELECT value FROM portfolio_risk_metrics WHERE snapshot_id=? AND metric='volatility' LIMIT 1",
            [snapshot_id],
        ).fetchone()
        con.execute(
            "INSERT OR IGNORE INTO portfolio_decision_scorecards VALUES (?,?,?,?,?,0,NULL,TRUE,current_timestamp)",
            [report_id, cutoff, value, risk[0] if risk else None, json.dumps(statuses)]
        )
    return {"hypothetical_reports": len(reports)}


def build_factor_scorecards(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM factor_live_scorecards")
    rows = con.execute(
        "SELECT r.horizon_sessions,r.regime_status,r.evidence_for_json,o.direction_correct,o.actual_return "
        "FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured'"
    ).fetchall()
    buckets = {}
    for horizon, regime, evidence, correct, actual in rows:
        for factor in _loads(evidence, []):
            buckets.setdefault((factor, horizon, regime), []).append((correct, actual))
    for (factor, horizon, regime), values in buckets.items():
        directional = [float(hit) for hit, _ in values if hit is not None]
        returns = [value for _, value in values]
        con.execute(
            "INSERT INTO factor_live_scorecards VALUES (?,?,?,?,?,?,?,?,current_timestamp)",
            [factor, horizon, regime, len(values), sum(directional),
             float(np.mean(directional)) if directional else None, float(np.mean(returns)),
             None, "live_observation_only"]
        )
    return {"factors": len(buckets)}


def forecast_track_record(con) -> dict:
    status = forecast_status(con)
    status["scorecards"] = con.execute("SELECT count(*) FROM forecast_scorecards").fetchone()[0]
    status["journal"] = con.execute("SELECT count(*) FROM forecast_learning_journal").fetchone()[0]
    return status


def forecast_status(con) -> dict:
    ensure_schema(con)
    total = con.execute("SELECT count(*) FROM forecast_registry").fetchone()[0]
    outcome_counts = dict(
        con.execute("SELECT outcome_status,count(*) FROM forecast_outcomes GROUP BY 1").fetchall()
    )
    matured = int(outcome_counts.get("matured", 0))
    pending_outcomes = int(outcome_counts.get("pending", 0))
    evaluated = con.execute(
        """SELECT count(DISTINCT forecast_id) FROM forecast_outcomes
        WHERE outcome_status='matured' AND evaluated_at IS NOT NULL"""
    ).fetchone()[0]
    pending = max(0, total - matured)
    versions = [row[0] for row in con.execute("SELECT DISTINCT model_version FROM forecast_registry ORDER BY 1").fetchall()]
    return {
        "total": total,
        "matured": matured,
        "pending": pending,
        "pending_outcome_records": pending_outcomes,
        "matured_outcome_records": matured,
        "evaluated": evaluated,
        "model_versions": versions,
        "live_status": _live_status(matured, None),
    }


def update_forecast_scorecards(con) -> dict:
    capture = capture_daily_forecasts(con)
    evaluation = evaluate_matured_forecasts(con)
    scorecards = build_forecast_scorecards(con)
    journal = build_learning_journal(con)
    decisions = build_decision_scorecards(con)
    factors = build_factor_scorecards(con)
    return {"capture": capture, "evaluation": evaluation, "scorecards": scorecards,
            "journal": journal, "decisions": decisions, "factors": factors,
            "status": forecast_status(con)}
