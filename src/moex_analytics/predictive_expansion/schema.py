"""Versioned Stage 30 research tables; production tables remain frozen."""

DDL = """
CREATE TABLE IF NOT EXISTS stage30_expansion_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 target_securities INTEGER,securities_before INTEGER,securities_after INTEGER,
 rows_before BIGINT,rows_after BIGINT,requests INTEGER,errors INTEGER,runtime_seconds DOUBLE,
 database_growth BIGINT,raw_growth BIGINT,stop_reason VARCHAR,production_changes INTEGER,
 config_json JSON
);
CREATE TABLE IF NOT EXISTS stage30_expansion_checkpoints(
 run_id VARCHAR,checkpoint INTEGER,captured_at TIMESTAMP,batch_run_id VARCHAR,
 securities_added INTEGER,active INTEGER,inactive INTEGER,jobs_completed INTEGER,
 eod_rows BIGINT,requests INTEGER,errors INTEGER,runtime_seconds DOUBLE,
 database_growth BIGINT,raw_growth BIGINT,status VARCHAR,receipt_json JSON,
 PRIMARY KEY(run_id,checkpoint)
);
CREATE TABLE IF NOT EXISTS stage30_liquidity_daily(
 trade_date DATE,secid VARCHAR,boardid VARCHAR,return_1d DOUBLE,turnover DOUBLE,
 volume DOUBLE,num_trades BIGINT,average_trade_value DOUBLE,average_trade_size DOUBLE,
 amihud DOUBLE,zero_volume BOOLEAN,turnover_1 DOUBLE,turnover_5 DOUBLE,
 turnover_20 DOUBLE,turnover_60 DOUBLE,turnover_120 DOUBLE,turnover_250 DOUBLE,
 volume_20 DOUBLE,trades_20 DOUBLE,volume_acceleration DOUBLE,
 trade_count_acceleration DOUBLE,price_impact_proxy DOUBLE,turnover_volatility DOUBLE,
 liquidity_percentile DOUBLE,turnover_percentile DOUBLE,volume_percentile DOUBLE,
 liquidity_regime VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(trade_date,secid,boardid)
);
CREATE TABLE IF NOT EXISTS stage30_breadth_daily(
 trade_date DATE PRIMARY KEY,number_tradable INTEGER,advancers INTEGER,decliners INTEGER,
 unchanged INTEGER,new_high_20 INTEGER,new_high_60 INTEGER,new_high_250 INTEGER,
 new_low_20 INTEGER,new_low_60 INTEGER,new_low_250 INTEGER,above_sma20 INTEGER,
 above_sma50 INTEGER,above_sma100 INTEGER,above_sma200 INTEGER,positive_momentum_5 INTEGER,
 positive_momentum_20 INTEGER,positive_momentum_60 INTEGER,positive_momentum_120 INTEGER,
 median_return DOUBLE,equal_weight_return DOUBLE,cross_sectional_volatility DOUBLE,
 return_dispersion DOUBLE,turnover_breadth DOUBLE,volume_breadth DOUBLE,
 liquidity_breadth DOUBLE,drawdown_gt_10 INTEGER,drawdown_gt_20 INTEGER,
 drawdown_gt_30 INTEGER,number_of_constituents_used INTEGER,quality_status VARCHAR,
 calculated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stage30_security_quality(
 secid VARCHAR PRIMARY KEY,first_trade DATE,last_trade DATE,observations BIGINT,
 history_years DOUBLE,missing_ohlc DOUBLE,volume_coverage DOUBLE,numtrades_coverage DOUBLE,
 board_count INTEGER,board_continuity VARCHAR,corporate_action_flags INTEGER,
 fundamental_periods INTEGER,pit_quality VARCHAR,feature_coverage DOUBLE,
 training_tier VARCHAR,exclusion_reason VARCHAR,calculated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stage30_training_sample_quality(
 secid VARCHAR,horizon INTEGER,rows BIGINT,years DOUBLE,effective_n DOUBLE,
 regimes INTEGER,missingness DOUBLE,corporate_action_flags INTEGER,
 median_turnover DOUBLE,feature_coverage DOUBLE,complex_model_allowed BOOLEAN,
 quality_tier VARCHAR,calculated_at TIMESTAMP,PRIMARY KEY(secid,horizon)
);
CREATE TABLE IF NOT EXISTS stage30_survivorship_diagnostics(
 threshold INTEGER,security_count INTEGER,captured_at TIMESTAMP,days INTEGER,
 mean_difference DOUBLE,median_difference DOUBLE,p95_absolute_difference DOUBLE,
 breadth_rows BIGINT,factor_effect_json JSON,tournament_effect_json JSON,status VARCHAR,
 PRIMARY KEY(threshold,security_count)
);
CREATE TABLE IF NOT EXISTS stage30_market_feature_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,status VARCHAR,securities INTEGER,
 liquidity_rows BIGINT,breadth_days BIGINT,quality_rows BIGINT,runtime_seconds DOUBLE,
 production_changes INTEGER,details_json JSON
);
CREATE TABLE IF NOT EXISTS stage30_data_value_ledger(
 run_id VARCHAR,dataset_family VARCHAR,rows_added BIGINT,storage_cost BIGINT,
 download_time DOUBLE,oos_effect DOUBLE,horizons_helped JSON,instruments_helped JSON,
 status VARCHAR,evidence VARCHAR,created_at TIMESTAMP,
 PRIMARY KEY(run_id,dataset_family)
);
CREATE TABLE IF NOT EXISTS stage30_fundamental_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 issuers INTEGER,documents INTEGER,validated_observations INTEGER,validated_periods INTEGER,
 dividends INTEGER,errors INTEGER,details_json JSON,production_changes INTEGER
);
CREATE TABLE IF NOT EXISTS stage30_fundamental_coverage(
 issuer VARCHAR PRIMARY KEY,secids_json JSON,documents INTEGER,validated_observations INTEGER,
 validated_periods INTEGER,earliest_period DATE,latest_period DATE,latest_publication_date DATE,
 missingness_status VARCHAR,pit_status VARCHAR,coverage_status VARCHAR,limitation VARCHAR,
 calculated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stage30_dividend_pit(
 secid VARCHAR,record_date DATE,publication_date DATE,available_from TIMESTAMP,dps DOUBLE,
 currency VARCHAR,close_on_available_date DOUBLE,dividend_yield_pit DOUBLE,
 dividend_growth DOUBLE,cut_flag BOOLEAN,payment_status VARCHAR,share_class VARCHAR,
 source VARCHAR,quality_status VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(secid,record_date,dps)
);
"""
