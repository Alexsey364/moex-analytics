"""Stage 20.5 schemas."""

DDL = """
CREATE TABLE IF NOT EXISTS actual_document_inventory(
 document_id VARCHAR PRIMARY KEY, issuer VARCHAR, document_type VARCHAR,
 standard VARCHAR, period VARCHAR, publication_date DATE, url VARCHAR,
 mime VARCHAR, source_hash VARCHAR, size_bytes BIGINT, parser VARCHAR,
 status VARCHAR, local_path VARCHAR, discovered_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS actual_manual_review_candidates(
 candidate_id VARCHAR PRIMARY KEY, issuer VARCHAR, metric VARCHAR, period VARCHAR,
 publication_date DATE, document_url VARCHAR, source_hash VARCHAR, page VARCHAR,
 source_table VARCHAR, row_label VARCHAR, candidate_value DOUBLE, unit VARCHAR,
 reason VARCHAR, status VARCHAR, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS actual_backfill_checkpoints(
 checkpoint VARCHAR, run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
 before_json JSON, after_json JSON, new_rows BIGINT, status VARCHAR,
 PRIMARY KEY(checkpoint,run_id)
);
CREATE TABLE IF NOT EXISTS tradable_on_date_universe(
 trade_date DATE, secid VARCHAR, board VARCHAR, close DOUBLE, volume DOUBLE,
 value DOUBLE, inactive_at_audit BOOLEAN, source VARCHAR, loaded_at TIMESTAMP,
 PRIMARY KEY(trade_date,secid,board)
);
CREATE TABLE IF NOT EXISTS universe_pilot_runs(
 run_id VARCHAR PRIMARY KEY, securities INTEGER, inactive INTEGER, requests INTEGER,
 rows_received BIGINT, rows_inserted BIGINT, errors INTEGER, elapsed_seconds DOUBLE,
 disk_bytes BIGINT, started_at TIMESTAMP, finished_at TIMESTAMP, details_json JSON
);
CREATE TABLE IF NOT EXISTS futures_spec_documents(
 secid VARCHAR, valid_from DATE, valid_to DATE, underlying VARCHAR, lot DOUBLE,
 multiplier DOUBLE, price_step DOUBLE, step_value DOUBLE, currency VARCHAR,
 expiration DATE, source_url VARCHAR, source_hash VARCHAR, units_validated BOOLEAN,
 checked_at TIMESTAMP, PRIMARY KEY(secid,valid_from)
);
CREATE TABLE IF NOT EXISTS coverage_change_history(
 run_id VARCHAR PRIMARY KEY, captured_at TIMESTAMP, before_json JSON,
 after_json JSON, storage_before BIGINT, storage_after BIGINT
);
CREATE TABLE IF NOT EXISTS moex_equity_eod(
 trade_date DATE, secid VARCHAR, boardid VARCHAR, trading_session INTEGER,
 open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, legal_close_price DOUBLE,
 wa_price DOUBLE, market_price DOUBLE, market_price2 DOUBLE, admitted_quote DOUBLE,
 value DOUBLE, volume DOUBLE, num_trades BIGINT, currencyid VARCHAR,
 source_url VARCHAR, source_hash VARCHAR, loaded_at TIMESTAMP,
 PRIMARY KEY(trade_date,secid,boardid,trading_session)
);
CREATE TABLE IF NOT EXISTS market_history_jobs(
 secid VARCHAR, boardid VARCHAR, engine VARCHAR, market VARCHAR,
 history_from DATE, history_till DATE, next_start BIGINT, rows_loaded BIGINT,
 status VARCHAR, attempts INTEGER, last_error VARCHAR, updated_at TIMESTAMP,
 PRIMARY KEY(secid,boardid)
);
CREATE TABLE IF NOT EXISTS market_history_requests(
 request_id VARCHAR PRIMARY KEY, run_id VARCHAR, secid VARCHAR, boardid VARCHAR,
 page_start BIGINT, requested_at TIMESTAMP, duration_seconds DOUBLE,
 http_status INTEGER, rows_received INTEGER, source_url VARCHAR,
 source_hash VARCHAR, raw_path VARCHAR, status VARCHAR, error VARCHAR
);
CREATE TABLE IF NOT EXISTS equity_board_history(
 secid VARCHAR, boardid VARCHAR, first_trade DATE, last_trade DATE,
 observations BIGINT, total_value DOUBLE, selected_for_chain BOOLEAN,
 exclusion_reason VARCHAR, calculated_at TIMESTAMP,
 PRIMARY KEY(secid,boardid)
);
CREATE TABLE IF NOT EXISTS equity_liquidity_daily(
 trade_date DATE, secid VARCHAR, boardid VARCHAR, return_1d DOUBLE,
 turnover DOUBLE, volume DOUBLE, num_trades BIGINT, average_trade_value DOUBLE,
 zero_volume BOOLEAN, amihud DOUBLE, turnover_5 DOUBLE, turnover_20 DOUBLE,
 turnover_60 DOUBLE, turnover_120 DOUBLE, turnover_250 DOUBLE,
 volume_20 DOUBLE, trades_20 DOUBLE, liquidity_percentile DOUBLE,
 PRIMARY KEY(trade_date,secid,boardid)
);
CREATE TABLE IF NOT EXISTS market_breadth_daily(
 trade_date DATE PRIMARY KEY, tradable_count INTEGER, advancing INTEGER,
 declining INTEGER, unchanged INTEGER, new_high_20 INTEGER, new_low_20 INTEGER,
 above_sma20 INTEGER, above_sma50 INTEGER, above_sma200 INTEGER,
 positive_mom20 INTEGER, positive_mom60 INTEGER, equal_weight_return DOUBLE,
 return_dispersion DOUBLE, total_turnover DOUBLE, advancing_turnover DOUBLE,
 declining_turnover DOUBLE, calculated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS market_state_daily(
 trade_date DATE PRIMARY KEY, breadth_score DOUBLE, liquidity_score DOUBLE,
 volatility_score DOUBLE, dispersion_score DOUBLE, trend_score DOUBLE,
 risk_appetite_score DOUBLE, state_label VARCHAR, explain_json JSON,
 calculated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stage21_coverage_snapshots(
 run_id VARCHAR PRIMARY KEY, captured_at TIMESTAMP, securities INTEGER,
 boards INTEGER, rows BIGINT, date_from DATE, date_to DATE, completed_jobs INTEGER,
 pending_jobs INTEGER, failed_jobs INTEGER, database_bytes BIGINT, details_json JSON
);
CREATE TABLE IF NOT EXISTS stage21_factor_evaluation(
 feature VARCHAR, horizon INTEGER, sample_start DATE, sample_end DATE,
 observations INTEGER, folds INTEGER, baseline_accuracy DOUBLE,
 model_accuracy DOUBLE, delta_accuracy DOUBLE, ci_low DOUBLE, ci_high DOUBLE,
 mean_ic DOUBLE, stable_fold_wins INTEGER, status VARCHAR, calculated_at TIMESTAMP,
 PRIMARY KEY(feature,horizon)
);
CREATE TABLE IF NOT EXISTS market_history_batch_runs(
 run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
 jobs_attempted INTEGER, jobs_completed INTEGER, securities_before INTEGER,
 securities_after INTEGER, rows_before BIGINT, rows_after BIGINT, requests INTEGER,
 errors INTEGER, duration_seconds DOUBLE, database_bytes_before BIGINT,
 database_bytes_after BIGINT, raw_bytes_before BIGINT, raw_bytes_after BIGINT,
 status VARCHAR, cursor_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS market_history_control(
 control_key VARCHAR PRIMARY KEY, control_value VARCHAR, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS market_history_quality_issues(
 issue_id VARCHAR PRIMARY KEY, detected_at TIMESTAMP, issue_type VARCHAR,
 secid VARCHAR, boardid VARCHAR, trade_date DATE, details_json JSON,
 status VARCHAR
);
"""
