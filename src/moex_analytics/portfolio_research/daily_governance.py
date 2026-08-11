"""Incremental daily orchestration and explicit model governance."""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta

import numpy as np

from moex_analytics import update_monitor

UPDATE_LEVELS = ("quick", "deep", "retrain")
DDL = """
CREATE TABLE IF NOT EXISTS dataset_update_state(dataset VARCHAR PRIMARY KEY,last_observation_date DATE,
 last_checked_at TIMESTAMP,next_expected_update TIMESTAMP,update_frequency VARCHAR,source VARCHAR,
 update_method VARCHAR,status VARCHAR,rows_inserted INTEGER,request_count INTEGER,
 duration_seconds DOUBLE,error VARCHAR);
CREATE TABLE IF NOT EXISTS daily_update_runs(run_id VARCHAR PRIMARY KEY,update_type VARCHAR,
 started_at TIMESTAMP,finished_at TIMESTAMP,duration_seconds DOUBLE,sources_checked INTEGER,
 http_requests INTEGER,rows_inserted INTEGER,rows_revised INTEGER,errors INTEGER,new_forecasts INTEGER,
 matured_forecasts INTEGER,status VARCHAR,no_change BOOLEAN,details_json JSON);
CREATE TABLE IF NOT EXISTS daily_update_requests(run_id VARCHAR,step INTEGER,dataset VARCHAR,source VARCHAR,
 date_from DATE,date_to DATE,overlap_days INTEGER,requests INTEGER,rows_inserted INTEGER,
 rows_revised INTEGER,status VARCHAR,error VARCHAR,duration_seconds DOUBLE,
 PRIMARY KEY(run_id,step,dataset));
CREATE TABLE IF NOT EXISTS model_registry(model_family VARCHAR,version VARCHAR,created_at TIMESTAMP,
 activated_at TIMESTAMP,retired_at TIMESTAMP,feature_version VARCHAR,training_range VARCHAR,
 validation_range VARCHAR,pseudo_oos_range VARCHAR,live_status VARCHAR,approval_status VARCHAR,
 config_hash VARCHAR,code_commit VARCHAR,notes VARCHAR,frozen BOOLEAN,PRIMARY KEY(model_family,version));
CREATE TABLE IF NOT EXISTS challenger_models(model_family VARCHAR,version VARCHAR,status VARCHAR,
 shadow_since DATE,forecasts INTEGER,matured_forecasts INTEGER,promotion_recommended BOOLEAN,
 explicit_approval BOOLEAN,reasons_json JSON,updated_at TIMESTAMP,PRIMARY KEY(model_family,version));
CREATE TABLE IF NOT EXISTS model_governance_metrics(model_family VARCHAR,version VARCHAR,horizon INTEGER,
 performance_window VARCHAR,matured INTEGER,hit_rate DOUBLE,brier DOUBLE,interval_coverage DOUBLE,mae DOUBLE,
 degradation_status VARCHAR,data_drift_status VARCHAR,concept_drift_status VARCHAR,
 retrain_suggestion BOOLEAN,reasons_json JSON,calculated_at TIMESTAMP,
 PRIMARY KEY(model_family,version,horizon,performance_window));
"""


def ensure_schema(con):
    con.execute(DDL)


def incremental_range(last_observation, today=None, overlap_days=5):
    today = today or date.today()
    return (
        (None, today)
        if last_observation is None
        else (last_observation - timedelta(days=overlap_days), today)
    )


def _latest(con):
    try:
        return con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    except Exception:
        return None


def _source(dataset):
    return {
        "prices": "MOEX ISS",
        "macro": "CBR/MOEX",
        "fundamentals": "issuer disclosures",
        "news_events": "governed official RSS",
        "dividends_events": "MOEX/issuer",
        "regimes": "local",
        "portfolio": "local",
        "forecasts": "local immutable report",
        "forecast_evaluation": "local prices",
    }[dataset]


