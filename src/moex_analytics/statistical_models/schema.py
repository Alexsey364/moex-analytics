"""Stage 103 statistical-model schema."""

DDL = """
CREATE TABLE IF NOT EXISTS statistical_model_runs(
 run_id VARCHAR PRIMARY KEY,version VARCHAR,feature_run_id VARCHAR,target_run_id VARCHAR,
 started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,models INTEGER,predictions BIGINT,
 details_json JSON,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS statistical_model_registry(
 run_id VARCHAR,model_id VARCHAR,secid VARCHAR,horizon INTEGER,target VARCHAR,model VARCHAR,
 features_version VARCHAR,training_cutoff DATE,calibration_cutoff DATE,test_start DATE,test_end DATE,
 hyperparameters_json JSON,status VARCHAR,artifact_location VARCHAR,automatic_promotion BOOLEAN,
 PRIMARY KEY(run_id,model_id));
CREATE TABLE IF NOT EXISTS statistical_model_predictions(
 run_id VARCHAR,model_id VARCHAR,secid VARCHAR,horizon INTEGER,trade_date DATE,target VARCHAR,
 actual DOUBLE,prediction DOUBLE,baseline_prediction DOUBLE,split VARCHAR,train_end DATE,
 probability_up DOUBLE,immutable BOOLEAN,PRIMARY KEY(run_id,model_id,trade_date));
CREATE TABLE IF NOT EXISTS statistical_model_scorecards(
 run_id VARCHAR,model_id VARCHAR,secid VARCHAR,horizon INTEGER,target VARCHAR,model VARCHAR,
 oos_n INTEGER,mae DOUBLE,baseline_mae DOUBLE,improvement DOUBLE,direction_accuracy DOUBLE,
 brier DOUBLE,rank_ic DOUBLE,sign_stability DOUBLE,subperiod_stability DOUBLE,status VARCHAR,
 probability_allowed BOOLEAN,ci_low DOUBLE,ci_high DOUBLE,details_json JSON,
 PRIMARY KEY(run_id,model_id));
CREATE TABLE IF NOT EXISTS statistical_model_coefficients(
 run_id VARCHAR,model_id VARCHAR,feature VARCHAR,coefficient DOUBLE,standardized BOOLEAN,
 PRIMARY KEY(run_id,model_id,feature));
"""
