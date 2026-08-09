"""Immutable calibration research schema."""

DDL = """
CREATE TABLE IF NOT EXISTS calibration_runs(
 run_id VARCHAR PRIMARY KEY,source_run_id VARCHAR,created_at TIMESTAMP,status VARCHAR,
 runtime_seconds DOUBLE,models_audited INTEGER,probability_approved INTEGER,note VARCHAR
);
CREATE TABLE IF NOT EXISTS probability_calibration_audit(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,method VARCHAR,
 train_end DATE,test_start DATE,train_n INTEGER,test_n INTEGER,auc DOUBLE,brier DOUBLE,
 baseline_brier DOUBLE,log_loss DOUBLE,ece DOUBLE,slope DOUBLE,intercept DOUBLE,
 fold_stable BOOLEAN,holdout_acceptable BOOLEAN,regime_acceptable BOOLEAN,
 drift_warning BOOLEAN,probability_allowed BOOLEAN,status VARCHAR,reason VARCHAR,
 immutable BOOLEAN,created_at TIMESTAMP,
 PRIMARY KEY(run_id,secid,horizon,model,method)
);
CREATE TABLE IF NOT EXISTS prediction_interval_audit(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,method VARCHAR,
 train_n INTEGER,test_n INTEGER,coverage_50 DOUBLE,coverage_80 DOUBLE,coverage_90 DOUBLE,
 width_50 DOUBLE,width_80 DOUBLE,width_90 DOUBLE,status VARCHAR,immutable BOOLEAN,
 created_at TIMESTAMP,PRIMARY KEY(run_id,secid,horizon,model,method)
);
CREATE TABLE IF NOT EXISTS uncertainty_decomposition(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,data_uncertainty DOUBLE,
 model_disagreement DOUBLE,regime_uncertainty DOUBLE,analog_uncertainty DOUBLE,
 live_uncertainty DOUBLE,status VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,model)
);
"""
