"""Stage 49 persistence schema."""

DDL = """
CREATE TABLE IF NOT EXISTS analog_validation_runs(
 run_id VARCHAR PRIMARY KEY,fusion_run_id VARCHAR,created_at TIMESTAMP,finished_at TIMESTAMP,
 status VARCHAR,scorecard_rows BIGINT,bootstrap_rows BIGINT,methodology_version VARCHAR,
 details_json JSON
);
CREATE TABLE IF NOT EXISTS analog_validation_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,variant VARCHAR,split VARCHAR,context VARCHAR,
 observations INTEGER,effective_n DOUBLE,balanced_accuracy DOUBLE,sign_accuracy DOUBLE,
 mae DOUBLE,rmse DOUBLE,spearman DOUBLE,rank_ic DOUBLE,coverage_50 DOUBLE,coverage_80 DOUBLE,
 coverage_90 DOUBLE,baseline_mae DOUBLE,mae_improvement DOUBLE,result_status VARCHAR,
 abstention_rate DOUBLE,train_end DATE,test_start DATE,test_end DATE,
 PRIMARY KEY(run_id,secid,horizon,variant,split,context)
);
CREATE TABLE IF NOT EXISTS analog_validation_bootstrap(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,variant VARCHAR,split VARCHAR,metric VARCHAR,
 estimate DOUBLE,ci_low DOUBLE,ci_high DOUBLE,block_length INTEGER,iterations INTEGER,
 baseline_variant VARCHAR,status VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,variant,split,metric)
);
CREATE TABLE IF NOT EXISTS analog_method_validation_status(
 run_id VARCHAR,method VARCHAR,k INTEGER,horizon INTEGER,status VARCHAR,reason VARCHAR,
 holdout_touched_for_selection BOOLEAN,
 PRIMARY KEY(run_id,method,k,horizon)
);
CREATE TABLE IF NOT EXISTS analog_method_selection_v2(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,method VARCHAR,selected_k INTEGER,
 validation_mae DOUBLE,train_end DATE,validation_end DATE,holdout_start DATE,
 scaler_hash VARCHAR,regime_model_hash VARCHAR,similarity_hash VARCHAR,policy_hash VARCHAR,
 holdout_touched_for_selection BOOLEAN,status VARCHAR,reason VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,method)
);
CREATE TABLE IF NOT EXISTS analog_strict_predictions_v2(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,method VARCHAR,k INTEGER,cutoff DATE,split VARCHAR,
 predicted_return DOUBLE,actual_return DOUBLE,q10 DOUBLE,q25 DOUBLE,q75 DOUBLE,q90 DOUBLE,
 effective_n INTEGER,policy_hash VARCHAR,library_end DATE,probability_allowed BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon,method,k,cutoff,split)
);
"""
