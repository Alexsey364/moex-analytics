from datetime import date

import duckdb
import pytest

import moex_analytics.portfolio_research.live_validation as module
from moex_analytics.portfolio_research.forecast_scorecards import ensure_schema as forecast_schema
from moex_analytics.portfolio_research.live_validation import (
    _balanced_accuracy,
    _metrics,
    apply_review_decision,
    block_bootstrap_ci,
    build_error_diagnostics,
    build_feature_live_drift,
    build_live_duels,
    build_live_scorecards,
    build_rankings_and_gates,
    effective_sample_size,
    ensure_schema,
    evaluate_live_validation,
    live_validation_status,
    run_live_informed_research_cycle,
    sample_level,
    save_review_decision,
    update_research_trigger,
)
from moex_analytics.training_quality.schema import DDL as TRAINING_DDL


def _con():
    con = duckdb.connect(":memory:")
    forecast_schema(con)
    ensure_schema(con)
    con.execute(
        "CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)"
    )
    return con


def _matured(con, fid="f1", cutoff="2026-08-07", close=303.0, direction=True):
    con.execute(
        "INSERT INTO forecast_registry(forecast_id,cutoff,secid,horizon_sessions,model_family,"
        "model_version,current_price,qualitative_direction,probability_allowed,median_return,"
        "confidence,immutable,evidence_for_json,limitations_json) VALUES "
        "(?,?,'SBERP',1,'human','v1',300,'small_positive',FALSE,.005,.6,TRUE,'[\"momentum\"]',"
        "'[\"fresh\"]')", [fid, cutoff]
    )
    con.execute(
        "INSERT INTO forecast_outcomes VALUES (?, 'matured', '2026-08-10', ?, ?,NULL,-.01,.02,"
        "FALSE,FALSE,FALSE,FALSE,FALSE,FALSE,?,FALSE,TRUE,TRUE,TRUE,1,current_timestamp,'test')",
        [fid, close, close / 300 - 1, direction],
    )


def test_sample_policy_effective_n_hac_and_block_bootstrap():
    assert [sample_level(n) for n in (0, 10, 30, 50, 100)] == [
        "insufficient", "early_evidence", "accumulating", "preliminary_validation",
        "serious_live_evidence",
    ]
    effective, hac = effective_sample_size([1, 0] * 30, 20)
    assert 1 <= effective <= 3
    assert hac is not None
    assert effective_sample_size([1], 1) == (1.0, None)
    assert effective_sample_size([1, 1, 1], 20) == (1.0, 0.0)
    low, high = block_bootstrap_ci([1, 0] * 20, 20)
    assert low <= 0.5 <= high
    assert block_bootstrap_ci([1, 0], 1) == (None, None)


def test_metrics_collapse_duplicate_cutoffs_and_report_all_fields():
    rows = [
        (date(2026, 1, 1), "a", .02, .01, True, True, .04, -.01, True, True, True, None),
        (date(2026, 1, 1), "duplicate", .02, .01, True, True, .04, -.01, True, True, True, None),
        (date(2026, 1, 2), "b", -.01, 0, False, True, .01, -.03, False, True, True, True),
        (date(2026, 1, 3), "c", .01, 0, True, False, .02, -.02, True, True, True, False),
    ]
    result = _metrics(rows, 1)
    assert result["n"] == 4
    assert result["unique"] == 3
    assert result["direction"] == pytest.approx(2 / 3)
    assert result["mae"] > 0
    assert result["coverage90"] == 1
    assert _metrics([], 1)["sample"] == "insufficient"
    assert _balanced_accuracy([], []) is None


def test_scorecards_duels_rankings_gates_errors_and_feature_drift():
    con = _con()
    _matured(con)
    assert build_live_scorecards(con)["scorecards"] == 1
    score = con.execute(
        "SELECT observations,unique_cutoffs,effective_n,sample_status "
        "FROM live_validation_scorecards"
    ).fetchone()
    assert score == (1, 1, 1.0, "insufficient")
    assert build_live_duels(con) == {"duels": 4, "retrospective_reconstruction": False}
    current = con.execute(
        "SELECT matched_dates,status FROM live_model_duels "
        "WHERE competitor='production_or_fallback'"
    ).fetchone()
    assert current == (1, "current_reference_same_date")
    assert con.execute(
        "SELECT count(*) FROM live_model_duels WHERE status='unavailable_same_date'"
    ).fetchone()[0] == 3
    ranking = build_rankings_and_gates(con)
    assert ranking["automatic_promotion"] is False
    gate = con.execute(
        "SELECT eligible_for_review,automatic_promotion,probability_live_gate "
        "FROM live_promotion_readiness"
    ).fetchone()
    assert gate == (False, False, False)
    assert con.execute(
        "SELECT applied_to_production FROM live_confidence_adjustments"
    ).fetchone()[0] is False
    assert con.execute("SELECT model_version,immutable FROM live_shadow_versions").fetchone() == (
        "v1", True,
    )
    assert build_error_diagnostics(con) == {"errors": 1, "inserted": 1}
    assert build_error_diagnostics(con) == {"errors": 1, "inserted": 0}
    assert "no causal" in con.execute(
        "SELECT causality_warning FROM live_error_diagnostics"
    ).fetchone()[0]
    assert build_feature_live_drift(con) == {"features": 0}
    con.execute(
        "INSERT INTO factor_live_scorecards VALUES "
        "('momentum',1,'trend',12,8,.666,.01,NULL,'live',current_timestamp)"
    )
    assert build_feature_live_drift(con) == {"features": 1}
    assert con.execute("SELECT drift_status FROM live_feature_drift").fetchone()[0] == "insufficient_live"


