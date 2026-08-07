"""DuckDB schema and persistence operations."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .config import PROJECT_ROOT, load_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    secid VARCHAR PRIMARY KEY, name VARCHAR, instrument_type VARCHAR,
    engine VARCHAR, market VARCHAR, board VARCHAR, history_from DATE,
    is_active BOOLEAN, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily_prices (
    trade_date DATE, secid VARCHAR, board VARCHAR, open DOUBLE, high DOUBLE,
    low DOUBLE, close DOUBLE, weighted_average_price DOUBLE, volume DOUBLE,
    value DOUBLE, number_of_trades BIGINT, source VARCHAR, loaded_at TIMESTAMP,
    PRIMARY KEY (trade_date, secid, board)
);
CREATE SEQUENCE IF NOT EXISTS load_log_id_seq START 1;
CREATE TABLE IF NOT EXISTS load_log (
    id BIGINT PRIMARY KEY DEFAULT nextval('load_log_id_seq'), secid VARCHAR,
    date_from DATE, date_to DATE, started_at TIMESTAMP, finished_at TIMESTAMP,
    rows_received BIGINT, rows_inserted BIGINT, status VARCHAR, error_message VARCHAR
);
CREATE SEQUENCE IF NOT EXISTS quality_issue_id_seq START 1;
CREATE TABLE IF NOT EXISTS data_quality_issues (
    id BIGINT PRIMARY KEY DEFAULT nextval('quality_issue_id_seq'), secid VARCHAR,
    trade_date DATE, issue_type VARCHAR, description VARCHAR, detected_at TIMESTAMP
);
CREATE SEQUENCE IF NOT EXISTS segment_id_seq START 1;
CREATE TABLE IF NOT EXISTS instrument_history_segments (
    id BIGINT PRIMARY KEY DEFAULT nextval('segment_id_seq'),
    canonical_secid VARCHAR, source_secid VARCHAR, engine VARCHAR, market VARCHAR,
    board VARCHAR, date_from DATE, date_to DATE, priority INTEGER, is_primary BOOLEAN,
    notes VARCHAR, discovered_at TIMESTAMP,
    UNIQUE(canonical_secid, source_secid, board)
);
CREATE TABLE IF NOT EXISTS canonical_daily_prices (
    trade_date DATE, canonical_secid VARCHAR, source_secid VARCHAR, board VARCHAR,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, weighted_average_price DOUBLE,
    volume DOUBLE, value DOUBLE, number_of_trades BIGINT, source_priority INTEGER,
    loaded_at TIMESTAMP, PRIMARY KEY(trade_date, canonical_secid)
);
CREATE TABLE IF NOT EXISTS dividends (
    canonical_secid VARCHAR, registry_close_date DATE, declared_date DATE,
    payment_date DATE, dividend_per_share DOUBLE, currency VARCHAR, source VARCHAR,
    loaded_at TIMESTAMP, notes VARCHAR,
    PRIMARY KEY(canonical_secid, registry_close_date)
);
CREATE TABLE IF NOT EXISTS daily_returns (
    trade_date DATE, canonical_secid VARCHAR, price_return DOUBLE, log_return DOUBLE,
    dividend_cash DOUBLE, dividend_return DOUBLE, total_return DOUBLE,
    total_return_index DOUBLE, calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(trade_date, canonical_secid, calculation_version)
);
CREATE TABLE IF NOT EXISTS trading_calendar (
    trade_date DATE, market VARCHAR, is_trading_day BOOLEAN, session_type VARCHAR,
    source VARCHAR, loaded_at TIMESTAMP, PRIMARY KEY(trade_date, market, session_type)
);
CREATE TABLE IF NOT EXISTS daily_features (
    trade_date DATE, canonical_secid VARCHAR, features_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP, source VARCHAR,
    minimum_history INTEGER, PRIMARY KEY(trade_date, canonical_secid, calculation_version)
);
CREATE TABLE IF NOT EXISTS market_regimes (
    trade_date DATE, regime VARCHAR, reasons_json JSON, values_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP, source VARCHAR,
    minimum_history INTEGER, PRIMARY KEY(trade_date, calculation_version)
);
CREATE TABLE IF NOT EXISTS forward_returns (
    condition_date DATE, exit_date DATE, canonical_secid VARCHAR, horizon INTEGER,
    price_return DOUBLE, total_return DOUBLE, max_drawdown DOUBLE, max_gain DOUBLE,
    calculation_version VARCHAR, calculated_at TIMESTAMP, source VARCHAR,
    minimum_history INTEGER,
    PRIMARY KEY(condition_date, canonical_secid, horizon, calculation_version)
);
CREATE TABLE IF NOT EXISTS historical_analogue_results (
    as_of_date DATE, canonical_secid VARCHAR, analogue_date DATE, rank INTEGER,
    distance DOUBLE, similarity DOUBLE, regime VARCHAR,
    calculation_version VARCHAR, calculated_at TIMESTAMP, source VARCHAR,
    minimum_history INTEGER,
    PRIMARY KEY(as_of_date, canonical_secid, analogue_date, calculation_version)
);
CREATE TABLE IF NOT EXISTS instrument_scores (
    trade_date DATE, canonical_secid VARCHAR, total_score DOUBLE, status VARCHAR,
    blocks_json JSON, positive_factors_json JSON, negative_factors_json JSON,
    statistics_quality VARCHAR, calculation_version VARCHAR, calculated_at TIMESTAMP,
    source VARCHAR, minimum_history INTEGER,
    PRIMARY KEY(trade_date, canonical_secid, calculation_version)
);
CREATE SEQUENCE IF NOT EXISTS analytics_run_id_seq START 1;
CREATE TABLE IF NOT EXISTS analytics_runs (
    id BIGINT PRIMARY KEY DEFAULT nextval('analytics_run_id_seq'), run_type VARCHAR,
    calculation_version VARCHAR, config_hash VARCHAR, started_at TIMESTAMP,
    finished_at TIMESTAMP, duration_seconds DOUBLE, rows_written BIGINT,
    status VARCHAR, details_json JSON
);
CREATE TABLE IF NOT EXISTS macro_series (
    series_id VARCHAR PRIMARY KEY, name VARCHAR, unit VARCHAR, frequency VARCHAR,
    source VARCHAR, endpoint VARCHAR, start_date DATE, publication_rule VARCHAR,
    revision_rule VARCHAR, is_point_in_time_safe BOOLEAN, notes VARCHAR,
    updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS macro_observations (
    series_id VARCHAR, observation_date DATE, release_date DATE,
    available_from TIMESTAMPTZ, value DOUBLE, vintage VARCHAR, loaded_at TIMESTAMP,
    source VARCHAR, PRIMARY KEY(series_id, observation_date, vintage)
);
CREATE TABLE IF NOT EXISTS macro_releases (
    series_id VARCHAR, observation_date DATE, release_date DATE,
    available_from TIMESTAMPTZ, vintage VARCHAR, source VARCHAR, loaded_at TIMESTAMP,
    PRIMARY KEY(series_id, observation_date, vintage)
);
CREATE TABLE IF NOT EXISTS macro_features (
    trade_date DATE, canonical_secid VARCHAR, horizon INTEGER, features_json JSON,
    source_dates_json JSON, available_at TIMESTAMPTZ, calculation_version VARCHAR,
    calculated_at TIMESTAMP, PRIMARY KEY(trade_date,canonical_secid,horizon,calculation_version)
);
CREATE SEQUENCE IF NOT EXISTS macro_load_log_id_seq START 1;
CREATE TABLE IF NOT EXISTS macro_load_log (
    id BIGINT PRIMARY KEY DEFAULT nextval('macro_load_log_id_seq'), run_type VARCHAR,
    started_at TIMESTAMP, finished_at TIMESTAMP, rows_received BIGINT,
    rows_inserted BIGINT, status VARCHAR, error_message VARCHAR, details_json JSON
);
CREATE SEQUENCE IF NOT EXISTS macro_quality_issue_id_seq START 1;
CREATE TABLE IF NOT EXISTS macro_quality_issues (
    id BIGINT PRIMARY KEY DEFAULT nextval('macro_quality_issue_id_seq'), series_id VARCHAR,
    observation_date DATE, issue_type VARCHAR, description VARCHAR,
    detected_at TIMESTAMP, severity VARCHAR
);
CREATE TABLE IF NOT EXISTS event_calendar (
    event_id VARCHAR PRIMARY KEY, event_type VARCHAR, country VARCHAR,
    related_instrument VARCHAR, scheduled_date DATE, actual_release_at TIMESTAMPTZ,
    source VARCHAR, status VARCHAR, importance VARCHAR, loaded_at TIMESTAMP, notes VARCHAR
);
CREATE TABLE IF NOT EXISTS macro_model_results (
    canonical_secid VARCHAR, horizon INTEGER, model_type VARCHAR, fold INTEGER,
    period VARCHAR, train_end DATE, test_start DATE, test_end DATE, metrics_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(canonical_secid,horizon,model_type,fold,period,calculation_version)
);
CREATE TABLE IF NOT EXISTS forecast_ranges (
    as_of_date DATE, canonical_secid VARCHAR, horizon INTEGER, current_price DOUBLE,
    median_return DOUBLE, lower_50 DOUBLE, upper_50 DOUBLE, lower_80 DOUBLE,
    upper_80 DOUBLE, lower_90 DOUBLE, upper_90 DOUBLE, positive_frequency DOUBLE,
    lower_price_50 DOUBLE, upper_price_50 DOUBLE, lower_price_80 DOUBLE,
    upper_price_80 DOUBLE, lower_price_90 DOUBLE, upper_price_90 DOUBLE,
    model_quality VARCHAR, baseline VARCHAR, available_at TIMESTAMPTZ,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(as_of_date,canonical_secid,horizon,calculation_version)
);
CREATE TABLE IF NOT EXISTS experimental_scores (
    trade_date DATE, canonical_secid VARCHAR, macro_score DOUBLE,
    combined_score DOUBLE, improvement_proven BOOLEAN, explanation_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(trade_date,canonical_secid,calculation_version)
);
CREATE TABLE IF NOT EXISTS macro_data_audit (
    run_id VARCHAR, canonical_secid VARCHAR, series_id VARCHAR, metrics_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(run_id,canonical_secid,series_id)
);
CREATE TABLE IF NOT EXISTS macro_matrix_audit (
    run_id VARCHAR, canonical_secid VARCHAR, horizon INTEGER, metrics_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(run_id,canonical_secid,horizon)
);
CREATE TABLE IF NOT EXISTS macro_ablation_results (
    run_id VARCHAR, canonical_secid VARCHAR, horizon INTEGER, block_name VARCHAR,
    sample_type VARCHAR, period VARCHAR, metrics_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(run_id,canonical_secid,horizon,block_name,sample_type,period)
);
CREATE TABLE IF NOT EXISTS macro_coefficient_audit (
    run_id VARCHAR, canonical_secid VARCHAR, horizon INTEGER, block_name VARCHAR,
    feature_name VARCHAR, metrics_json JSON, calculation_version VARCHAR,
    calculated_at TIMESTAMP,
    PRIMARY KEY(run_id,canonical_secid,horizon,block_name,feature_name)
);
CREATE TABLE IF NOT EXISTS macro_regime_audit (
    run_id VARCHAR, canonical_secid VARCHAR, horizon INTEGER, regime VARCHAR,
    metrics_json JSON, calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(run_id,canonical_secid,horizon,regime)
);
CREATE TABLE IF NOT EXISTS macro_feature_audit (
    run_id VARCHAR, canonical_secid VARCHAR, horizon INTEGER, block_name VARCHAR,
    status VARCHAR, reason VARCHAR, evidence_json JSON, calculation_version VARCHAR,
    calculated_at TIMESTAMP,
    PRIMARY KEY(run_id,canonical_secid,horizon,block_name)
);
CREATE TABLE IF NOT EXISTS macro_audit_runs (
    run_id VARCHAR PRIMARY KEY, calculation_version VARCHAR, started_at TIMESTAMP,
    finished_at TIMESTAMP, duration_seconds DOUBLE, status VARCHAR, details_json JSON
);
CREATE TABLE IF NOT EXISTS fundamental_series (
    metric_id VARCHAR PRIMARY KEY, name VARCHAR, frequency VARCHAR, unit VARCHAR,
    report_type VARCHAR, accounting_standard VARCHAR, source VARCHAR,
    description VARCHAR, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fundamental_observations (
    secid VARCHAR, metric_id VARCHAR, period_start DATE, period_end DATE,
    report_type VARCHAR, accounting_standard VARCHAR, publication_date DATE,
    available_from TIMESTAMPTZ, value DOUBLE, unit VARCHAR, source VARCHAR,
    source_document VARCHAR, revision_id VARCHAR, loaded_at TIMESTAMP,
    PRIMARY KEY(secid,metric_id,period_end,accounting_standard,revision_id)
);
CREATE TABLE IF NOT EXISTS fundamental_releases (
    release_id VARCHAR PRIMARY KEY, secid VARCHAR, period_start DATE, period_end DATE,
    report_type VARCHAR, accounting_standard VARCHAR, publication_date DATE,
    available_from TIMESTAMPTZ, source VARCHAR, source_document VARCHAR,
    revision_id VARCHAR, import_method VARCHAR, loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fundamental_features (
    trade_date DATE, secid VARCHAR, metric_id VARCHAR, value DOUBLE, unit VARCHAR,
    report_period_end DATE, publication_date DATE, source VARCHAR,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(trade_date,secid,metric_id,calculation_version)
);
CREATE TABLE IF NOT EXISTS fundamental_snapshots (
    trade_date DATE, secid VARCHAR, metric_id VARCHAR, value DOUBLE,
    report_period_end DATE, publication_date DATE, age_days INTEGER, source VARCHAR,
    calculation_version VARCHAR,
    PRIMARY KEY(trade_date,secid,metric_id,calculation_version)
);
CREATE TABLE IF NOT EXISTS valuation_inputs (
    as_of_date DATE, secid VARCHAR, input_name VARCHAR, value DOUBLE, unit VARCHAR,
    source_period_end DATE, source_publication_date DATE, scenario_version VARCHAR,
    calculated_at TIMESTAMP,
    PRIMARY KEY(as_of_date,secid,input_name,scenario_version)
);
CREATE TABLE IF NOT EXISTS valuation_scenarios (
    as_of_date DATE, secid VARCHAR, scenario VARCHAR, assumptions_json JSON,
    scenario_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(as_of_date,secid,scenario,scenario_version)
);
CREATE TABLE IF NOT EXISTS valuation_results (
    as_of_date DATE, secid VARCHAR, scenario VARCHAR, method VARCHAR,
    fair_value DOUBLE, dividend DOUBLE, total_return DOUBLE, lower_price DOUBLE,
    upper_price DOUBLE, details_json JSON, scenario_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(as_of_date,secid,scenario,method,scenario_version)
);
CREATE SEQUENCE IF NOT EXISTS fundamental_quality_issue_id_seq START 1;
CREATE TABLE IF NOT EXISTS fundamental_quality_issues (
    id BIGINT PRIMARY KEY DEFAULT nextval('fundamental_quality_issue_id_seq'),
    secid VARCHAR, metric_id VARCHAR, period_end DATE, issue_type VARCHAR,
    severity VARCHAR, description VARCHAR, detected_at TIMESTAMP
);
CREATE SEQUENCE IF NOT EXISTS fundamental_run_id_seq START 1;
CREATE TABLE IF NOT EXISTS fundamental_runs (
    id BIGINT PRIMARY KEY DEFAULT nextval('fundamental_run_id_seq'), run_type VARCHAR,
    started_at TIMESTAMP, finished_at TIMESTAMP, duration_seconds DOUBLE,
    rows_received BIGINT, rows_written BIGINT, status VARCHAR, details_json JSON
);
CREATE TABLE IF NOT EXISTS fundamental_documents (
    document_id VARCHAR PRIMARY KEY, secid VARCHAR, document_type VARCHAR,
    accounting_standard VARCHAR, period_start DATE, period_end DATE,
    publication_date DATE, available_from TIMESTAMPTZ, title VARCHAR,
    source_url VARCHAR, local_path VARCHAR, file_hash VARCHAR, mime_type VARCHAR,
    parser_version VARCHAR, processing_status VARCHAR, validation_status VARCHAR,
    revision_id VARCHAR, loaded_at TIMESTAMP, notes VARCHAR,
    UNIQUE(source_url,revision_id)
);
CREATE TABLE IF NOT EXISTS fundamental_metric_values (
    document_id VARCHAR, secid VARCHAR, metric_id VARCHAR, raw_value DOUBLE,
    raw_unit VARCHAR, normalized_value DOUBLE, normalized_unit VARCHAR,
    normalization_rule VARCHAR, accounting_standard VARCHAR, period_start DATE,
    period_end DATE, publication_date DATE, available_from TIMESTAMPTZ,
    source_page VARCHAR, source_table VARCHAR, source_note VARCHAR,
    revision_id VARCHAR, quality_status VARCHAR, loaded_at TIMESTAMP,
    PRIMARY KEY(document_id,metric_id,revision_id)
);
CREATE TABLE IF NOT EXISTS fundamental_accounting_regimes (
    regime_id VARCHAR PRIMARY KEY, accounting_regime VARCHAR,
    reporting_methodology_version VARCHAR, comparable_from DATE, comparable_to DATE,
    comparability_status VARCHAR, comparability_notes VARCHAR
);
CREATE TABLE IF NOT EXISTS fundamental_confidence (
    as_of_date DATE, secid VARCHAR, data_confidence DOUBLE,
    valuation_confidence DOUBLE, backtest_confidence DOUBLE, components_json JSON,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(as_of_date,secid,calculation_version)
);
CREATE TABLE IF NOT EXISTS fundamental_backtest_results (
    valuation_date DATE, secid VARCHAR, method VARCHAR, horizon INTEGER,
    current_price DOUBLE, estimated_price DOUBLE, lower_price DOUBLE,
    upper_price DOUBLE, future_price DOUBLE, total_return DOUBLE, confidence DOUBLE,
    release_id VARCHAR, market_regime VARCHAR, calculation_version VARCHAR,
    calculated_at TIMESTAMP,
    PRIMARY KEY(valuation_date,secid,method,horizon,calculation_version)
);
CREATE TABLE IF NOT EXISTS fundamental_backtest_errors (
    valuation_date DATE, secid VARCHAR, method VARCHAR, horizon INTEGER,
    absolute_error DOUBLE, percentage_error DOUBLE, return_error DOUBLE,
    direction_correct BOOLEAN, interval_hit BOOLEAN, confidence DOUBLE,
    calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(valuation_date,secid,method,horizon,calculation_version)
);
CREATE TABLE IF NOT EXISTS fundamental_model_comparison (
    period VARCHAR, model VARCHAR, horizon INTEGER, sample_size INTEGER,
    mae DOUBLE, mape DOUBLE, sign_accuracy DOUBLE, interval_coverage DOUBLE,
    average_width DOUBLE, calculation_version VARCHAR, calculated_at TIMESTAMP,
    PRIMARY KEY(period,model,horizon,calculation_version)
);
CREATE TABLE IF NOT EXISTS sber_guidance (guidance_id VARCHAR PRIMARY KEY, publication_date DATE, period_target DATE, metric_id VARCHAR, lower_bound DOUBLE, upper_bound DOUBLE, point_estimate DOUBLE, unit VARCHAR, statement_text VARCHAR, source_document_id VARCHAR, available_from TIMESTAMPTZ, guidance_status VARCHAR, superseded_by VARCHAR, loaded_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_interim_metrics (document_id VARCHAR, metric_id VARCHAR, period_start DATE, period_end DATE, accounting_standard VARCHAR, reported_ytd_value DOUBLE, derived_period_value DOUBLE, ttm_value DOUBLE, annualized_run_rate DOUBLE, unit VARCHAR, derivation_formula VARCHAR, previous_document_id VARCHAR, comparability_status VARCHAR, available_from TIMESTAMPTZ, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(document_id,metric_id,calculation_version));
CREATE TABLE IF NOT EXISTS sber_dividend_outlook (as_of_date DATE, scenario VARCHAR, profit DOUBLE, payout DOUBLE, capital_constraint VARCHAR, shares DOUBLE, dps DOUBLE, expected_yield DOUBLE, confidence DOUBLE, facts_json JSON, source_document_id VARCHAR, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(as_of_date,scenario,calculation_version));
CREATE TABLE IF NOT EXISTS sber_daily_fundamental_state (trade_date DATE PRIMARY KEY, latest_ifrs_period DATE, latest_ras_period DATE, latest_publication_date DATE, net_profit_ttm DOUBLE, annualized_profit DOUBLE, roe_ttm DOUBLE, equity DOUBLE, eps_ttm DOUBLE, bvps DOUBLE, forecast_eps_base DOUBLE, forecast_dividend_base DOUBLE, pe_trailing DOUBLE, pe_forward_experimental DOUBLE, pb_current DOUBLE, dividend_yield_trailing DOUBLE, dividend_yield_expected DOUBLE, data_age_days INTEGER, data_confidence DOUBLE, valuation_confidence DOUBLE, calculation_version VARCHAR, calculated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_valuation_ensemble (as_of_date DATE, scenario VARCHAR, method VARCHAR, estimate DOUBLE, weight DOUBLE, confidence DOUBLE, weight_reasons_json JSON, weighted_median DOUBLE, lower_quartile DOUBLE, upper_quartile DOUBLE, stress_low DOUBLE, stress_high DOUBLE, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(as_of_date,scenario,method,calculation_version));
CREATE TABLE IF NOT EXISTS sber_decision_runs (run_id VARCHAR PRIMARY KEY, as_of_date DATE, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, calculation_version VARCHAR, input_hash VARCHAR, rows_written BIGINT, details_json JSON);
CREATE TABLE IF NOT EXISTS sber_decision_evidence (run_id VARCHAR, block_id VARCHAR, score DOUBLE, confidence DOUBLE, status VARCHAR, positive_json JSON, negative_json JSON, data_json JSON, data_date DATE, calculation_version VARCHAR, PRIMARY KEY(run_id,block_id));
CREATE TABLE IF NOT EXISTS sber_decision_results (run_id VARCHAR PRIMARY KEY, as_of_date DATE, decision_status VARCHAR, horizon INTEGER, decision_confidence DOUBLE, first_position_fraction DOUBLE, current_price DOUBLE, main_low DOUBLE, main_high DOUBLE, stress_low DOUBLE, stress_high DOUBLE, expected_dividend DOUBLE, explanation VARCHAR, conflicts_json JSON, cancellation_json JSON, calculation_version VARCHAR, calculated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_price_zones (run_id VARCHAR, zone_name VARCHAR, lower_bound DOUBLE, upper_bound DOUBLE, action VARCHAR, max_position_fraction DOUBLE, reasons_json JSON, validity_json JSON, calculation_date DATE, PRIMARY KEY(run_id,zone_name));
CREATE TABLE IF NOT EXISTS sber_decision_triggers (run_id VARCHAR, trigger_id VARCHAR, category VARCHAR, condition_text VARCHAR, decision_change VARCHAR, recalculates_json JSON, threshold_value DOUBLE, unit VARCHAR, PRIMARY KEY(run_id,trigger_id));
CREATE TABLE IF NOT EXISTS sber_decision_backtest (decision_date DATE, strategy VARCHAR, horizon INTEGER, status VARCHAR, initial_fraction DOUBLE, average_entry DOUBLE, total_return DOUBLE, max_drawdown DOUBLE, downside_deviation DOUBLE, capital_at_risk DOUBLE, turnover DOUBLE, future_date DATE, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(decision_date,strategy,horizon,calculation_version));
CREATE TABLE IF NOT EXISTS sber_information_sources (source_id VARCHAR PRIMARY KEY, name VARCHAR, domain VARCHAR, source_type VARCHAR, trust_level DOUBLE, timezone VARCHAR, archive_available BOOLEAN, load_method VARCHAR, limitations VARCHAR, license_terms VARCHAR, last_successful_check TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_information_documents (document_id VARCHAR PRIMARY KEY, source_id VARCHAR, title VARCHAR, source_url VARCHAR, published_at TIMESTAMPTZ, available_from TIMESTAMPTZ, content_hash VARCHAR, document_type VARCHAR, validation_status VARCHAR, loaded_at TIMESTAMP, metadata_json JSON);
CREATE TABLE IF NOT EXISTS sber_events (event_id VARCHAR PRIMARY KEY, canonical_event_id VARCHAR, event_type VARCHAR, event_subtype VARCHAR, title VARCHAR, description VARCHAR, related_entity VARCHAR, scheduled_at TIMESTAMPTZ, occurred_at TIMESTAMPTZ, published_at TIMESTAMPTZ, available_from TIMESTAMPTZ, source_id VARCHAR, source_url VARCHAR, document_id VARCHAR, official_status VARCHAR, confirmation_count INTEGER, expected_status VARCHAR, relevance_to_sber DOUBLE, direction_hypothesis VARCHAR, severity DOUBLE, duration_hypothesis VARCHAR, point_in_time_safe BOOLEAN, validation_status VARCHAR, classification_rule VARCHAR, loaded_at TIMESTAMP, notes VARCHAR);
CREATE TABLE IF NOT EXISTS sber_event_entities (event_id VARCHAR, entity_id VARCHAR, entity_type VARCHAR, relation VARCHAR, PRIMARY KEY(event_id,entity_id));
CREATE TABLE IF NOT EXISTS sber_event_metrics (event_id VARCHAR, metric_id VARCHAR, observation_date DATE, publication_date DATE, available_from TIMESTAMPTZ, value DOUBLE, unit VARCHAR, source VARCHAR, revision_id VARCHAR, PRIMARY KEY(event_id,metric_id,revision_id));
CREATE TABLE IF NOT EXISTS sber_expectations (forecast_id VARCHAR PRIMARY KEY, publisher VARCHAR, publication_date DATE, available_from TIMESTAMPTZ, target_period DATE, metric_id VARCHAR, estimate DOUBLE, lower_bound DOUBLE, upper_bound DOUBLE, unit VARCHAR, source_url VARCHAR, source_document VARCHAR, analyst_count INTEGER, consensus_method VARCHAR, confidence DOUBLE, validation_status VARCHAR);
CREATE TABLE IF NOT EXISTS sber_surprises (event_id VARCHAR, metric_id VARCHAR, actual DOUBLE, consensus DOUBLE, difference DOUBLE, percentage_difference DOUBLE, standardized_surprise DOUBLE, direction VARCHAR, consensus_sample_size INTEGER, confidence DOUBLE, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(event_id,metric_id,calculation_version));
CREATE TABLE IF NOT EXISTS sber_event_reactions (event_id VARCHAR, event_window VARCHAR, anchor_trade_date DATE, exit_trade_date DATE, raw_return DOUBLE, imoex_return DOUBLE, finance_return DOUBLE, abnormal_imoex DOUBLE, abnormal_finance DOUBLE, volume_change DOUBLE, volatility_change DOUBLE, max_gain DOUBLE, max_drawdown DOUBLE, sessions_to_max INTEGER, publication_session VARCHAR, confounding_status VARCHAR, confounders_json JSON, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(event_id,event_window,calculation_version));
CREATE TABLE IF NOT EXISTS sber_event_studies (event_type VARCHAR, event_window VARCHAR, sample_size INTEGER, mean_reaction DOUBLE, median_reaction DOUBLE, positive_frequency DOUBLE, q25 DOUBLE, q75 DOUBLE, best_outcome DOUBLE, worst_outcome DOUBLE, mean_drawdown DOUBLE, sample_quality VARCHAR, segment VARCHAR, calculation_version VARCHAR, calculated_at TIMESTAMP, PRIMARY KEY(event_type,event_window,segment,calculation_version));
CREATE TABLE IF NOT EXISTS sber_event_impacts (event_id VARCHAR PRIMARY KEY, impact_score DOUBLE, impact_status VARCHAR, proposed_adjustments_json JSON, auto_apply_allowed BOOLEAN, reasons_json JSON, calculation_version VARCHAR, calculated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_event_quality_issues (issue_id VARCHAR PRIMARY KEY, event_id VARCHAR, issue_type VARCHAR, severity VARCHAR, description VARCHAR, detected_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_intelligence_runs (run_id VARCHAR PRIMARY KEY, run_type VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, input_hash VARCHAR, documents_found INTEGER, events_written INTEGER, duplicates INTEGER, details_json JSON, calculation_version VARCHAR);
CREATE TABLE IF NOT EXISTS sber_live_information_state (calculation_at TIMESTAMP, last_market_data_at TIMESTAMP, last_fundamental_data_at TIMESTAMP, last_event_at TIMESTAMPTZ, last_validated_event_at TIMESTAMPTZ, upcoming_event_at TIMESTAMPTZ, event_risk_level VARCHAR, data_freshness VARCHAR, unresolved_events INTEGER, manual_review_count INTEGER, information_confidence DOUBLE, version VARCHAR PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sber_decision_change_log (changed_at TIMESTAMP, previous_decision_id VARCHAR, new_decision_id VARCHAR, trigger_event_id VARCHAR, changed_blocks JSON, changed_parameters JSON, old_status VARCHAR, new_status VARCHAR, old_zones JSON, new_zones JSON, explanation VARCHAR, version VARCHAR, PRIMARY KEY(changed_at,version));"""


