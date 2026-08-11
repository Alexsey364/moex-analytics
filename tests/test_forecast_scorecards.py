from datetime import date, datetime, timedelta

import duckdb
import pytest

from moex_analytics.portfolio_research.forecast_scorecards import (
    DDL,
    _live_status,
    build_forecast_scorecards,
    build_learning_journal,
    capture_daily_forecasts,
    direction_result,
    evaluate_matured_forecasts,
    forecast_status,
    trading_maturity,
)
from moex_analytics.portfolio_research.human_intelligence import DDL as HUMAN_DDL


@pytest.mark.parametrize(
    ("direction", "actual", "expected"),
    [
        ("↑", 0.02, (True, None)),
        ("↓", 0.02, (False, None)),
        ("→", 0.005, (None, True)),
        ("→", 0.02, (None, False)),
    ],
)
def test_direction_result_accepts_persisted_display_symbols(direction, actual, expected):
    assert direction_result(direction, actual) == expected


def _con(with_future=True):
    con = duckdb.connect(":memory:")
    con.execute(HUMAN_DDL)
    con.execute(DDL)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute("CREATE TABLE portfolio_risk_metrics(snapshot_id VARCHAR,metric VARCHAR,value DOUBLE)")
    con.execute(
        "INSERT INTO human_daily_reports VALUES "
        "('r1','2024-01-05',?, 's1',100,0.1,'normal',0,NULL,'hash','v1',TRUE)",
        [datetime(2024, 1, 5, 20)],
    )
    con.execute(
        "INSERT INTO human_instrument_synthesis VALUES ('r1','SBERP',100,1,90,.1,.11,"
        "'up','up','up','neutral','estimated','moderate','fit','wait','wait','средняя',50,"
        "'momentum','risk','[\"momentum\"]','[\"risk\"]','[\"regime change\"]','validated_current')"
    )
    for horizon in (1, 5, 20, 60, 120, 250):
        con.execute(
            "INSERT INTO human_horizon_views VALUES ('r1','SBERP',?,'small_positive','up','[]',50,'v1')",
            [horizon],
        )
    con.execute(
        "INSERT INTO canonical_daily_prices VALUES "
        "('2024-01-05','SBERP',100),('2024-01-05','IMOEX',100)"
    )
    if with_future:
        for index in range(1, 22):
            day = date(2024, 1, 5) + timedelta(days=index)
            con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?)", [day, "SBERP", 100 + index])
            con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?)", [day, "IMOEX", 100 + index / 2])
    return con


def test_immutable_capture_no_backfill_and_same_cutoff_no_duplicate():
    con = _con(False)
    first = capture_daily_forecasts(con)
    second = capture_daily_forecasts(con)
    assert first["inserted"] == 6
    assert second["status"] == "no_change" and second["inserted"] == 0
    assert con.execute("SELECT count(*),bool_and(immutable) FROM forecast_registry").fetchone() == (6, True)
    assert con.execute("SELECT min(cutoff),max(cutoff) FROM forecast_registry").fetchone() == (
        date(2024, 1, 5), date(2024, 1, 5))


def test_trading_session_maturity_and_pending():
    con = _con(True)
    assert trading_maturity(con, "SBERP", date(2024, 1, 5), 5)[0] == date(2024, 1, 10)
    assert trading_maturity(con, "SBERP", date(2024, 1, 5), 60) is None
    capture_daily_forecasts(con)
    result = evaluate_matured_forecasts(con)
    assert result["matured"] == 3 and result["pending"] == 3


def test_mature_once_actual_return_mae_mfe_touch_and_duplicate_evaluation():
    con = _con(True)
    capture_daily_forecasts(con)
    evaluate_matured_forecasts(con)
    row = con.execute(
        "SELECT actual_return,max_adverse_excursion,max_favorable_excursion,touched_up_3,"
        "direction_correct FROM forecast_outcomes o JOIN forecast_registry r USING(forecast_id) "
        "WHERE horizon_sessions=5"
    ).fetchone()
    assert row[0] == pytest.approx(.05)
    assert row[1] == pytest.approx(.01) and row[2] == pytest.approx(.05)
    assert row[3] and row[4]
    assert evaluate_matured_forecasts(con)["matured"] == 0
    assert con.execute("SELECT count(*) FROM forecast_outcomes").fetchone()[0] == 6


def test_direction_and_neutral_scoring_are_separate():
    assert direction_result("small_positive", .01) == (True, None)
    assert direction_result("small_negative", -.01) == (True, None)
    assert direction_result("neutral", .005) == (None, True)
    assert direction_result("neutral", .02) == (None, False)


def test_wait_vs_buy_is_hypothetical_and_lot_of_strategies():
    con = _con(True)
    capture_daily_forecasts(con)
    evaluate_matured_forecasts(con)
    rows = con.execute(
        "SELECT strategy,hypothetical FROM decision_outcomes WHERE forecast_id IN "
        "(SELECT forecast_id FROM forecast_registry WHERE horizon_sessions=5)"
    ).fetchall()
    assert {row[0] for row in rows} == {"buy_now", "wait_5", "wait_down_3", "wait_down_5"}
    assert all(row[1] for row in rows)


def test_version_separation_insufficient_sample_and_interval_coverage():
    con = _con(True)
    capture_daily_forecasts(con)
    evaluate_matured_forecasts(con)
    result = build_forecast_scorecards(con)
    assert result["models"] == 1
    assert con.execute("SELECT DISTINCT model_version FROM forecast_scorecards").fetchone()[0] == "v1"
    assert _live_status(10, .9) == "insufficient_live_sample"
    assert (
        con.execute("SELECT DISTINCT live_status FROM forecast_scorecards").fetchone()[0]
        == "insufficient_live_sample"
    )


def test_learning_journal_is_deterministic_and_no_causal_overclaim():
    con = _con(True)
    capture_daily_forecasts(con)
    evaluate_matured_forecasts(con)
    first = build_learning_journal(con)
    second = build_learning_journal(con)
    assert first["inserted"] == 3 and second["inserted"] == 0
    warning = con.execute("SELECT DISTINCT causality_warning FROM forecast_learning_journal").fetchone()[0]
    assert "не доказанную причинность" in warning


def test_forecast_status_counts_pending_and_matured():
    con = _con(True)
    capture_daily_forecasts(con)
    evaluate_matured_forecasts(con)
    status = forecast_status(con)
    assert status["total"] == 6 and status["matured"] == 3 and status["pending"] == 3