def test_same_date_version_duel_is_real_not_reconstructed():
    con = _con()
    _matured(con)
    con.execute(
        "INSERT INTO forecast_registry(forecast_id,cutoff,secid,horizon_sessions,model_family,"
        "model_version,current_price,qualitative_direction,probability_allowed,confidence,immutable) "
        "VALUES ('f2','2026-08-07','SBERP',1,'shadow','shadow_v2',300,'small_negative',FALSE,.5,TRUE)"
    )
    con.execute(
        "INSERT INTO forecast_outcomes VALUES ('f2','matured','2026-08-10',303,.01,NULL,-.01,.02,"
        "FALSE,FALSE,FALSE,FALSE,FALSE,FALSE,FALSE,FALSE,TRUE,TRUE,TRUE,1,current_timestamp,'test')"
    )
    build_live_scorecards(con)
    result = build_live_duels(con)
    assert result["retrospective_reconstruction"] is False
    duel = con.execute(
        "SELECT matched_dates,model_score,competitor_score,status FROM live_model_duels "
        "WHERE model_version='v1' AND competitor='version:shadow_v2'"
    ).fetchone()
    assert duel == (1, 1.0, 0.0, "insufficient_live")


def test_research_trigger_and_holdout_only_versioning(monkeypatch):
    con = _con()
    monkeypatch.setattr(module, "forecast_status", lambda _con: {"matured": 50})
    con.execute(
        "INSERT INTO live_validation_scorecards VALUES "
        "('secid','SBERP',120,'v1',50,50,30,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,"
        "NULL,NULL,NULL,NULL,'accumulating',current_timestamp)"
    )
    trigger = update_research_trigger(con)
    assert trigger["triggered"]
    result = run_live_informed_research_cycle(con)
    assert result["live_used_as_holdout"] is True
    assert result["live_used_for_training"] is False
    assert result["production_changes"] == 0
    assert run_live_informed_research_cycle(_con())["status"] == "not_triggered"


def test_manual_review_requires_explicit_complete_evidence_and_applies_fundamental():
    con = _con()
    con.execute(TRAINING_DDL)
    con.execute(
        "INSERT INTO fundamental_manual_review_candidates VALUES "
        "('run','candidate','SBER','2025-12-31','revenue','official.pdf','p10','Revenue',"
        "100,'RUB',.8,'manual check','hash','pending')"
    )
    with pytest.raises(ValueError, match="reviewer"):
        save_review_decision(
            con, review_type="fundamental", candidate_id="candidate", decision="accept",
            reviewer="", evidence={},
        )
    with pytest.raises(ValueError, match="missing required evidence"):
        save_review_decision(
            con, review_type="fundamental", candidate_id="candidate", decision="accept",
            reviewer="analyst", evidence={},
        )
    evidence = {
        "reporting_standard": "IFRS", "publication_date": "2026-03-01", "source_hash": "hash"
    }
    saved = save_review_decision(
        con, review_type="fundamental", candidate_id="candidate", decision="accept",
        reviewer="analyst", evidence=evidence,
    )
    assert saved["applied"] is False
    applied = apply_review_decision(con, review_type="fundamental", candidate_id="candidate")
    assert applied["benchmark_changed"] is False
    assert con.execute(
        "SELECT validation_status FROM issuer_fundamental_values"
    ).fetchone()[0] == "manual_validated"
    assert apply_review_decision(
        con, review_type="fundamental", candidate_id="candidate"
    )["status"] == "already_applied"


def test_review_reject_and_validation_errors():
    con = _con()
    with pytest.raises(ValueError, match="unsupported"):
        save_review_decision(
            con, review_type="bad", candidate_id="x", decision="reject", reviewer="a", evidence={}
        )
    with pytest.raises(ValueError, match="accept or reject"):
        save_review_decision(
            con, review_type="fundamental", candidate_id="x", decision="maybe",
            reviewer="a", evidence={},
        )
    result = save_review_decision(
        con, review_type="fundamental", candidate_id="x", decision="reject",
        reviewer="a", evidence={},
    )
    assert result["production_changes"] == 0
    with pytest.raises(ValueError, match="accepted"):
        apply_review_decision(con, review_type="fundamental", candidate_id="x")


def test_orchestration_and_status_are_production_safe(monkeypatch):
    con = _con()
    monkeypatch.setattr(module, "evaluate_live_evidence", lambda _con: {
        "total": 108, "matured": 0, "pending": 108, "matured_new": 0,
        "production_changes": 0, "probability_gate_changes": 0,
    })
    monkeypatch.setattr(module, "build_live_scorecards", lambda _con: {"scorecards": 0})
    monkeypatch.setattr(module, "build_live_duels", lambda _con: {
        "duels": 0, "retrospective_reconstruction": False,
    })
    monkeypatch.setattr(module, "build_rankings_and_gates", lambda _con: {
        "rankings": 0, "eligible_for_review": 0, "automatic_promotion": False,
        "probability_allowed": 0,
    })
    monkeypatch.setattr(module, "build_feature_live_drift", lambda _con: {"features": 0})
    monkeypatch.setattr(module, "build_error_diagnostics", lambda _con: {"errors": 0, "inserted": 0})
    monkeypatch.setattr(module, "update_research_trigger", lambda _con: {
        "triggered": False, "reasons": [],
    })
    result = evaluate_live_validation(con)
    assert result["production_changes"] == 0
    assert result["probability_gate_changes"] == 0
    monkeypatch.setattr(module, "forecast_status", lambda _con: {"matured": 0, "total": 108})
    status = live_validation_status(con)
    assert status["eligible_for_review"] == status["probability_live_gates"] == 0
