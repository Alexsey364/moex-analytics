"""Stage 102 predictive feature persistence."""

DDL = """
CREATE TABLE IF NOT EXISTS predictive_feature_runs(
 run_id VARCHAR PRIMARY KEY,version VARCHAR,source_version VARCHAR,cutoff DATE,
 data_signature VARCHAR,feature_signature VARCHAR,started_at TIMESTAMP,finished_at TIMESTAMP,
 status VARCHAR,rows BIGINT,features INTEGER,families INTEGER,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS predictive_feature_store(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,available_at TIMESTAMP,price DOUBLE,
 return_1 DOUBLE,return_5 DOUBLE,return_10 DOUBLE,return_20 DOUBLE,return_40 DOUBLE,
 return_60 DOUBLE,return_120 DOUBLE,return_250 DOUBLE,ma_distance_20 DOUBLE,
 ma_distance_60 DOUBLE,drawdown_250 DOUBLE,trend_slope_20 DOUBLE,trend_consistency_20 DOUBLE,
 realized_vol_5 DOUBLE,realized_vol_20 DOUBLE,realized_vol_60 DOUBLE,downside_vol_20 DOUBLE,
 volatility_ratio DOUBLE,turnover_20 DOUBLE,liquidity_spike DOUBLE,market_return_20 DOUBLE,
 market_drawdown_250 DOUBLE,market_vol_20 DOUBLE,breadth_balance DOUBLE,
 sector_return_20 DOUBLE,relative_sector_20 DOUBLE,key_rate DOUBLE,ruonia DOUBLE,rgbi DOUBLE,
 usd_rub DOUBLE,fx_return_20 DOUBLE,brent DOUBLE,brent_return_20 DOUBLE,
 dividend_yield DOUBLE,growth_score DOUBLE,valuation_history_score DOUBLE,
 momentum_rank DOUBLE,volatility_rank DOUBLE,liquidity_rank DOUBLE,
 feature_version VARCHAR,history_end DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,trade_date,secid)
);
CREATE TABLE IF NOT EXISTS predictive_feature_diagnostics(
 run_id VARCHAR,diagnostic_type VARCHAR,feature_a VARCHAR,feature_b VARCHAR,value DOUBLE,
 observations BIGINT,status VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,diagnostic_type,feature_a,feature_b)
);
"""
