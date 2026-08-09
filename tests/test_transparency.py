import duckdb

from moex_analytics.portfolio_research.human_intelligence import answer_question
from moex_analytics.transparency.core import (
    build_decision_trace,
    data_inventory,
    explain_current_decision,
    freshness_inventory,
    instrument_data_passport,
)


def seeded():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('SBERP','2026-08-07'),('SBERP','2026-08-08')")
    con.execute(
        "CREATE TABLE issuer_fundamental_values(secid VARCHAR,period_end DATE,validation_status VARCHAR)"
    )
    con.execute("INSERT INTO issuer_fundamental_values VALUES ('SBERP','2025-12-31','validated')")
    con.execute("CREATE TABLE dividends(secid VARCHAR,record_date DATE)")
    con.execute("CREATE TABLE forecast_registry(secid VARCHAR,as_of_date DATE,status VARCHAR)")
    con.execute("INSERT INTO forecast_registry VALUES ('SBERP','2026-08-08','pending')")
    con.execute("""CREATE TABLE portfolio_action_map(
        snapshot_id VARCHAR,secid VARCHAR,target_status VARCHAR,
        evidence_for_json JSON,evidence_against_json JSON)""")
    con.execute(
        "INSERT INTO portfolio_action_map VALUES ('s1','SBERP','yellow','[\"quality\"]','[\"stress\"]')"
    )
    return con


def test_inventory_totals_and_freshness():
    con = seeded()
    inventory = data_inventory(con)
    assert inventory["totals"]["eod_rows"] == 2
    assert inventory["totals"]["pending_forecasts"] == 1
    equity = next(x for x in freshness_inventory(con) if x["dataset"] == "Акции EOD")
    assert equity["observations"] == 2


def test_instrument_passport_uses_exact_instrument():
    passport = instrument_data_passport(seeded(), "sberp")
    assert passport["price"]["rows"] == 2
    assert passport["fundamentals"]["rows"] == 1


def test_decision_trace_is_immutable_and_has_exclusions():
    con = seeded()
    first = build_decision_trace(con, "SBERP")
    second = build_decision_trace(con, "SBERP")
    assert first["decision_id"] == second["decision_id"]
    assert con.execute("SELECT count(*) FROM transparency_decision_traces").fetchone()[0] == 1
    explanation = explain_current_decision(con, "SBERP")
    assert explanation["excluded"]
    assert explanation["summary"]["probability_disclosed"] is False
    assert explanation["final_status"] == "yellow"


def test_qa_explanation_is_built_from_trace_without_probability():
    answer = answer_question(seeded(), "Почему SBERP жёлтый?")
    assert answer["supported"] is True
    assert answer["conclusion"] == "SBERP: yellow"
    assert not any("probability" in item.lower() for item in answer["opposing_evidence"])
