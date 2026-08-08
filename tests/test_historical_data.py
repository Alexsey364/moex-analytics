from datetime import date

import duckdb
import pandas as pd
import pytest

from moex_analytics.cli import build_parser
from moex_analytics.historical_data.core import (
    DATASET_FAMILIES,
    PORTFOLIO_GROUPS,
    audit_corporate_actions,
    audit_dividends,
    build_coverage_matrix,
    complete_historical_data_audit,
    detect_dividend_duplicates,
    ensure_schema,
    pit_integrity_score,
    priority_score,
    same_sample_ablation,
    validate_futures_units,
    validate_sector_membership,
)


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute("""
        CREATE TABLE canonical_daily_prices(
            trade_date DATE, canonical_secid VARCHAR, value DOUBLE
        );
        CREATE TABLE daily_returns(trade_date DATE, canonical_secid VARCHAR);
        CREATE TABLE dividends(
            canonical_secid VARCHAR, registry_close_date DATE,
            dividend_per_share DOUBLE, currency VARCHAR, source VARCHAR
        );
        CREATE TABLE fundamental_observations(
            secid VARCHAR, period_end DATE, accounting_standard VARCHAR
        );
    """)
    ensure_schema(connection)
    yield connection
    connection.close()


def test_coverage_matrix_has_every_instrument_family(con):
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2020-01-03','SBER',100)")
    result = build_coverage_matrix(con)
    assert result["rows"] == len(PORTFOLIO_GROUPS) * len(DATASET_FAMILIES)
    row = con.execute("SELECT observation_count,current_status FROM historical_data_coverage WHERE instrument='SBER' AND dataset_family='EOD prices'").fetchone()
    assert row == (1, "partial")


def test_actual_tradable_universe_replaces_zero_membership_without_claiming_index(con):
    con.execute("""CREATE TABLE tradable_on_date_universe(
        trade_date DATE,secid VARCHAR,inactive_at_audit BOOLEAN)""")
    con.execute("INSERT INTO tradable_on_date_universe VALUES ('2001-01-03','OLD',true)")
    build_coverage_matrix(con)
    broad = con.execute("""SELECT observation_count,source FROM historical_data_coverage
        WHERE instrument='SBER' AND dataset_family='broad universe'""").fetchone()
    assert broad == (1, "MOEX ISS trade history")


def test_priority_is_ordinal_and_paid_gap_is_not_critical_precision():
    critical = priority_score(relevance=3, depth_gain=3, pit=3, predictive_value=3, cross_section=3, cost=0, complexity=0, license_risk=0)
    paid = priority_score(relevance=3, depth_gain=3, pit=3, predictive_value=3, cross_section=3, cost=3, complexity=1, license_risk=3)
    assert critical["status"] == "critical"
    assert paid["status"] in {"paid_optional", "medium", "low"}
    with pytest.raises(ValueError):
        priority_score(relevance=4, depth_gain=0, pit=0, predictive_value=0, cross_section=0, cost=0, complexity=0, license_risk=0)


def test_pit_integrity_penalizes_leakage_and_missing_vintages():
    clean = pit_integrity_score(has_available_from=True, publication_order_valid=True, revision_support=True, duplicates=0, impossible_dates=0, stale_ratio=0, frequency_match=True)
    leaking = pit_integrity_score(has_available_from=False, publication_order_valid=False, revision_support=False, duplicates=2, impossible_dates=1, stale_ratio=0.5, frequency_match=False)
    assert clean == 100
    assert leaking < 20


def test_basis_disabled_until_all_units_are_validated():
    invalid = validate_futures_units({"secid": "Si", "spot_scale": 1, "futures_scale": 1, "multiplier": None, "lot": 1, "currency": "RUB", "expiration": date(2026, 9, 17)})
    valid = validate_futures_units({**invalid, "multiplier": 1000})
    assert not invalid["basis_enabled"]
    assert valid["basis_enabled"]


def test_historical_sector_intervals_cannot_overlap():
    frame = pd.DataFrame([
        {"secid": "SBER", "valid_from": "2020-01-01", "valid_to": "2022-12-31"},
        {"secid": "SBER", "valid_from": "2022-01-01", "valid_to": "2024-12-31"},
    ])
    assert validate_sector_membership(frame) == ["SBER: overlapping intervals"]


def test_same_sample_ablation_prevents_sample_advantage():
    baseline = pd.DataFrame({"date": [1, 2, 3], "score": [0.1, 0.2, 0.3]})
    candidate = pd.DataFrame({"date": [2, 3, 4], "score": [0.3, 0.4, 0.5]})
    left, right = same_sample_ablation(baseline, candidate, ["date"])
    assert left.date.tolist() == right.date.tolist() == [2, 3]


def test_dividend_duplicate_and_invalid_audit(con):
    frame = pd.DataFrame({"secid": ["SBERP", "SBERP"], "record_date": [date(2024, 7, 11)] * 2})
    assert len(detect_dividend_duplicates(frame)) == 2
    con.execute("INSERT INTO dividends VALUES ('SBERP','2024-07-11',0,'RUB','MOEX')")
    assert audit_dividends(con)["invalid"] == 1


def test_five_x5_mapping_is_not_assumed(con):
    result = audit_corporate_actions(con)
    assert not result["five_x5_mapping_validated"]
    assert "never mechanically joined" in result["warning"]


def test_full_audit_does_not_promote_production(con, monkeypatch, tmp_path):
    monkeypatch.setattr("moex_analytics.historical_data.core.PROJECT_ROOT", tmp_path)
    result = complete_historical_data_audit(con)
    assert result["downloaded_rows"] == 0
    assert result["ablation"]["production_promotion"] is False
    assert result["production_models_changed"] is False


@pytest.mark.parametrize("command", [
    "audit-historical-data-coverage", "backfill-issuer-fundamentals",
    "backfill-historical-universe", "backfill-sector-history",
    "backfill-external-factors", "backfill-futures", "audit-options-history",
    "audit-corporate-actions", "audit-dividends", "calculate-pit-integrity",
    "run-data-value-ablation", "historical-data-status", "complete-historical-data-audit",
])
def test_stage20_cli_commands_are_registered(command):
    assert build_parser().parse_args([command]).command == command
