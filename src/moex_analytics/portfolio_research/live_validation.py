"""Stage 40 evidence-driven live validation with an explicit production boundary."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

import numpy as np

from .forecast_scorecards import forecast_status
from .live_evidence import evaluate_live_evidence

VERSION = "live-validation-v1"

DDL = """
CREATE TABLE IF NOT EXISTS live_validation_scorecards(
 dimension VARCHAR,dimension_value VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,
 observations INTEGER,unique_cutoffs INTEGER,effective_n DOUBLE,direction_accuracy DOUBLE,
 balanced_accuracy DOUBLE,mae DOUBLE,rmse DOUBLE,median_return_error DOUBLE,
 mean_favorable_excursion DOUBLE,mean_adverse_excursion DOUBLE,coverage_50 DOUBLE,
 coverage_80 DOUBLE,coverage_90 DOUBLE,neutral_hit_rate DOUBLE,hac_standard_error DOUBLE,
 bootstrap_ci_low DOUBLE,bootstrap_ci_high DOUBLE,sample_status VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(dimension,dimension_value,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS live_model_duels(
 secid VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,competitor VARCHAR,
 matched_dates INTEGER,effective_n DOUBLE,model_score DOUBLE,competitor_score DOUBLE,
 advantage DOUBLE,status VARCHAR,reason VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(secid,horizon_sessions,model_version,competitor));
CREATE TABLE IF NOT EXISTS live_model_rankings(
 secid VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,historical_rank INTEGER,
 live_rank INTEGER,combined_rank INTEGER,historical_status VARCHAR,live_status VARCHAR,
 disagreement VARCHAR,recommendation VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(secid,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS live_promotion_readiness(
 secid VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,historical_oos_ok BOOLEAN,
 untouched_holdout_ok BOOLEAN,live_effective_n_ok BOOLEAN,live_advantage_ok BOOLEAN,
 regimes_stable BOOLEAN,calibration_ok BOOLEAN,no_severe_drift BOOLEAN,abstention_ok BOOLEAN,
 probability_live_gate BOOLEAN,eligible_for_review BOOLEAN,automatic_promotion BOOLEAN,
 reasons_json JSON,calculated_at TIMESTAMP,
 PRIMARY KEY(secid,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS live_feature_drift(
 factor VARCHAR,horizon_sessions INTEGER,regime VARCHAR,historical_status VARCHAR,
 live_uses INTEGER,live_hits INTEGER,live_hit_rate DOUBLE,live_status VARCHAR,
 drift_status VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(factor,horizon_sessions,regime));
CREATE TABLE IF NOT EXISTS live_error_diagnostics(
 forecast_id VARCHAR PRIMARY KEY,secid VARCHAR,horizon_sessions INTEGER,prediction VARCHAR,
 actual_return DOUBLE,magnitude_error DOUBLE,direction_result VARCHAR,regime VARCHAR,
 model_disagreement VARCHAR,feature_state_json JSON,data_freshness VARCHAR,
 abstention_would_help BOOLEAN,causality_warning VARCHAR,created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS live_confidence_adjustments(
 secid VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,base_confidence DOUBLE,
 live_effective_n DOUBLE,live_component_weight DOUBLE,live_track_record DOUBLE,
 adjusted_confidence DOUBLE,applied_to_production BOOLEAN,calculated_at TIMESTAMP,
 PRIMARY KEY(secid,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS live_research_recommendations(
 recommendation_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,reason VARCHAR,
 matured_total INTEGER,max_shadow_effective_n DOUBLE,degradation_detected BOOLEAN,
 recommendation VARCHAR,automatic_cycle BOOLEAN,automatic_promotion BOOLEAN,status VARCHAR);
CREATE TABLE IF NOT EXISTS live_shadow_versions(
 secid VARCHAR,horizon_sessions INTEGER,model_family VARCHAR,model_version VARCHAR,
 parent_version VARCHAR,benchmark_hash VARCHAR,training_cutoff DATE,live_holdout_cutoff DATE,
 status VARCHAR,created_at TIMESTAMP,immutable BOOLEAN,
 PRIMARY KEY(secid,horizon_sessions,model_version));
CREATE TABLE IF NOT EXISTS live_research_cycles(
 cycle_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,trigger_id VARCHAR,
 benchmark_hash VARCHAR,new_version VARCHAR,live_used_as_holdout BOOLEAN,
 live_used_for_training BOOLEAN,status VARCHAR,production_changes INTEGER,details_json JSON);
CREATE TABLE IF NOT EXISTS stage40_review_decisions(
 review_type VARCHAR,candidate_id VARCHAR,decision VARCHAR,reviewer VARCHAR,reviewed_at TIMESTAMP,
 evidence_json JSON,applied BOOLEAN,apply_status VARCHAR,
 PRIMARY KEY(review_type,candidate_id));
CREATE TABLE IF NOT EXISTS issuer_fundamental_values(
 secid VARCHAR,metric VARCHAR,reporting_standard VARCHAR,period_start DATE,period_end DATE,
 publication_date DATE,available_from TIMESTAMP,source VARCHAR,document VARCHAR,page_table VARCHAR,
 raw_value DOUBLE,normalized_value DOUBLE,unit VARCHAR,validation_status VARCHAR,revision VARCHAR,
 document_id VARCHAR,source_hash VARCHAR,structured_field VARCHAR,parser_version VARCHAR,
 issuer VARCHAR,raw_unit VARCHAR,
 PRIMARY KEY(secid,metric,period_end,reporting_standard,revision));
CREATE TABLE IF NOT EXISTS live_validation_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,forecasts_total INTEGER,
 matured_new INTEGER,matured_total INTEGER,scorecards INTEGER,duels INTEGER,rankings INTEGER,
 errors_logged INTEGER,review_triggered BOOLEAN,production_changes INTEGER,
 probability_gate_changes INTEGER,details_json JSON);
"""


def ensure_schema(con) -> None:
    con.execute(DDL)


def sample_level(n: float) -> str:
    if n < 10:
        return "insufficient"
    if n < 30:
        return "early_evidence"
    if n < 50:
        return "accumulating"
    if n < 100:
        return "preliminary_validation"
    return "serious_live_evidence"


def effective_sample_size(values, horizon: int) -> tuple[float, float | None]:
    """HAC-style effective N and standard error for overlapping forecast outcomes."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return float(n), None
    centered = x - x.mean()
    gamma0 = float(np.dot(centered, centered) / n)
    if gamma0 <= 1e-15:
        return float(min(n, max(1, math.ceil(n / max(1, horizon))))), 0.0
    lag_max = min(n - 1, max(1, horizon - 1))
    long_variance = gamma0
    for lag in range(1, lag_max + 1):
        gamma = float(np.dot(centered[lag:], centered[:-lag]) / n)
        long_variance += 2 * (1 - lag / (lag_max + 1)) * gamma
    long_variance = max(long_variance, gamma0 / n)
    hac_se = math.sqrt(long_variance / n)
    autocorrelation_neff = n * gamma0 / long_variance
    overlap_cap = max(1.0, n / max(1, horizon)) if horizon > 5 else float(n)
    return float(max(1.0, min(n, autocorrelation_neff, overlap_cap))), float(hac_se)


def block_bootstrap_ci(values, horizon: int, iterations: int = 500) -> tuple[float | None, float | None]:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return None, None
    block = min(len(x), max(1, min(horizon, math.ceil(math.sqrt(len(x))))))
    rng = np.random.default_rng(40)
    means = []
    for _ in range(iterations):
        sample = []
        while len(sample) < len(x):
            start = int(rng.integers(0, len(x)))
            sample.extend(x.take(np.arange(start, start + block) % len(x)).tolist())
        means.append(float(np.mean(sample[: len(x)])))
    return tuple(float(v) for v in np.quantile(means, [0.025, 0.975]))


def _balanced_accuracy(actual, predicted) -> float | None:
    actual, predicted = np.asarray(actual, bool), np.asarray(predicted, bool)
    scores = []
    for label in (False, True):
        mask = actual == label
        if mask.any():
            scores.append(float(np.mean(predicted[mask] == label)))
    return float(np.mean(scores)) if scores else None


def _metrics(rows, horizon: int) -> dict:
    if not rows:
        return {"n": 0, "unique": 0, "effective": 0.0, "sample": "insufficient"}
    # Same-cutoff duplicates are one evidence unit, not independent observations.
    by_cutoff = {}
    for row in rows:
        by_cutoff.setdefault(row[0], row)
    unique_rows = list(by_cutoff.values())
    actual = np.array([float(r[2]) for r in unique_rows])
    predicted_return = np.array([float(r[3] or 0) for r in unique_rows])
    direction_pairs = [(r[4], r[2]) for r in unique_rows if r[4] is not None]
    direction = np.array([float(bool(r[5])) for r in unique_rows if r[5] is not None])
    effective, hac = effective_sample_size(direction, horizon)
    low, high = block_bootstrap_ci(direction, horizon)
    actual_labels = [pair[1] > 0 for pair in direction_pairs]
    predicted_labels = [bool(pair[0]) for pair in direction_pairs]
    return {
        "n": len(rows), "unique": len(unique_rows), "effective": effective,
        "direction": float(direction.mean()) if len(direction) else None,
        "balanced": _balanced_accuracy(actual_labels, predicted_labels),
        "mae": float(np.mean(np.abs(actual - predicted_return))),
        "rmse": float(np.sqrt(np.mean((actual - predicted_return) ** 2))),
        "median_error": float(np.median(actual - predicted_return)),
        "mfe": float(np.mean([r[6] for r in unique_rows])),
        "mae_excursion": float(np.mean(np.abs([r[7] for r in unique_rows]))),
        "coverage50": _optional_mean([r[8] for r in unique_rows]),
        "coverage80": _optional_mean([r[9] for r in unique_rows]),
        "coverage90": _optional_mean([r[10] for r in unique_rows]),
        "neutral": _optional_mean([r[11] for r in unique_rows]),
        "hac": hac, "low": low, "high": high, "sample": sample_level(effective),
    }


def _optional_mean(values):
    present = [float(value) for value in values if value is not None]
    return float(np.mean(present)) if present else None


def build_live_scorecards(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM live_validation_scorecards")
    groups = con.execute(
        "SELECT DISTINCT secid,horizon_sessions,model_version FROM forecast_registry ORDER BY 1,2,3"
    ).fetchall()
    written = 0
    for secid, horizon, version in groups:
        rows = con.execute(
            "SELECT r.cutoff,r.forecast_id,o.actual_return,r.median_return,"
            "CASE WHEN r.qualitative_direction IN ('small_positive','↑') THEN TRUE "
            "WHEN r.qualitative_direction IN ('small_negative','↓') THEN FALSE END,"
            "CASE WHEN r.qualitative_direction IN ('small_positive','↑') THEN o.actual_return>0 "
            "WHEN r.qualitative_direction IN ('small_negative','↓') THEN o.actual_return<0 END,"
            "o.max_favorable_excursion,o.max_adverse_excursion,"
            "o.inside_50_interval,o.inside_80_interval,o.inside_90_interval,"
            "CASE WHEN r.qualitative_direction IN ('neutral','→') "
            "THEN abs(o.actual_return)<=0.01 END "
            "FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
            "WHERE r.secid=? AND r.horizon_sessions=? AND r.model_version=? "
            "AND o.outcome_status='matured' ORDER BY r.cutoff", [secid, horizon, version]
        ).fetchall()
        metrics = _metrics(rows, horizon)
        con.execute(
            "INSERT INTO live_validation_scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            ["secid", secid, horizon, version, metrics["n"], metrics["unique"],
             metrics["effective"], metrics.get("direction"), metrics.get("balanced"),
             metrics.get("mae"), metrics.get("rmse"), metrics.get("median_error"),
             metrics.get("mfe"), metrics.get("mae_excursion"), metrics.get("coverage50"),
             metrics.get("coverage80"), metrics.get("coverage90"), metrics.get("neutral"),
             metrics.get("hac"), metrics.get("low"), metrics.get("high"), metrics["sample"]],
        )
        written += 1
    return {"scorecards": written}


def build_live_duels(con) -> dict:
    """Only compare immutable forecasts sharing real cutoff dates; never reconstruct competitors."""
    ensure_schema(con)
    con.execute("DELETE FROM live_model_duels")
    models = con.execute(
        "SELECT DISTINCT secid,horizon_sessions,model_version FROM forecast_registry"
    ).fetchall()
    written = 0
    competitors = ("baseline", "production_or_fallback", "best_historical_shadow", "challenger_shadow")
    for secid, horizon, version in models:
        live = con.execute(
            "SELECT effective_n,direction_accuracy,unique_cutoffs FROM live_validation_scorecards "
            "WHERE dimension='secid' AND dimension_value=? AND horizon_sessions=? AND model_version=?",
            [secid, horizon, version],
        ).fetchone() or (0, None, 0)
        for competitor in competitors:
            if competitor == "production_or_fallback":
                matched, competitor_score, advantage = live[2], live[1], 0.0 if live[1] is not None else None
                status = "current_reference_same_date"
                reason = "current immutable forecast registry reference"
            else:
                matched, competitor_score, advantage = 0, None, None
                status = "unavailable_same_date"
                reason = "competitor forecast was not captured immutably on the same live cutoff dates"
            con.execute(
                "INSERT INTO live_model_duels VALUES (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                [secid, horizon, version, competitor, matched, live[0], live[1],
                 competitor_score, advantage, status, reason],
            )
            written += 1
        other_versions = con.execute(
            "SELECT DISTINCT model_version FROM forecast_registry WHERE secid=? "
            "AND horizon_sessions=? AND model_version<>?", [secid, horizon, version]
        ).fetchall()
        for (other_version,) in other_versions:
            matched_rows = con.execute(
                "SELECT a.cutoff,oa.direction_correct,ob.direction_correct "
                "FROM forecast_registry a JOIN forecast_outcomes oa ON oa.forecast_id=a.forecast_id "
                "JOIN forecast_registry b ON b.secid=a.secid AND b.horizon_sessions=a.horizon_sessions "
                "AND b.cutoff=a.cutoff JOIN forecast_outcomes ob ON ob.forecast_id=b.forecast_id "
                "WHERE a.secid=? AND a.horizon_sessions=? AND a.model_version=? "
                "AND b.model_version=? AND oa.outcome_status='matured' AND ob.outcome_status='matured' "
                "AND oa.direction_correct IS NOT NULL AND ob.direction_correct IS NOT NULL",
                [secid, horizon, version, other_version],
            ).fetchall()
            unique = {row[0]: row[1:] for row in matched_rows}
            model_hits = [float(pair[0]) for pair in unique.values()]
            other_hits = [float(pair[1]) for pair in unique.values()]
            advantages = [a - b for a, b in zip(model_hits, other_hits, strict=True)]
            effective, _ = effective_sample_size(advantages, horizon)
            model_score = _optional_mean(model_hits)
            competitor_score = _optional_mean(other_hits)
            advantage = (
                model_score - competitor_score
                if model_score is not None and competitor_score is not None else None
            )
            status = (
                "insufficient_live"
                if effective < 10 else "degraded" if advantage is not None and advantage < 0
                else "continue_shadow"
            )
            reason = (
                "recommend retire_shadow; explicit approval required"
                if status == "degraded" and effective >= 30 else "same-date immutable live comparison"
            )
            con.execute(
                "INSERT INTO live_model_duels VALUES (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                [secid, horizon, version, f"version:{other_version}", len(unique), effective,
                 model_score, competitor_score, advantage, status, reason],
            )
            written += 1
    return {"duels": written, "retrospective_reconstruction": False}


def _historical_status(con, secid, horizon):
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "issuer_evidence_results" not in tables:
        return None, None
    return con.execute(
        "SELECT status,improvement FROM issuer_evidence_results WHERE secid=? AND horizon=? "
        "ORDER BY CASE status WHEN 'SHADOW_CANDIDATE' THEN 0 WHEN 'WEAK_EVIDENCE' THEN 1 ELSE 2 END,"
        "improvement DESC NULLS LAST LIMIT 1", [secid, horizon]
    ).fetchone() or (None, None)


def build_rankings_and_gates(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM live_model_rankings")
    con.execute("DELETE FROM live_promotion_readiness")
    con.execute("DELETE FROM live_confidence_adjustments")
    rows = con.execute(
        "SELECT dimension_value,horizon_sessions,model_version,effective_n,direction_accuracy,"
        "sample_status FROM live_validation_scorecards WHERE dimension='secid'"
    ).fetchall()
    for secid, horizon, version, effective, live_score, live_status in rows:
        benchmark = con.execute(
            "SELECT benchmark_hash FROM issuer_evidence_benchmarks ORDER BY frozen_at DESC LIMIT 1"
        ).fetchone() if "issuer_evidence_benchmarks" in {
            item[0] for item in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        } else None
        cutoff = con.execute(
            "SELECT max(cutoff) FROM forecast_registry WHERE secid=? AND horizon_sessions=? "
            "AND model_version=?", [secid, horizon, version]
        ).fetchone()[0]
        con.execute(
            "INSERT OR IGNORE INTO live_shadow_versions VALUES "
            "(?,?, 'registered_live_model',?,NULL,?,?,?,'live_reference',current_timestamp,TRUE)",
            [secid, horizon, version, benchmark[0] if benchmark else None, cutoff, cutoff],
        )
        historical_status, historical_advantage = _historical_status(con, secid, horizon)
        disagreement = (
            "historical_positive_live_insufficient"
            if historical_advantage is not None and historical_advantage > 0 and effective < 30
            else "insufficient_comparable_evidence"
        )
        recommendation = "continue_shadow" if effective < 30 else "eligible_for_diagnostic_review"
        con.execute(
            "INSERT INTO live_model_rankings VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [secid, horizon, version, 1 if historical_advantage is not None else None,
             None if effective < 10 else 1, None, historical_status, live_status,
             disagreement, recommendation],
        )
        historical_ok = historical_status == "SHADOW_CANDIDATE"
        holdout_ok = False
        live_n_ok = effective >= 100
        advantage_ok = False
        probability_gate = live_n_ok and advantage_ok
        reasons = [name for name, passed in {
            "historical_oos": historical_ok, "untouched_holdout": holdout_ok,
            "live_effective_n": live_n_ok, "live_advantage": advantage_ok,
            "regime_stability": False, "calibration": False, "no_severe_drift": True,
            "abstention": False, "probability_live_gate": probability_gate,
        }.items() if not passed]
        con.execute(
            "INSERT INTO live_promotion_readiness VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [secid, horizon, version, historical_ok, holdout_ok, live_n_ok, advantage_ok,
             False, False, True, False, probability_gate, False, False, json.dumps(reasons)],
        )
        base = con.execute(
            "SELECT avg(confidence) FROM forecast_registry WHERE secid=? AND horizon_sessions=? "
            "AND model_version=?", [secid, horizon, version]
        ).fetchone()[0]
        base = float(base or 0)
        weight = min(0.25, effective / 400)
        adjusted = base if live_score is None else (1 - weight) * base + weight * live_score
        con.execute(
            "INSERT INTO live_confidence_adjustments VALUES (?,?,?,?,?,?,?,?,FALSE,current_timestamp)",
            [secid, horizon, version, base, effective, weight, live_score, adjusted],
        )
    return {"rankings": len(rows), "eligible_for_review": 0, "automatic_promotion": False,
            "probability_allowed": 0}


def build_feature_live_drift(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM live_feature_drift")
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "factor_live_scorecards" not in tables:
        return {"features": 0}
    rows = con.execute(
        "SELECT factor,horizon_sessions,regime,uses,hits,hit_rate FROM factor_live_scorecards"
    ).fetchall()
    for factor, horizon, regime, uses, hits, hit_rate in rows:
        status = sample_level(uses)
        con.execute(
            "INSERT INTO live_feature_drift VALUES (?,?,?,'historical_reference_not_comparable',"
            "?,?,?,?,?,current_timestamp)",
            [factor, horizon, regime, uses, hits, hit_rate, status,
             "insufficient_live" if uses < 30 else "requires_same_sample_review"],
        )
    return {"features": len(rows)}


def build_error_diagnostics(con) -> dict:
    ensure_schema(con)
    rows = con.execute(
        "SELECT r.forecast_id,r.secid,r.horizon_sessions,r.qualitative_direction,o.actual_return,"
        "abs(o.actual_return-coalesce(r.median_return,0)),o.direction_correct,o.neutral_hit,"
        "r.regime_status,r.evidence_for_json,r.limitations_json FROM forecast_registry r "
        "JOIN forecast_outcomes o USING(forecast_id) WHERE o.outcome_status='matured'"
    ).fetchall()
    inserted = 0
    for row in rows:
        fid, secid, horizon, prediction, actual, error, correct, neutral, regime, features, limits = row
        is_neutral = prediction in {"neutral", "→"}
        neutral = abs(float(actual)) <= 0.01 if is_neutral else None
        if prediction in {"small_positive", "↑"}:
            correct = float(actual) > 0
        elif prediction in {"small_negative", "↓"}:
            correct = float(actual) < 0
        result = "neutral_hit" if neutral else "correct" if correct else "wrong"
        before = con.execute("SELECT count(*) FROM live_error_diagnostics WHERE forecast_id=?", [fid]).fetchone()[0]
        con.execute(
            "INSERT OR REPLACE INTO live_error_diagnostics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [fid, secid, horizon, prediction, actual, error, result, regime,
             "competitor_same_date_unavailable", features, limits, correct is False,
             "diagnostic association only; no causal explanation"],
        )
        inserted += int(before == 0)
    return {"errors": len(rows), "inserted": inserted}


def update_research_trigger(con) -> dict:
    ensure_schema(con)
    matured = forecast_status(con)["matured"]
    max_effective = con.execute(
        "SELECT coalesce(max(effective_n),0) FROM live_validation_scorecards"
    ).fetchone()[0]
    degradation = bool(con.execute(
        "SELECT count(*) FROM live_model_duels WHERE status='degraded'"
    ).fetchone()[0])
    reasons = []
    if matured >= 50:
        reasons.append("matured_total_at_least_50")
    if max_effective >= 30:
        reasons.append("shadow_effective_n_at_least_30")
    if degradation:
        reasons.append("significant_degradation")
    if not reasons:
        return {"triggered": False, "reasons": []}
    digest = hashlib.sha256("|".join(reasons).encode()).hexdigest()[:20]
    con.execute(
        "INSERT OR IGNORE INTO live_research_recommendations VALUES (?,current_timestamp,?,?,?,?,"
        "'Пора провести новый исследовательский learning cycle.',FALSE,FALSE,'pending_review')",
        [digest, json.dumps(reasons), matured, max_effective, degradation],
    )
    return {"triggered": True, "recommendation_id": digest, "reasons": reasons,
            "automatic_cycle": False, "automatic_promotion": False}


def evaluate_live_validation(con) -> dict:
    ensure_schema(con)
    started = datetime.now(UTC)
    evidence = evaluate_live_evidence(con)
    scorecards = build_live_scorecards(con)
    duels = build_live_duels(con)
    rankings = build_rankings_and_gates(con)
    features = build_feature_live_drift(con)
    errors = build_error_diagnostics(con)
    trigger = update_research_trigger(con)
    today_results = [
        {"secid": row[0], "horizon": row[1], "result": row[2]}
        for row in con.execute(
            "SELECT r.secid,r.horizon_sessions,d.direction_result FROM live_error_diagnostics d "
            "JOIN forecast_outcomes o USING(forecast_id) JOIN forecast_registry r USING(forecast_id) "
            "WHERE o.evaluated_at>=? ORDER BY r.secid,r.horizon_sessions", [started]
        ).fetchall()
    ]
    run_id = hashlib.sha256(f"stage40:{started.isoformat()}".encode()).hexdigest()[:20]
    details = {"evidence": evidence, "features": features, "trigger": trigger,
               "today_results": today_results,
               "retrospective_forecasts": False, "auto_refit": False}
    con.execute(
        "INSERT INTO live_validation_runs VALUES (?,?,current_timestamp,?,?,?,?,?,?,?,?,?,?,?)",
        [run_id, started, evidence["total"], evidence["matured_new"], evidence["matured"],
         scorecards["scorecards"], duels["duels"], rankings["rankings"], errors["inserted"],
         trigger["triggered"], 0, 0, json.dumps(details, default=str)],
    )
    return {**evidence, **scorecards, **duels, **rankings, "run_id": run_id,
            "errors_logged": errors["inserted"], "research_trigger": trigger,
            "today_results": today_results,
            "production_changes": 0, "probability_gate_changes": 0}


def live_validation_status(con) -> dict:
    ensure_schema(con)
    status = forecast_status(con)
    status.update({
        "scorecards": con.execute("SELECT count(*) FROM live_validation_scorecards").fetchone()[0],
        "eligible_for_review": con.execute(
            "SELECT count(*) FROM live_promotion_readiness WHERE eligible_for_review"
        ).fetchone()[0],
        "probability_live_gates": con.execute(
            "SELECT count(*) FROM live_promotion_readiness WHERE probability_live_gate"
        ).fetchone()[0],
        "production_changes": 0,
    })
    return status


def save_review_decision(con, *, review_type: str, candidate_id: str, decision: str,
                         reviewer: str, evidence: dict) -> dict:
    """Persist an explicit human decision; parser candidates are never auto-accepted."""
    ensure_schema(con)
    if review_type not in {"fundamental", "corporate_action"}:
        raise ValueError("unsupported review type")
    if decision not in {"accept", "reject"}:
        raise ValueError("decision must be accept or reject")
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    required = (
        {"reporting_standard", "publication_date", "source_hash"}
        if review_type == "fundamental" and decision == "accept"
        else {"official_source", "document_hash", "publication_date", "effective_date"}
        if review_type == "corporate_action" and decision == "accept"
        else set()
    )
    missing = sorted(required - {key for key, value in evidence.items() if value})
    if missing:
        raise ValueError(f"missing required evidence: {', '.join(missing)}")
    apply_status = (
        "accepted_pending_explicit_apply" if decision == "accept" else "rejected_no_data_change"
    )
    con.execute(
        "INSERT OR REPLACE INTO stage40_review_decisions VALUES "
        "(?,?,?,?,current_timestamp,?,FALSE,?)",
        [review_type, candidate_id, decision, reviewer, json.dumps(evidence, default=str), apply_status],
    )
    return {"review_type": review_type, "candidate_id": candidate_id, "decision": decision,
            "applied": False, "production_changes": 0}


def apply_review_decision(con, *, review_type: str, candidate_id: str) -> dict:
    """Apply one explicitly accepted decision to research data, preserving raw and benchmarks."""
    ensure_schema(con)
    decision = con.execute(
        "SELECT decision,evidence_json,applied FROM stage40_review_decisions "
        "WHERE review_type=? AND candidate_id=?", [review_type, candidate_id]
    ).fetchone()
    if not decision or decision[0] != "accept":
        raise ValueError("an explicit accepted decision is required")
    if decision[2]:
        return {"status": "already_applied", "production_changes": 0}
    evidence = json.loads(decision[1])
    if review_type == "fundamental":
        candidate = con.execute(
            "SELECT issuer,period,metric,document,page_table,candidate_value,unit,source_hash "
            "FROM fundamental_manual_review_candidates WHERE candidate_id=?", [candidate_id]
        ).fetchone()
        if not candidate:
            raise ValueError("fundamental candidate not found")
        issuer, period, metric, document, page_table, value, unit, source_hash = candidate
        standard = evidence["reporting_standard"].upper()
        if standard not in {"IFRS", "RAS"}:
            raise ValueError("reporting_standard must be IFRS or RAS")
        publication = evidence["publication_date"]
        revision = f"manual-{candidate_id[:12]}"
        con.execute(
            "INSERT OR IGNORE INTO issuer_fundamental_values VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [issuer, metric, standard, None, period, publication, f"{publication} 23:59:59",
             "manual_review_official_document", document, page_table, value, value, unit,
             "manual_validated", revision, candidate_id, source_hash, None, VERSION, issuer, unit],
        )
        con.execute(
            "UPDATE fundamental_manual_review_candidates SET status='manual_validated' "
            "WHERE candidate_id=?", [candidate_id]
        )
        apply_status = "fundamental_applied_research_pit"
    else:
        candidate = con.execute(
            "SELECT secid,candidate_ratio,candidate_type FROM corporate_action_candidate_episodes "
            "WHERE episode_id=?", [candidate_id]
        ).fetchone()
        if not candidate:
            raise ValueError("corporate-action candidate not found")
        secid, ratio, action_type = candidate
        evidence_id = hashlib.sha256(f"manual:{candidate_id}".encode()).hexdigest()[:24]
        con.execute(
            "INSERT OR IGNORE INTO corporate_action_evidence VALUES "
            "(?,?,?,?,current_timestamp,?,?,?,?,?,?,?,'validated',?)",
            [evidence_id, candidate_id, evidence["official_source"], evidence["official_source"],
             evidence["document_hash"], evidence["publication_date"], evidence["effective_date"],
             action_type, ratio, secid, secid,
             json.dumps({"manual_review": True, "review_id": candidate_id})],
        )
        con.execute(
            "UPDATE corporate_action_candidate_episodes SET evidence_status='manual_official_validated',"
            "review_status='auto_validated' WHERE episode_id=?", [candidate_id]
        )
        from moex_analytics.training_quality.corporate_actions import _adjusted_prices, _quality

        _adjusted_prices(con)
        _quality(con)
        apply_status = "corporate_action_applied_research_panel_rebuilt"
    con.execute(
        "UPDATE stage40_review_decisions SET applied=TRUE,apply_status=? "
        "WHERE review_type=? AND candidate_id=?", [apply_status, review_type, candidate_id]
    )
    return {"status": apply_status, "production_changes": 0, "benchmark_changed": False}


def run_live_informed_research_cycle(con) -> dict:
    """Version a research cycle only after a trigger; live remains an untouched holdout."""
    ensure_schema(con)
    trigger = con.execute(
        "SELECT recommendation_id FROM live_research_recommendations WHERE status='pending_review' "
        "ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not trigger:
        return {"status": "not_triggered", "production_changes": 0}
    tables = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    benchmark = (
        con.execute(
            "SELECT benchmark_hash FROM issuer_evidence_benchmarks ORDER BY frozen_at DESC LIMIT 1"
        ).fetchone()
        if "issuer_evidence_benchmarks" in tables
        else None
    )
    benchmark_hash = benchmark[0] if benchmark else "no_historical_benchmark"
    cycle_id = hashlib.sha256(f"{trigger[0]}:{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:20]
    version = f"shadow_v{con.execute('SELECT count(*)+2 FROM live_research_cycles').fetchone()[0]}"
    con.execute(
        "INSERT INTO live_research_cycles VALUES (?,current_timestamp,current_timestamp,?,?,?,?,?,"
        "'completed_holdout_only',0,?)",
        [cycle_id, trigger[0], benchmark_hash, version, True, False,
         json.dumps({"thresholds_changed": False, "live_training": False})],
    )
    return {"cycle_id": cycle_id, "new_version": version, "benchmark_hash": benchmark_hash,
            "live_used_as_holdout": True, "live_used_for_training": False,
            "production_changes": 0}
