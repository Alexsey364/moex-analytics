import duckdb

from moex_analytics.portfolio_research.human_intelligence import answer_question
from moex_analytics.transparency.core import (
    _investment_view,
    build_decision_trace,
    data_inventory,
    explain_current_decision,
    freshness_inventory,
    instrument_data_passport,
)


def test_investment_view_status_branches_are_portfolio_independent():
    assert _investment_view({}, False)[0] == "insufficient_data"
    assert _investment_view({"regime_status": "stress"}, True)[0] == "cautious"
    assert _investment_view(
        {"valuation_status": "undervalued", "fundamental_confidence": "high"}, True
    )[0] == "attractive"
    assert _investment_view({"valuation_status": "fair"}, True)[0] == "neutral"


def seeded():
    con = duckdb.connect(":memory:")
    con.execute(
        """CREATE TABLE historical_equity_universe(
        secid VARCHAR,is_traded BOOLEAN)"""
    )
    con.execute("INSERT INTO historical_equity_universe VALUES ('SBERP',TRUE),('OLD',FALSE),('EMPTY',TRUE)")
    con.execute("CREATE TABLE moex_equity_eod(secid VARCHAR,trade_date DATE)")
    con.execute("INSERT INTO moex_equity_eod VALUES ('SBERP','2026-08-07'),('OLD','2020-01-01')")
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('SBERP','2026-08-07'),('SBERP','2026-08-08')")
    con.execute(
        "CREATE TABLE issuer_fundamental_values(secid VARCHAR,period_end DATE,validation_status VARCHAR)"
    )
    con.execute("INSERT INTO issuer_fundamental_values VALUES ('SBERP','2025-12-31','validated')")
    con.execute("CREATE TABLE dividends(secid VARCHAR,record_date DATE)")
    con.execute("INSERT INTO dividends VALUES ('SBERP','2025-07-18')")
    con.execute("CREATE TABLE forecast_registry(forecast_id VARCHAR,secid VARCHAR,cutoff DATE)")
    con.execute("INSERT INTO forecast_registry VALUES ('f1','SBERP','2026-08-08')")
    con.execute(
        """CREATE TABLE forecast_outcomes(
        forecast_id VARCHAR,outcome_status VARCHAR,evaluated_at TIMESTAMP)"""
    )
    con.execute("INSERT INTO forecast_outcomes VALUES ('f1','pending',NULL)")
    con.execute("""CREATE TABLE portfolio_action_map(
        snapshot_id VARCHAR,secid VARCHAR,target_status VARCHAR,
        valuation_status VARCHAR,fundamental_confidence VARCHAR,regime_status VARCHAR,
        evidence_for_json JSON,evidence_against_json JSON)""")
    con.execute(
        """INSERT INTO portfolio_action_map VALUES
        ('s1','SBERP','target_not_set','experimental','low','normal',
        '["quality"]','["target_not_set","valuation evidence incomplete"]')"""
    )
    return con


def test_inventory_totals_and_freshness():
    con = seeded()
    inventory = data_inventory(con)
    assert inventory["totals"]["eod_rows"] == 2
    assert inventory["totals"]["catalog_securities"] == 3
    assert inventory["totals"]["securities_with_eod_history"] == 2
    assert inventory["totals"]["raw_eod_rows"] == 2
    assert inventory["totals"]["canonical_eod_rows"] == 2
    assert inventory["totals"]["active_securities_with_history"] == 1
    assert inventory["totals"]["inactive_securities_with_history"] == 1
    assert inventory["totals"]["pending_forecasts"] == 1
    assert inventory["totals"]["matured_forecasts"] == 0
    assert inventory["totals"]["pending_outcome_records"] == 1
    assert inventory["totals"]["matured_outcome_records"] == 0
    assert inventory["totals"]["evaluated_forecasts"] == 0
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
    assert explanation["final_status"] == "cautious"
    assert explanation["investment_view"]["status"] == "cautious"
    assert explanation["portfolio_allocation_view"]["status"] == "target_not_set"
    assert explanation["blocks_used"] == 13
    assert len(explanation["used"]) == 13
    assert len(explanation["excluded"]) == 6
    assert set(explanation["influential"]).isdisjoint(explanation["informational"])


def test_qa_explanation_is_built_from_trace_without_probability():
    answer = answer_question(seeded(), "Почему SBERP жёлтый?")
    assert answer["supported"] is True
    assert answer["conclusion"] == "SBERP: cautious"
    assert not any("probability" in item.lower() for item in answer["opposing_evidence"])
