"""Persistence for Stage 101 baseline predictions and scorecards."""

DDL = """
CREATE TABLE IF NOT EXISTS predictive_baseline_runs(
 run_id VARCHAR PRIMARY KEY,target_run_id VARCHAR,version VARCHAR,cutoff DATE,input_hash VARCHAR,
 started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,prediction_rows BIGINT,
 scorecard_rows BIGINT,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS predictive_baseline_predictions(
 run_id VARCHAR,evaluation_date DATE,secid VARCHAR,horizon INTEGER,model VARCHAR,
 prediction DOUBLE,actual DOUBLE,actual_excess_market DOUBLE,probability_up DOUBLE,
 training_observations INTEGER,training_end DATE,feature_timestamp TIMESTAMP,
 target_available_date DATE,split VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,evaluation_date,secid,horizon,model)
);
CREATE TABLE IF NOT EXISTS predictive_baseline_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,sample_size INTEGER,
 mae DOUBLE,median_ae DOUBLE,rmse DOUBLE,direction_accuracy DOUBLE,brier DOUBLE,
 spearman_rank_correlation DOUBLE,excess_return_mae DOUBLE,subperiod_stability DOUBLE,
 status VARCHAR,rank INTEGER,mae_difference_to_best DOUBLE,details_json JSON,
 PRIMARY KEY(run_id,secid,horizon,model)
);
"""
