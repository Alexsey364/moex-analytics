"""Immutable schema for research-only meta confidence."""

DDL = """
CREATE TABLE IF NOT EXISTS meta_learning_runs(
 run_id VARCHAR PRIMARY KEY,source_run_id VARCHAR,created_at TIMESTAMP,status VARCHAR,
 runtime_seconds DOUBLE,models INTEGER,note VARCHAR
);
CREATE TABLE IF NOT EXISTS meta_confidence_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,train_end DATE,test_start DATE,
 train_n INTEGER,test_n INTEGER,base_accuracy DOUBLE,selected_accuracy DOUBLE,
 coverage DOUBLE,abstention_benefit DOUBLE,unknown_regime_rate DOUBLE,policy VARCHAR,
 status VARCHAR,reason VARCHAR,immutable BOOLEAN,created_at TIMESTAMP,
 PRIMARY KEY(run_id,secid,horizon,model)
);
CREATE TABLE IF NOT EXISTS selective_accuracy_curves(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,target_coverage DOUBLE,
 threshold DOUBLE,actual_coverage DOUBLE,accuracy DOUBLE,n INTEGER,threshold_source VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,model,target_coverage)
);
CREATE TABLE IF NOT EXISTS meta_oos_predictions(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,trade_date DATE,
 primary_correct BOOLEAN,large_error BOOLEAN,interval_failure BOOLEAN,
 meta_confidence DOUBLE,training_distance DOUBLE,regime_novelty DOUBLE,
 model_disagreement DOUBLE,policy VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon,model,trade_date)
);
"""
