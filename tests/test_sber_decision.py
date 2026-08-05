from datetime import date

import duckdb
import pytest

from moex_analytics.database import SCHEMA
from moex_analytics.sber_decision.engine import _weighted_quantile, calculate
from moex_analytics.sber_decision.explanations import render
from moex_analytics.sber_decision.models import Evidence
from moex_analytics.sber_decision.rules import decide
from moex_analytics.sber_decision.triggers import build as triggers
from moex_analytics.sber_decision.validation import annualize, ytd_to_period
from moex_analytics.sber_decision.zones import build_zones, staged_plan


@pytest.fixture
def con():
    db = duckdb.connect(":memory:")
    db.execute(SCHEMA)
    yield db
    db.close()


def test_ytd_conversion_and_guards():
    assert ytd_to_period(120, 80, comparable=True, current_months=9, previous_months=6)["value"] == 40
    assert (
        ytd_to_period(120, 80, comparable=True, current_months=9, previous_months=6, revised=True)["value"]
        is None
    )
    assert (
        ytd_to_period(120, 80, comparable=False, current_months=9, previous_months=6)["status"]
        == "not_comparable"
    )
    assert annualize(30, 3) == 120 and annualize(30, 0) is None


def test_guidance_point_in_time(con):
    con.execute(
        "INSERT INTO sber_guidance VALUES ('g',DATE '2024-02-01',DATE '2024-12-31','roe',.2,.25,NULL,'ratio','official','d',TIMESTAMPTZ '2024-02-01 10:00:00+03','active',NULL,current_timestamp)"
    )
    assert (
        con.execute(
            "SELECT count(*) FROM sber_guidance WHERE available_from<=TIMESTAMPTZ '2024-01-31 23:00:00+03'"
        ).fetchone()[0]
        == 0
    )


def test_weighted_ensemble_prefers_bank_method():
    assert _weighted_quantile([(200, 0.15), (300, 0.35), (400, 0.5)], 0.5) == 300


def test_decision_hierarchy_and_critical_override():
    blocks = [
        Evidence("data_quality", 0, 70, "ok"),
        Evidence("business_quality", 0.5, 70, "ok"),
        Evidence("valuation", 0.4, 60, "ok"),
        Evidence("dividend", 0.3, 60, "ok"),
        Evidence("technical", 0, 50, "ok"),
        Evidence("risk", 0, 50, "ok"),
        Evidence("macro", 1, 100, "rejected_excluded"),
    ]
    assert decide(blocks).status == "допустима поэтапная покупка"
    assert decide(blocks, critical_error=True).status == "недостаточно данных"


def test_conflicting_blocks_lower_confidence():
    base = [
        Evidence("data_quality", 0, 80, "ok"),
        Evidence("business_quality", 0.5, 80, "ok"),
        Evidence("valuation", 0.4, 80, "ok"),
        Evidence("dividend", 0.3, 80, "ok"),
        Evidence("technical", -0.5, 80, "ok"),
        Evidence("risk", 0, 80, "ok"),
    ]
    result = decide(base)
    assert result.conflicts and result.confidence < 80


def test_rejected_macro_cannot_change_result():
    blocks = [
        Evidence("data_quality", 0, 70, "ok"),
        Evidence("business_quality", 0.2, 60, "ok"),
        Evidence("valuation", 0, 60, "ok"),
        Evidence("dividend", 0, 60, "ok"),
        Evidence("technical", 0, 60, "ok"),
        Evidence("risk", 0, 60, "ok"),
    ]
    assert (
        decide(blocks + [Evidence("macro", -1, 100, "rejected_excluded")]).status
        == decide(blocks + [Evidence("macro", 1, 100, "rejected_excluded")]).status
    )


def test_zones_rounding_and_plan():
    zones = build_zones(243, 351, 171, 449)
    assert all(z["low"] is None or z["low"] % 5 == 0 for z in zones)
    assert zones[-2]["action"] == "не догонять цену"
    plan = staged_plan(100000, 0.6, zones)
    assert sum(plan[k] for k in ("first", "second", "after_report", "reserve")) == 60000