def _finish(
    con, run_id, started, sources, requests, rows, errors, forecasts, matured, status, no_change, details
):
    duration = time.perf_counter() - started
    con.execute(
        "UPDATE daily_update_runs SET finished_at=current_timestamp,duration_seconds=?,sources_checked=?,"
        "http_requests=?,rows_inserted=?,rows_revised=0,errors=?,new_forecasts=?,matured_forecasts=?,"
        "status=?,no_change=?,details_json=? WHERE run_id=?",
        [
            duration,
            sources,
            requests,
            rows,
            errors,
            forecasts,
            matured,
            status,
            no_change,
            json.dumps(details, ensure_ascii=False),
            run_id,
        ],
    )
    from moex_analytics.transparency import update_receipt

    update_receipt(con, run_id)
    monitor_state = update_monitor.load()
    if monitor_state.get("run_id") == run_id:
        monitor_status = "completed" if status in {"no_change", "dry_run"} else status
        update_monitor.finish(monitor_state, monitor_status)
    return {
        "run_id": run_id,
        "duration_seconds": duration,
        "sources_checked": sources,
        "http_requests": requests,
        "rows_inserted": rows,
        "errors": errors,
        "new_forecasts": forecasts,
        "matured_forecasts": matured,
        "status": status,
        "no_change": no_change,
        **details,
    }


def run_daily_update(con, *, mode="quick", dry_run=False, fail_source=None, now=None):
    """Run bounded update with per-source failure isolation and smart skips."""
    if mode not in UPDATE_LEVELS:
        raise ValueError(f"unknown update mode: {mode}")
    ensure_schema(con)
    now, run_id, started = now or datetime.now(), uuid.uuid4().hex[:20], time.perf_counter()
    con.execute(
        "INSERT INTO daily_update_runs(run_id,update_type,started_at,status,no_change) "
        "VALUES (?,?,?,'running',FALSE)",
        [run_id, mode, now],
    )
    monitor_state = update_monitor.start(run_id, mode)
    if mode == "retrain":
        details = {
            "dry_run": dry_run,
            "planned": ["alpha research", "nested CV", "challengers"],
            "promotion": "blocked_without_explicit_approval",
        }
        return _finish(con, run_id, started, 0, 0, 0, 0, 0, 0, "dry_run", True, details)
    if mode == "deep" and dry_run:
        details = {
            "dry_run": True,
            "planned": [
                "issuer discovery",
                "missing documents",
                "quality audits",
                "feature rebuild",
                "historical checks",
            ],
        }
        return _finish(con, run_id, started, 5, 0, 0, 0, 0, 0, "dry_run", True, details)
    latest = _latest(con)
    start_date, end_date = incremental_range(latest, now.date(), 5)
    datasets = [
        "prices",
        "macro",
        "fundamentals",
        "news_events",
        "dividends_events",
        "regimes",
        "portfolio",
        "forecasts",
        "forecast_evaluation",
    ]
    total_requests = total_rows = errors = new_forecasts = matured = 0
    results, market_changed, evidence_result = [], False, {}
    for number, dataset in enumerate(datasets, 1):
        if update_monitor.cancel_requested():
            update_monitor.clear_cancel()
            return _finish(con, run_id, started, number - 1, total_requests, total_rows, errors,
                new_forecasts, matured, "cancelled", total_rows == 0,
                {"steps": results, "resume": "incremental_next_run"})
        began, requests, rows, status, error = time.perf_counter(), 0, 0, "smart_skip", None
        stage_meta = dict((item[0], item[1:]) for item in update_monitor.STAGES)[dataset]
        update_monitor.progress(monitor_state, dataset=dataset, stage=stage_meta[0],
                                source=stage_meta[1], status="running")
        try:
            if dataset == fail_source:
                raise RuntimeError(f"simulated {dataset} source failure")
            if dataset == "prices":
                if latest is None or (now.date() - latest).days > 3:
                    from moex_analytics.moex_client import MoexClient

                    from .core import build_portfolio_total_returns, download_portfolio_history

                    before = con.execute("SELECT count(*) FROM canonical_daily_prices").fetchone()[0]
                    def request_progress(*, dataset_name=dataset, **info):
                        monitor_state["retries"] += int(info["status"] == "retrying")
                        update_monitor.progress(monitor_state, dataset=dataset_name,
                            stage=f"MOEX request: {info['path'].split('?')[0]}",
                            source="MOEX ISS",
                            status="retrying" if info["status"] == "retrying" else "running",
                            requests=int(info["status"] == "completed"),
                            duration=info["duration"])

                    client = MoexClient(progress_callback=request_progress)
                    download_portfolio_history(con, client=client)
                    build_portfolio_total_returns(con)
                    after = con.execute("SELECT count(*) FROM canonical_daily_prices").fetchone()[0]
                    rows, requests = after - before, client.request_count
                    status, market_changed = "completed", after > before
                else:
                    status = "no_new_logical_cutoff"
            elif dataset == "forecasts":
                from .forecast_scorecards import capture_daily_forecasts

                result = capture_daily_forecasts(con)
                new_forecasts, status = result["inserted"], result["status"]
            elif dataset == "news_events":
                from moex_analytics.news_foundation.core import load_source_registry
                from moex_analytics.news_intelligence.core import ingest_live_news
                from moex_analytics.news_reaction.core import build_reaction_memory
                from moex_analytics.news_research.core import run_news_research

                if mode == "quick" and (now.weekday() >= 5 or latest == now.date()):
                    status = "smart_skip_no_new_market_cutoff"
                else:
                    load_source_registry(con)
                    result = ingest_live_news(con)
                    requests, rows = result["requests"], result["inserted"]
                    build_reaction_memory(con)
                    run_news_research(con)
                    status = result["status"]
            elif dataset == "forecast_evaluation":
                from .live_validation import evaluate_live_validation

                result = evaluate_live_validation(con)
                evidence_result = result
                matured = result["matured_new"]
                build_governance_metrics(con)
                status = "completed" if matured else "no_change"
            elif market_changed and dataset == "portfolio":
                from .human_intelligence import run_daily_intelligence

                run_daily_intelligence(con, update_data=False)
                status = "completed"
            elif mode == "deep":
                status = "scheduled_deep_component"
            else:
                status = "smart_skip_unchanged_input"
        except Exception as exc:
            errors += 1
            status = "failed_using_previous_snapshot"
            error = f"{type(exc).__name__}: {exc}"
        duration = time.perf_counter() - began
        update_monitor.progress(monitor_state, dataset=dataset, stage=stage_meta[0],
            source=stage_meta[1], status=status, requests=requests, rows=rows,
            error=error, duration=duration)
        con.execute(
            "INSERT INTO daily_update_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                number,
                dataset,
                _source(dataset),
                start_date,
                end_date,
                5,
                requests,
                rows,
                0,
                status,
                error,
                duration,
            ],
        )
        con.execute(
            "INSERT OR REPLACE INTO dataset_update_state VALUES (?, ?,current_timestamp,?,"
            "'daily',?,'incremental_with_revision_overlap',?,?,?,?,?)",
            [
                dataset,
                latest,
                now + timedelta(days=1),
                _source(dataset),
                status,
                rows,
                requests,
                duration,
                error,
            ],
        )
        total_requests += requests
        total_rows += rows
        results.append(
            {
                "step": number,
                "dataset": dataset,
                "status": status,
                "rows": rows,
                "requests": requests,
                "error": error,
            }
        )
    no_change = total_rows == 0 and new_forecasts == 0 and matured == 0
    status = "completed_with_warnings" if errors else "no_change" if no_change else "completed"
    return _finish(
        con,
        run_id,
        started,
        8,
        total_requests,
        total_rows,
        errors,
        new_forecasts,
        matured,
        status,
        no_change,
        {
            "steps": results,
            "forecasts_pending": evidence_result.get("pending", 0),
            "matured_today": matured,
            "matured_total": evidence_result.get("matured", matured),
            "shadow_models_evaluated": evidence_result.get("shadows_evaluated", 0),
        },
    )


