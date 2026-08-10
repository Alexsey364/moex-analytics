"""Stage 39 live evidence readiness without retrospective forecast fabrication."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .forecast_scorecards import (
    build_forecast_scorecards,
    build_learning_journal,
    evaluate_matured_forecasts,
    forecast_status,
)

VERSION = "live-evidence-v1"

DDL = """
CREATE TABLE IF NOT EXISTS forecast_maturity_calendar(
 forecast_id VARCHAR PRIMARY KEY,cutoff DATE,secid VARCHAR,horizon_sessions INTEGER,
 observed_sessions INTEGER,sessions_remaining INTEGER,next_expected_maturity DATE,
 maturity_trade_date DATE,maturity_status VARCHAR,date_basis VARCHAR,calculated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS live_evidence_meter(
 secid VARCHAR,horizon_sessions INTEGER,model_family VARCHAR,model_version VARCHAR,
 historical_oos_n INTEGER,historical_direction_score DOUBLE,historical_mae DOUBLE,
 live_n INTEGER,live_direction_score DOUBLE,live_mae DOUBLE,live_calibration DOUBLE,
 drift_status VARCHAR,evidence_band VARCHAR,statistical_sufficiency BOOLEAN,
 updated_at TIMESTAMP,PRIMARY KEY(secid,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS sequential_evidence_monitor(
 secid VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,live_n INTEGER,
 accumulated_correct INTEGER,accumulated_wrong INTEGER,monitoring_method VARCHAR,
 multiplicity_control VARCHAR,degradation_warning BOOLEAN,research_review_recommended BOOLEAN,
 reasons_json JSON,calculated_at TIMESTAMP,
 PRIMARY KEY(secid,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS live_forecast_paths(
 forecast_id VARCHAR,trade_date DATE,session_number INTEGER,actual_close DOUBLE,
 actual_return DOUBLE,is_terminal BOOLEAN,result_classification VARCHAR,
 PRIMARY KEY(forecast_id,trade_date));
CREATE TABLE IF NOT EXISTS live_evidence_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,forecasts_total INTEGER,
 pending INTEGER,matured_before INTEGER,matured_new INTEGER,matured_total INTEGER,
 shadows_evaluated INTEGER,research_review_recommended BOOLEAN,production_changes INTEGER,
 probability_gate_changes INTEGER,details_json JSON);
"""


def ensure_schema(con) -> None:
    con.execute(DDL)


def evidence_band(n: int) -> str:
    """Descriptive accumulation band; never a statistical sufficiency claim."""
    if n < 10:
        return "слишком мало"
    if n < 30:
        return "начальная выборка"
    if n < 50:
        return "накапливается"
    if n < 100:
        return "предварительная оценка"
    return "можно серьёзно сравнивать"


def _project_session_date(start: date, sessions: int) -> date:
    """Weekday projection only; actual maturity always requires observed exchange prices."""
    value, remaining = start, sessions
    while remaining:
        value += timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value


def build_maturity_calendar(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM forecast_maturity_calendar")
    rows = con.execute(
        "SELECT forecast_id,cutoff,secid,horizon_sessions FROM forecast_registry "
        "ORDER BY cutoff,secid,horizon_sessions"
    ).fetchall()
    confirmed = 0
    for forecast_id, cutoff, secid, horizon in rows:
        observed_rows = con.execute(
            "SELECT trade_date FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>? "
            "ORDER BY trade_date LIMIT ?", [secid, cutoff, horizon]
        ).fetchall()
        observed = len(observed_rows)
        matured = observed >= horizon
        maturity_date = observed_rows[-1][0] if matured else None
        remaining = max(0, horizon - observed)
        anchor = observed_rows[-1][0] if observed_rows else cutoff
        expected = maturity_date if matured else _project_session_date(anchor, remaining)
        status = "matured_confirmed" if matured else "pending"
        basis = "observed_exchange_sessions" if matured else "weekday_projection_unconfirmed"
        con.execute(
            "INSERT INTO forecast_maturity_calendar VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [forecast_id, cutoff, secid, horizon, observed, remaining, expected, maturity_date,
             status, basis],
        )
        confirmed += int(matured)
    summary = con.execute(
        "SELECT horizon_sessions,count(*),min(next_expected_maturity),"
        "sum(CASE WHEN maturity_status='matured_confirmed' THEN 1 ELSE 0 END) "
        "FROM forecast_maturity_calendar GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {"forecasts": len(rows), "matured_confirmed": confirmed,
            "by_horizon": [{"horizon": h, "forecasts": n, "next_expected": d,
                            "matured": m} for h, n, d, m in summary]}


def classify_result(direction, direction_correct, neutral_hit, inside_90, adverse) -> str:
    if direction not in {"small_positive", "small_negative", "neutral"}:
        return "model_abstained"
    if inside_90 is False:
        return "interval_miss"
    if adverse is not None and adverse <= -0.10:
        return "large_adverse_move"
    if direction == "neutral":
        return "neutral_hit" if neutral_hit else "wrong_direction"
    return "correct_direction" if direction_correct else "wrong_direction"


def build_forecast_paths(con) -> dict:
    ensure_schema(con)
    matured = con.execute(
        "SELECT r.forecast_id,r.cutoff,r.secid,r.current_price,r.qualitative_direction,"
        "o.maturity_trade_date,o.direction_correct,o.neutral_hit,o.inside_90_interval,"
        "o.max_adverse_excursion FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured'"
    ).fetchall()
    con.execute("DELETE FROM live_forecast_paths")
    written = 0
    for row in matured:
        fid, cutoff, secid, start, direction, end, correct, neutral, inside90, adverse = row
        classification = classify_result(direction, correct, neutral, inside90, adverse)
        path = con.execute(
            "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? "
            "AND trade_date>? AND trade_date<=? ORDER BY trade_date", [secid, cutoff, end]
        ).fetchall()
        for number, (trade_date, close) in enumerate(path, 1):
            con.execute(
                "INSERT INTO live_forecast_paths VALUES (?,?,?,?,?,?,?)",
                [fid, trade_date, number, close, float(close / start - 1), trade_date == end,
                 classification if trade_date == end else None],
            )
            written += 1
    return {"matured_forecasts": len(matured), "path_rows": written}


def _historical_reference(con, secid, horizon):
    """Return compatible historical evidence when present; otherwise remain explicitly absent."""
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "issuer_evidence_results" not in tables:
        return None, None, None
    columns = {r[0] for r in con.execute("DESCRIBE issuer_evidence_results").fetchall()}
    required = {"secid", "horizon", "oos_n", "balanced_accuracy", "mae"}
    if not required.issubset(columns):
        return None, None, None
    return con.execute(
        "SELECT oos_n,balanced_accuracy,mae FROM issuer_evidence_results "
        "WHERE secid=? AND horizon=? ORDER BY calculated_at DESC LIMIT 1", [secid, horizon]
    ).fetchone() or (None, None, None)


def build_live_evidence_meter(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM live_evidence_meter")
    groups = con.execute(
        "SELECT r.secid,r.horizon_sessions,r.model_family,r.model_version,"
        "count(*) FILTER(WHERE o.outcome_status='matured') live_n,"
        "avg(CASE WHEN o.direction_correct THEN 1.0 WHEN o.direction_correct=false THEN 0.0 END),"
        "avg(abs(o.actual_return-coalesce(r.median_return,0))),"
        "avg(CASE WHEN o.inside_90_interval THEN 1.0 WHEN o.inside_90_interval=false THEN 0.0 END) "
        "FROM forecast_registry r LEFT JOIN forecast_outcomes o USING(forecast_id) GROUP BY 1,2,3,4"
    ).fetchall()
    warnings = 0
    for secid, horizon, family, version, live_n, hit, mae, calibration in groups:
        hist_n, hist_hit, hist_mae = _historical_reference(con, secid, horizon)
        drift = "insufficient_live_sample"
        if live_n >= 20 and hist_hit is not None and hit is not None:
            drift = "degradation_warning" if hit < hist_hit - 0.10 else "stable"
        warnings += int(drift == "degradation_warning")
        con.execute(
            "INSERT INTO live_evidence_meter VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [secid, horizon, family, version, hist_n, hist_hit, hist_mae, live_n, hit, mae,
             calibration, drift, evidence_band(live_n), live_n >= 100],
        )
    return {"meters": len(groups), "degradation_warnings": warnings}


def build_sequential_monitor(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM sequential_evidence_monitor")
    rows = con.execute(
        "SELECT secid,horizon_sessions,model_version,live_n,live_direction_score,drift_status "
        "FROM live_evidence_meter"
    ).fetchall()
    recommended = 0
    for secid, horizon, version, n, hit, drift in rows:
        correct = round(n * hit) if hit is not None else 0
        wrong = n - correct if hit is not None else 0
        reasons = []
        if n >= 50:
            reasons.append("total_or_model_live_sample_threshold")
        if n >= 30:
            reasons.append("specific_shadow_sample_threshold")
        if drift == "degradation_warning":
            reasons.append("significant_degradation")
        review = bool(reasons)
        recommended += int(review)
        con.execute(
            "INSERT INTO sequential_evidence_monitor VALUES (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [secid, horizon, version, n, correct, wrong, "accumulating_diagnostics",
             "single_cumulative_monitor_per_shadow", drift == "degradation_warning", review,
             json.dumps(reasons)],
        )
    return {"monitors": len(rows), "research_review_recommended": recommended}


def evaluate_live_evidence(con) -> dict:
    """Evaluate each immutable forecast once, then rebuild all live-only evidence layers."""
    ensure_schema(con)
    started = datetime.now()
    run_id = started.strftime("%Y%m%d%H%M%S%f")
    before = forecast_status(con)["matured"]
    evaluation = evaluate_matured_forecasts(con)
    calendar = build_maturity_calendar(con)
    build_forecast_scorecards(con)
    journal = build_learning_journal(con)
    paths = build_forecast_paths(con)
    meters = build_live_evidence_meter(con)
    sequential = build_sequential_monitor(con)
    status = forecast_status(con)
    review = sequential["research_review_recommended"] > 0 or status["matured"] >= 50
    details = {"evaluation": evaluation, "calendar": calendar, "journal": journal,
               "paths": paths, "meters": meters, "sequential": sequential}
    con.execute(
        "INSERT INTO live_evidence_runs VALUES (?,?,current_timestamp,?,?,?,?,?,?,?,?,?,?)",
        [run_id, started, status["total"], status["pending"], before, evaluation["matured"],
         status["matured"], meters["meters"], review, 0, 0, json.dumps(details, default=str)],
    )
    return {"run_id": run_id, **status, "matured_new": evaluation["matured"],
            "shadows_evaluated": meters["meters"], "research_review_recommended": review,
            "production_changes": 0, "probability_gate_changes": 0}


def live_evidence_status(con) -> dict:
    ensure_schema(con)
    status = forecast_status(con)
    status.update({
        "meters": con.execute("SELECT count(*) FROM live_evidence_meter").fetchone()[0],
        "confirmed_live_models": con.execute(
            "SELECT count(*) FROM live_evidence_meter WHERE statistical_sufficiency"
        ).fetchone()[0],
        "research_review_recommended": bool(con.execute(
            "SELECT count(*) FROM sequential_evidence_monitor WHERE research_review_recommended"
        ).fetchone()[0]),
        "production_changes": 0,
        "probability_gate_changes": 0,
    })
    return status