def test_triggers_and_reverse_thresholds():
    result = triggers(285, 245, 72, 0.22, 35)
    assert {x["category"] for x in result} == {"market", "fundamental", "information"}


def test_explanation_is_deterministic():
    text = render("наблюдать", 42, ["факт"], ["риск"], ["конфликт"])
    assert "Макрослой исключён" in text and "не вероятность" in text


def test_partial_dashboard_import():
    from moex_analytics.dashboard.pages import sber_decision

    assert callable(sber_decision.render_decision)


def test_no_future_data_guard(con):
    con.execute(
        "INSERT INTO sber_guidance VALUES ('future',DATE '2025-01-01',DATE '2025-12-31','roe',NULL,NULL,.2,'ratio','x','d',TIMESTAMPTZ '2025-01-01 10:00:00+03','active',NULL,current_timestamp)"
    )
    assert (
        con.execute(
            "SELECT count(*) FROM sber_guidance WHERE available_from<=TIMESTAMPTZ '2024-12-31 23:59:00+03'"
        ).fetchone()[0]
        == 0
    )


def test_calculate_needs_daily_state(con):
    assert calculate(con, date(2024, 1, 1))["status"] == "insufficient_data"


def test_integrated_decision_pipeline(con):
    from datetime import datetime, timedelta

    from moex_analytics.sber_decision.engine import (
        backtest,
        build_daily_state,
        calculate_dividend_outlook,
        calculate_ensemble,
    )

    start = date(2023, 1, 1)
    prices = [
        (
            start + timedelta(days=i),
            "SBER",
            "SBER",
            "TQBR",
            100 + i * 0.2,
            101 + i * 0.2,
            99 + i * 0.2,
            100 + i * 0.2,
            100 + i * 0.2,
            1.0,
            1.0,
            1,
            1,
            datetime.now(),
        )
        for i in range(300)
    ]
    con.executemany("INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", prices)
    available = datetime(2023, 1, 2, 10)
    for metric, value in (("net_profit", 1_000_000_000_000.0), ("total_equity", 5_000_000_000_000.0)):
        con.execute(
            """INSERT INTO fundamental_metric_values VALUES
            ('doc','SBER',?,?,'RUB',?,'RUB','identity','RAS',DATE '2022-01-01',
            DATE '2022-12-31',DATE '2023-01-02',?,'1','table','note','original',
            'validated',current_timestamp)""",
            [metric, value, value, available],
        )
    con.execute(
        "INSERT INTO fundamental_confidence VALUES (DATE '2023-01-02','SBER',70,60,40,'{}','v',current_timestamp)"
    )
    con.execute(
        "INSERT INTO dividends VALUES ('SBER',DATE '2022-07-01',NULL,NULL,20,'RUB','MOEX',current_timestamp,NULL)"
    )
    as_of = prices[-1][0]
    for scenario in ("base", "stress"):
        for method, value in (("pe", 180.0), ("pb_roe", 200.0), ("dividend_discount", 120.0)):
            con.execute(
                """INSERT INTO valuation_results VALUES
                (?,'SBER',?,?,?,20,NULL,100,220,'{}','sber-fact-valuation-v1',current_timestamp)""",
                [as_of, scenario, method, value],
            )
    assert build_daily_state(con)["rows"] == 299
    assert calculate_dividend_outlook(con, as_of)["rows"] == 3
    assert calculate_ensemble(con, as_of)["rows"] == 6
    first = calculate(con, as_of)
    assert first["status"] == "success"
    assert len(first["zones"]) == 6
    assert calculate(con, as_of)["status"] == "no_change"
    replay = backtest(con)
    assert replay["rows"] > 0
    assert con.execute("SELECT count(*) FROM sber_decision_evidence").fetchone()[0] == 10