def register_frozen_model(
    con, *, family, version, feature_version, ranges, config_hash, code_commit, approval="research"
):
    ensure_schema(con)
    con.execute(
        "INSERT OR IGNORE INTO model_registry VALUES (?,?,current_timestamp,current_timestamp,"
        "NULL,?,?,?,?,?,?,?,?,?,TRUE)",
        [
            family,
            version,
            feature_version,
            ranges.get("training"),
            ranges.get("validation"),
            ranges.get("pseudo_oos"),
            "insufficient_live_sample",
            approval,
            config_hash,
            code_commit,
            "Daily update cannot alter features, coefficients, thresholds or calibration",
        ],
    )
    return {"family": family, "version": version, "frozen": True, "approval": approval}


def register_challenger(con, family, version):
    ensure_schema(con)
    con.execute(
        "INSERT OR REPLACE INTO challenger_models VALUES (?,?,'shadow',current_date,0,0,"
        "FALSE,FALSE,'[\"live sample required\"]',current_timestamp)",
        [family, version],
    )
    return {"family": family, "version": version, "status": "shadow", "promotion": False}


def promotion_recommendation(
    *, matured, stable_by_regime, beats_baseline, beats_production, calibrated, leakage_free, structural_break
):
    checks = {
        "sufficient_live_sample": matured >= 100,
        "stable_by_regime": stable_by_regime,
        "beats_baseline": beats_baseline,
        "beats_production": beats_production,
        "calibration_acceptable": calibrated,
        "no_leakage": leakage_free,
        "no_structural_break": not structural_break,
    }
    return {"promote_candidate": all(checks.values()), "automatic_promotion": False, "checks": checks}


