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
"""


def database_path() -> Path:
    return PROJECT_ROOT / load_settings()["paths"]["database"]


@contextmanager
def connection(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target))
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
