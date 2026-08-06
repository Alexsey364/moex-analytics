from datetime import UTC, date, datetime

import duckdb
import pytest

from moex_analytics.sber_operational.core import (
    RULE_VERSION,
    audit_zones,
    calculate_nowcast,
    calculate_operating_state,
    calculate_scorecard,
    classify_value_kind,
    derive_period_value,
    discover,
    ensure_schema,
    explain_position_size,
    import_validated_fundamentals,
    run_daily,
    save_live_decision,
    status,
    update_outcomes,
    upsert_observation,
)


def connection():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute(
        "CREATE TABLE sber_decision_results(as_of_date DATE,decision_status VARCHAR,decision_confidence DOUBLE,first_position_fraction DOUBLE,current_price DOUBLE,run_id VARCHAR)"
    )
    con.execute(
        """CREATE TABLE fundamental_metric_values(
        document_id VARCHAR,metric_id VARCHAR,period_start DATE,period_end DATE,
        normalized_value DOUBLE,normalized_unit VARCHAR,publication_date DATE,
        available_from TIMESTAMPTZ,revision_id VARCHAR,source_note VARCHAR,
        secid VARCHAR,quality_status VARCHAR)"""
    )
    return con


def observation(metric, value, kind="ytd_flow", available="2024-05-15 10:00:00+03"):
    return {
        "metric_id": metric,
        "period_start": date(2024, 1, 1),
        "period_end": date(2024, 4, 30),
        "value_kind": kind,
        "reported_value": value,
        "unit": "RUB",
        "document_id": f"doc-{metric}",
        "publication_date": date(2024, 5, 15),
        "available_from": available,
        "revision_id": "original",
        "methodology_version": "2024",
        "comparability_status": "comparable",
        "confidence": 0.8,
        "source_url": "https://www.sberbank.com/investor-relations",
    }


@pytest.mark.parametrize(
    "kind", ["stock", "monthly_flow", "ytd_flow", "quarter_flow", "ttm", "annualized_run_rate"]
)
def test_value_kinds(kind):
    assert classify_value_kind(kind) == kind


def test_bad_kind_and_non_comparable_ytd():
    with pytest.raises(ValueError):
        classify_value_kind("flow")
    with pytest.raises(ValueError):
        derive_period_value(12, 10, comparable=False)
    assert derive_period_value(12, 10, comparable=True) == 2


def test_revision_and_point_in_time_nowcast():
    con = connection()
    upsert_observation(con, observation("net_profit", 400.0))
    revised = observation("net_profit", 410.0)
    revised["revision_id"] = "revision-2"
    revised["document_id"] = "doc-net-profit-revised"
    revised["available_from"] = "2024-05-20 10:00:00+03"
    upsert_observation(con, revised)
    upsert_observation(con, observation("total_equity", 2000.0, "stock"))
    early = calculate_nowcast(con, date(2024, 5, 16))
    late = calculate_nowcast(con, date(2024, 5, 21))
    assert early["ensemble"] == pytest.approx(1140.0)
    assert late["ensemble"] == pytest.approx(1168.5)
    assert con.execute("select count(*) from sber_operational_observations").fetchone()[0] == 3


def test_state_weight_zero_zones_and_size():
    con = connection()
    upsert_observation(con, observation("net_profit", 400.0))
    upsert_observation(con, observation("total_equity", 2000.0, "stock"))
    state = calculate_operating_state(con, date(2024, 5, 16))
    assert state["block_weight"] == 0
    assert state["annual_profit_nowcast"] > 0
    evidence = con.execute("select weight,status from sber_operational_evidence").fetchone()
    assert evidence == (0.0, "experimental_weight_zero")
    assert audit_zones(con, date(2024, 5, 16))["strong_word_removed"]
    assert explain_position_size(con, date(2024, 5, 16))["first_fraction"] == 0.1


def test_live_record_is_immutable_and_cutoff_unique():
    con = connection()
    con.execute("insert into canonical_daily_prices values ('2024-05-16','SBER',310)")
    con.execute(
        "insert into sber_decision_results values ('2024-05-16','initial_position',50.9,0.1,310,'run-1')"
    )
    upsert_observation(con, observation("net_profit", 400.0))
    upsert_observation(con, observation("total_equity", 2000.0, "stock"))
    calculate_operating_state(con, date(2024, 5, 16))
    cutoff = datetime(2024, 5, 16, 20, tzinfo=UTC)
    first = save_live_decision(con, date(2024, 5, 16), cutoff)
    second = save_live_decision(con, date(2024, 5, 16), cutoff)
    assert first["status"] == "success"
    assert second["status"] == "no_change"
    assert first["input_hash"] == second["input_hash"]
    assert con.execute("select count(*) from sber_live_predictions").fetchone()[0] == 1
    assert con.execute("select count(*) from sber_live_decisions").fetchone()[0] == 6
    assert (
        con.execute("select count(*) from sber_frozen_rules where rule_version=?", [RULE_VERSION]).fetchone()[
            0
        ]
        == 1
    )
    assert status(con)["sber_live_predictions"] == 1


def test_import_discovery_outcomes_scorecard_and_daily():
    con = connection()
    con.execute(
        """insert into fundamental_metric_values values
        ('official-profit','net_profit','2024-01-01','2024-04-30',400,'RUB',
        '2024-05-15','2024-05-15 10:00:00+03','original','official','SBER','validated'),
        ('official-equity','total_equity','2024-01-01','2024-04-30',2000,'RUB',
        '2024-05-15','2024-05-15 10:00:00+03','original','official','SBER','validated')"""
    )
    assert import_validated_fundamentals(con)["rows_written"] == 2
    assert import_validated_fundamentals(con)["rows_written"] == 0
    assert discover(con)["forms"] == ["101", "102", "806", "807"]
    for index, day in enumerate(range(16, 23)):
        con.execute(
            "insert into canonical_daily_prices values (?,'SBER',?)",
            [date(2024, 5, day), 300 + index],
        )
    con.execute(
        """insert into sber_decision_results values
        ('2024-05-16','initial_position',50.9,0.1,300,'run-1')"""
    )
    first = run_daily(con, date(2024, 5, 16))
    second = run_daily(con, date(2024, 5, 16))
    assert first["live"]["status"] == "success"
    assert second["live"]["status"] == "no_change"
    outcome = update_outcomes(con, date(2024, 5, 22))
    assert outcome["matured_rows"] >= 2
    card = calculate_scorecard(con, date(2024, 5, 22))
    assert card["historical_separated"]
    assert card["live_outcomes"] >= 2