def population_stability_index(reference, current, bins=10):
    reference, current = np.asarray(reference, float), np.asarray(current, float)
    if len(reference) < bins or len(current) < bins:
        return None, "insufficient_sample"
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0, "stable"
    ref = np.histogram(reference, edges)[0] / len(reference)
    cur = np.histogram(current, edges)[0] / len(current)
    psi = float(np.sum((cur.clip(1e-6) - ref.clip(1e-6)) * np.log(cur.clip(1e-6) / ref.clip(1e-6))))
    return psi, "significant_drift" if psi >= 0.25 else "watch" if psi >= 0.1 else "stable"


def concept_drift_status(historical_ic, recent_ic):
    if historical_ic is None or recent_ic is None:
        return "insufficient_sample"
    if historical_ic * recent_ic < 0 or abs(recent_ic - historical_ic) >= 0.15:
        return "significant_drift"
    return "watch" if abs(recent_ic - historical_ic) >= 0.075 else "stable"


def degradation_status(recent_hits, historical_hit):
    if len(recent_hits) < 20:
        return "insufficient_live_sample"
    return "model_degradation_warning" if np.mean(recent_hits) < historical_hit - 0.10 else "stable"


def retrain_suggestion(*, new_matured, data_drift, concept_drift, degradation, structural_regime):
    reasons = []
    if new_matured >= 100:
        reasons.append("достаточно новых зрелых прогнозов")
    if data_drift == "significant_drift":
        reasons.append("значимый data drift")
    if concept_drift == "significant_drift":
        reasons.append("значимый concept drift")
    if degradation == "model_degradation_warning":
        reasons.append("ухудшение live-качества")
    if structural_regime:
        reasons.append("новый структурный режим")
    return {"suggest_research_retrain": bool(reasons), "automatic_retrain": False, "reasons": reasons}


def build_governance_metrics(con):
    """Keep rolling live windows separate by model and horizon."""
    ensure_schema(con)
    con.execute("DELETE FROM model_governance_metrics")
    models = con.execute(
        "SELECT DISTINCT model_family,model_version,horizon_sessions FROM forecast_registry"
    ).fetchall()
    written = 0
    for family, version, horizon in models:
        rows = con.execute(
            "SELECT o.direction_correct,o.actual_return FROM forecast_registry r "
            "JOIN forecast_outcomes o USING(forecast_id) WHERE r.model_family=? AND r.model_version=? "
            "AND r.horizon_sessions=? AND o.outcome_status='matured' ORDER BY o.evaluated_at DESC",
            [family, version, horizon],
        ).fetchall()
        historical_hits = [float(hit) for hit, _ in rows if hit is not None]
        historical_hit = float(np.mean(historical_hits)) if historical_hits else None
        for label, size in (("last_20", 20), ("last_50", 50), ("last_100", 100), ("all_live", None)):
            subset = rows if size is None else rows[:size]
            hits = [float(hit) for hit, _ in subset if hit is not None]
            returns = [value for _, value in subset]
            degradation = (
                degradation_status(hits, historical_hit)
                if historical_hit is not None
                else "insufficient_live_sample"
            )
            suggestion = retrain_suggestion(
                new_matured=len(rows),
                data_drift="insufficient_sample",
                concept_drift="insufficient_sample",
                degradation=degradation,
                structural_regime=False,
            )
            con.execute(
                "INSERT INTO model_governance_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                [
                    family,
                    version,
                    horizon,
                    label,
                    len(subset),
                    float(np.mean(hits)) if hits else None,
                    None,
                    None,
                    float(np.mean(np.abs(returns))) if returns else None,
                    degradation,
                    "insufficient_sample",
                    "insufficient_sample",
                    suggestion["suggest_research_retrain"],
                    json.dumps(suggestion["reasons"], ensure_ascii=False),
                ],
            )
            written += 1
    return {"rolling_metrics": written}


def governance_status(con):
    ensure_schema(con)
    return {
        "models": con.execute("SELECT count(*) FROM model_registry").fetchone()[0],
        "frozen": con.execute("SELECT count(*) FROM model_registry WHERE frozen").fetchone()[0],
        "challengers": con.execute("SELECT count(*) FROM challenger_models").fetchone()[0],
        "updates": con.execute("SELECT count(*) FROM daily_update_runs").fetchone()[0],
    }