def database_path() -> Path:
    return PROJECT_ROOT / load_settings()["paths"]["database"]


@contextmanager
def connection(path: Path | None = None, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target), read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def init_database(path: Path | None = None) -> None:
    with connection(path) as con:
        con.execute(SCHEMA)


def upsert_instruments(con: duckdb.DuckDBPyConnection, items: Sequence[dict[str, Any]]) -> None:
    now = datetime.now()
    for item in items:
        con.execute(
            """INSERT INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(secid) DO UPDATE SET name=excluded.name,
               instrument_type=excluded.instrument_type, engine=excluded.engine,
               market=excluded.market, board=excluded.board,
               history_from=excluded.history_from, is_active=excluded.is_active,
               updated_at=excluded.updated_at""",
            [
                item["secid"],
                item["name"],
                item["instrument_type"],
                item["engine"],
                item["market"],
                item["board"],
                item["history_from"],
                item.get("is_active", True),
                now,
            ],
        )


def insert_daily_prices(con: duckdb.DuckDBPyConnection, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    before = con.execute("SELECT count(*) FROM daily_prices").fetchone()[0]
    columns = (
        "trade_date",
        "secid",
        "board",
        "open",
        "high",
        "low",
        "close",
        "weighted_average_price",
        "volume",
        "value",
        "number_of_trades",
        "source",
        "loaded_at",
    )
    frame = pd.DataFrame([{column: row.get(column) for column in columns} for row in rows])
    con.register("incoming_daily_prices", frame)
    try:
        con.execute(
            """INSERT INTO daily_prices SELECT * FROM incoming_daily_prices
               ON CONFLICT(trade_date, secid, board) DO NOTHING"""
        )
    finally:
        con.unregister("incoming_daily_prices")
    after = con.execute("SELECT count(*) FROM daily_prices").fetchone()[0]
    return int(after - before)


def latest_date(con: duckdb.DuckDBPyConnection, secid: str, board: str) -> date | None:
    return con.execute(
        "SELECT max(trade_date) FROM daily_prices WHERE secid=? AND board=?",
        [secid, board],
    ).fetchone()[0]


def row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return dict(
        con.execute("SELECT secid, count(*) FROM daily_prices GROUP BY secid ORDER BY secid").fetchall()
    )


def start_load(con: duckdb.DuckDBPyConnection, secid: str, date_from: date, date_to: date) -> int:
    return con.execute(
        """INSERT INTO load_log(secid,date_from,date_to,started_at,status)
           VALUES (?,?,?,current_timestamp,'running') RETURNING id""",
        [secid, date_from, date_to],
    ).fetchone()[0]


def finish_load(
    con: duckdb.DuckDBPyConnection,
    load_id: int,
    received: int,
    inserted: int,
    status: str,
    error: str | None = None,
) -> None:
    con.execute(
        """UPDATE load_log SET finished_at=current_timestamp, rows_received=?,
           rows_inserted=?, status=?, error_message=? WHERE id=?""",
        [received, inserted, status, error, load_id],
    )


def upsert_segments(con: duckdb.DuckDBPyConnection, segments: Sequence[dict[str, Any]]) -> None:
    for item in segments:
        con.execute(
            """INSERT INTO instrument_history_segments
               (canonical_secid,source_secid,engine,market,board,date_from,date_to,
                priority,is_primary,notes,discovered_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)
               ON CONFLICT(canonical_secid,source_secid,board) DO UPDATE SET
               date_from=excluded.date_from,date_to=excluded.date_to,
               priority=excluded.priority,is_primary=excluded.is_primary,
               notes=excluded.notes,discovered_at=excluded.discovered_at""",
            [
                item[key]
                for key in (
                    "canonical_secid",
                    "source_secid",
                    "engine",
                    "market",
                    "board",
                    "date_from",
                    "date_to",
                    "priority",
                    "is_primary",
                    "notes",
                )
            ],
        )


def insert_dividends(con: duckdb.DuckDBPyConnection, rows: Sequence[dict[str, Any]]) -> int:
    before = con.execute("SELECT count(*) FROM dividends").fetchone()[0]
    for row in rows:
        con.execute(
            """INSERT INTO dividends VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(canonical_secid,registry_close_date) DO UPDATE SET
               dividend_per_share=excluded.dividend_per_share,
               currency=excluded.currency,source=excluded.source,
               loaded_at=excluded.loaded_at,notes=excluded.notes""",
            [
                row.get(key)
                for key in (
                    "canonical_secid",
                    "registry_close_date",
                    "declared_date",
                    "payment_date",
                    "dividend_per_share",
                    "currency",
                    "source",
                    "loaded_at",
                    "notes",
                )
            ],
        )
    after = con.execute("SELECT count(*) FROM dividends").fetchone()[0]
    return int(after - before)
