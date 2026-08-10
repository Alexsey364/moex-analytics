from datetime import date

import duckdb

from moex_analytics.portfolio_research.forecast_scorecards import ensure_schema as ensure_forecasts
from moex_analytics.portfolio_research.live_evidence import (
    build_forecast_paths,
    build_live_evidence_meter,
    build_maturity_calendar,
    build_sequential_monitor,
    classify_result,
    ensure_schema,
    evaluate_live_evidence,
    evidence_band,
    live_evidence_status,
)


def _database():
    con = duckdb.connect(":memory:")
    ensure_forecasts(con)
    ensure_schema(con)
    con.execute(
        "CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)"
    )
    con.execute(
        "INSERT INTO forecast_registry(forecast_id,cutoff,secid,horizon_sessions,model_family,"
        "model_version,current_price,qualitative_direction,probability_allowed,immutable) "
        "VALUES ('f1','2026-08-07','SBERP',1,'shadow','v1',300,'small_positive',FALSE,TRUE)"
    )
    return con


def test_evidence_bands_are_descriptive():
    assert [evidence_band(n) for n in (0, 10, 30, 50, 100)] == [
        "слишком мало", "начальная выборка", "накапливается",
        "предварительная оценка", "можно серьёзно сравнивать",
    ]


def test_pending_calendar_uses_unconfirmed_projection_only():
    con = _database()
    result = build_maturity_calendar(con)
    row = con.execute(
        "SELECT next_expected_maturity,maturity_trade_date,maturity_status,date_basis "
        "FROM forecast_maturity_calendar"
    ).fetchone()
    assert result["matured_confirmed"] == 0
    assert row == (date(2026, 8, 10), None, "pending", "weekday_projection_unconfirmed")


def test_actual_exchange_observation_confirms_maturity_once():
    con = _database()
    con.execute("INSERT INTO canonical_daily_prices VALUES ('SBERP','2026-08-10',303)")
    first = evaluate_live_evidence(con)
    second = evaluate_live_evidence(con)
    assert first["matured_new"] == 1
    assert second["matured_new"] == 0
    assert first["matured"] == second["matured"] == 1
    assert con.execute("SELECT count(*) FROM forecast_outcomes").fetchone()[0] == 1
    result = con.execute("SELECT result_classification FROM live_forecast_paths").fetchone()[0]
    assert result == "correct_direction"


def test_live_meter_and_monitor_do_not_promote_small_sample():
    con = _database()
    meter = build_live_evidence_meter(con)
    monitor = build_sequential_monitor(con)
    row = con.execute(
        "SELECT live_n,evidence_band,statistical_sufficiency FROM live_evidence_meter"
    ).fetchone()
    assert meter == {"meters": 1, "degradation_warnings": 0}
    assert monitor == {"monitors": 1, "research_review_recommended": 0}
    assert row == (0, "слишком мало", False)
    assert live_evidence_status(con)["confirmed_live_models"] == 0


def test_result_classification_covers_failure_modes():
    assert classify_result("unknown", None, None, None, None) == "model_abstained"
    assert classify_result("small_positive", False, None, False, 0) == "interval_miss"
    assert classify_result("small_positive", False, None, True, -0.12) == "large_adverse_move"
    assert classify_result("neutral", None, True, True, 0) == "neutral_hit"
    assert classify_result("neutral", None, False, True, 0) == "wrong_direction"


def test_path_builder_empty_is_safe():
    con = _database()
    assert build_forecast_paths(con) == {"matured_forecasts": 0, "path_rows": 0}
