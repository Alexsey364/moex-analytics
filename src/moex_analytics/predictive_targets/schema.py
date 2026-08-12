"""Persistence schema for the immutable Stage 51 target dataset."""

DDL = """
CREATE TABLE IF NOT EXISTS predictive_target_definitions(
 target_name VARCHAR, target_version VARCHAR, description VARCHAR, formula VARCHAR,
 horizon INTEGER, cutoff_semantics VARCHAR, execution_semantics VARCHAR,
 threshold DOUBLE, point_in_time_safe BOOLEAN, created_at TIMESTAMP, immutable BOOLEAN,
 PRIMARY KEY(target_name,target_version,horizon)
);
CREATE TABLE IF NOT EXISTS predictive_target_runs(
 run_id VARCHAR PRIMARY KEY, dataset_version VARCHAR, source_version VARCHAR,
 cutoff DATE, input_hash VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
 status VARCHAR, observation_rows BIGINT, entry_rows BIGINT, details_json JSON,
 immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS predictive_target_observations(
 run_id VARCHAR, trade_date DATE, secid VARCHAR, horizon INTEGER, exit_date DATE,
 total_return DOUBLE, excess_imoex DOUBLE, excess_sector DOUBLE,
 excess_cross_section_median DOUBLE, percentile_rank DOUBLE,
 top_10 BOOLEAN, top_20 BOOLEAN, bottom_10 BOOLEAN, bottom_20 BOOLEAN,
 move_up_3 BOOLEAN, move_up_5 BOOLEAN, move_up_10 BOOLEAN, move_up_15 BOOLEAN,
 move_down_3 BOOLEAN, move_down_5 BOOLEAN, move_down_10 BOOLEAN, move_down_15 BOOLEAN,
 mfe DOUBLE, mae DOUBLE, path_max_drawdown DOUBLE, time_to_high INTEGER,
 time_to_low INTEGER, path_shape VARCHAR, realized_volatility DOUBLE,
 return_over_volatility DOUBLE, return_over_downside DOUBLE, calmar_utility DOUBLE,
 eligible_count INTEGER, return_basis VARCHAR, sector_status VARCHAR,
 history_end DATE, immutable BOOLEAN,
 PRIMARY KEY(run_id,trade_date,secid,horizon)
);
CREATE TABLE IF NOT EXISTS predictive_entry_targets(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,horizon INTEGER,policy VARCHAR,
 signal_threshold DOUBLE,entry_date DATE,entry_price DOUBLE,exit_date DATE,
 policy_return DOUBLE,buy_now_return DOUBLE,entry_improvement DOUBLE,missed_return DOUBLE,
 entered BOOLEAN,history_end DATE,execution_semantics VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,trade_date,secid,horizon,policy)
);
CREATE TABLE IF NOT EXISTS predictive_return_targets(
 run_id VARCHAR,evaluation_date DATE,secid VARCHAR,horizon INTEGER,
 feature_timestamp TIMESTAMP,evaluation_timestamp TIMESTAMP,target_available_date DATE,
 forward_return DOUBLE,forward_log_return DOUBLE,market_return DOUBLE,
 excess_imoex DOUBLE,sector_return DOUBLE,excess_sector DOUBLE,up BOOLEAN,
 outperform_market BOOLEAN,max_drawdown DOUBLE,max_favorable_excursion DOUBLE,
 max_adverse_excursion DOUBLE,realized_vol DOUBLE,target_version VARCHAR,
 history_end DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,evaluation_date,secid,horizon)
);
"""
