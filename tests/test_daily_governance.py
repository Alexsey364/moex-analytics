from datetime import date, datetime

import duckdb
import numpy as np

from moex_analytics.portfolio_research.daily_governance import (
    DDL,
    concept_drift_status,
    degradation_status,
    incremental_range,
    population_stability_index,
    promotion_recommendation,
    register_challenger,
    register_frozen_model,
    retrain_suggestion,
    run_daily_update,
)
from moex_analytics.portfolio_research.forecast_scorecards import DDL as FORECAST_DDL
from moex_analytics.portfolio_research.human_intelligence import DDL as HUMAN_DDL


def _con():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.execute(FORECAST_DDL)
    con.execute(HUMAN_DDL)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2026-08-07','SBERP',100)")
    return con


def test_incremental_date_range_has_revision_overlap_and_no_full_backfill():
    start, end = incremental_range(date(2026, 8, 7), date(2026, 8, 8), 5)
    assert start == date(2026, 8, 2) and end == date(2026, 8, 8)


def test_weekend_quick_no_change_same_cutoff_and_request_log():
    con = _con()
    result = run_daily_update(con, now=datetime(2026, 8, 8, 12))
    assert result["no_change"] and result["http_requests"] == 0
    assert con.execute("SELECT count(*) FROM daily_update_requests").fetchone()[0] == 8
    assert con.execute("SELECT status FROM daily_update_requests WHERE dataset='prices'").fetchone()[0] == (
        "no_new_logical_cutoff"
    )


def test_failed_source_tolerance_uses_previous_snapshot():
    con = _con()
    result = run_daily_update(con, fail_source="fundamentals", now=datetime(2026, 8, 8, 12))
    assert result["errors"] == 1 and result["status"] == "completed_with_warnings"
    assert "simulated" in con.execute(
        "SELECT error FROM daily_update_requests WHERE dataset='fundamentals'"
    ).fetchone()[0]


def test_quick_deep_and_retrain_are_separate():
    con = _con()
    deep = run_daily_update(con, mode="deep", dry_run=True)
    retrain = run_daily_update(con, mode="retrain", dry_run=True)
    assert deep["status"] == retrain["status"] == "dry_run"
    assert "historical checks" in deep["planned"]
    assert retrain["promotion"] == "blocked_without_explicit_approval"


def test_model_freeze_challenger_shadow_and_promotion_blocked():
    con = _con()
    model = register_frozen_model(
        con, family="direction", version="v1", feature_version="f1",
        ranges={"training": "a", "validation": "b", "pseudo_oos": "c"},
        config_hash="h", code_commit="abc",
    )
    challenger = register_challenger(con, "direction", "v2")
    recommendation = promotion_recommendation(
        matured=10, stable_by_regime=True, beats_baseline=True, beats_production=True,
        calibrated=True, leakage_free=True, structural_break=False,
    )
    assert model["frozen"] and challenger["status"] == "shadow"
    assert not recommendation["promote_candidate"] and not recommendation["automatic_promotion"]
    assert con.execute("SELECT frozen FROM model_registry").fetchone()[0]


def test_data_drift_concept_drift_and_degradation_are_distinct():
    reference = np.linspace(0, 1, 100)
    psi, status = population_stability_index(reference, reference + 10)
    assert psi > .25 and status == "significant_drift"
    assert concept_drift_status(.1, -.1) == "significant_drift"
    assert degradation_status([False] * 20, .6) == "model_degradation_warning"


def test_retrain_is_suggestion_not_automatic_action():
    result = retrain_suggestion(new_matured=5, data_drift="significant_drift",
        concept_drift="stable", degradation="stable", structural_regime=False)
    assert result["suggest_research_retrain"]
    assert not result["automatic_retrain"]


def test_launcher_defaults_to_quick_update():
    batch = open("run_daily_analysis.bat", encoding="utf-8").read()
    launcher = open("src/moex_analytics/launcher.py", encoding="utf-8").read()
    assert "moex_analytics.launcher --daily-only" in batch
    assert '"quick-daily-update"' in launcher
    assert "deep-update" not in launcher and "model-research" not in launcher
