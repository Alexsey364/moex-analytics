DDL = """
CREATE TABLE IF NOT EXISTS regime_intelligence_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 cutoff DATE,train_end DATE,rows_count BIGINT,features_count INTEGER,selected_model VARCHAR,
 selected_k INTEGER,methodology_version VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS regime_market_state_vectors(
 run_id VARCHAR,trade_date DATE,ret_5 DOUBLE,ret_20 DOUBLE,ret_60 DOUBLE,
 volatility_5 DOUBLE,volatility_20 DOUBLE,volatility_60 DOUBLE,drawdown_60 DOUBLE,
 breadth_balance DOUBLE,dispersion DOUBLE,turnover_log DOUBLE,rvi_change DOUBLE,
 rusfar_change DOUBLE,rgbi_change DOUBLE,cny_change DOUBLE,usd_change DOUBLE,
 novelty_distance DOUBLE,novelty_percentile DOUBLE,novelty_status VARCHAR,
 PRIMARY KEY(run_id,trade_date)
);
CREATE TABLE IF NOT EXISTS regime_issuer_state_vectors(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,ret_5 DOUBLE,ret_20 DOUBLE,ret_60 DOUBLE,
 volatility_20 DOUBLE,drawdown_60 DOUBLE,turnover_log DOUBLE,relative_20 DOUBLE,
 breadth_balance DOUBLE,market_stress DOUBLE,data_coverage DOUBLE,
 PRIMARY KEY(run_id,trade_date,secid)
);
CREATE TABLE IF NOT EXISTS regime_model_candidates(
 run_id VARCHAR,algorithm VARCHAR,k INTEGER,train_rows BIGINT,test_rows BIGINT,
 silhouette_train DOUBLE,silhouette_test DOUBLE,persistence DOUBLE,min_cluster_share DOUBLE,
 oos_reproducibility DOUBLE,selection_score DOUBLE,selected BOOLEAN,status VARCHAR,
 PRIMARY KEY(run_id,algorithm,k)
);
CREATE TABLE IF NOT EXISTS regime_timeline_v2(
 run_id VARCHAR,trade_date DATE,algorithm VARCHAR,k INTEGER,regime INTEGER,
 regime_duration INTEGER,novelty_status VARCHAR,selected BOOLEAN,
 PRIMARY KEY(run_id,trade_date,algorithm,k)
);
CREATE TABLE IF NOT EXISTS regime_transitions_v2(
 run_id VARCHAR,algorithm VARCHAR,k INTEGER,from_regime INTEGER,to_regime INTEGER,
 observations BIGINT,transition_frequency DOUBLE,selected BOOLEAN,
 PRIMARY KEY(run_id,algorithm,k,from_regime,to_regime)
);
CREATE TABLE IF NOT EXISTS regime_conditional_effects(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,regime INTEGER,observations BIGINT,
 mean_return DOUBLE,median_return DOUBLE,volatility DOUBLE,positive_fraction DOUBLE,
 max_drawdown DOUBLE,status VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,regime)
);
"""
