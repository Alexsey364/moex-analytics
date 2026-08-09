"""Stage 24 immutable feature memory schema."""

DDL = """
CREATE TABLE IF NOT EXISTS feature_learning_runs(
 run_id VARCHAR PRIMARY KEY,dataset_version VARCHAR,created_at TIMESTAMP,status VARCHAR,
 instruments_json JSON,horizons_json JSON,runtime_seconds DOUBLE,records INTEGER,
 notes VARCHAR
);
CREATE TABLE IF NOT EXISTS feature_performance_history(
 run_id VARCHAR,feature VARCHAR,family VARCHAR,instrument VARCHAR,horizon INTEGER,
 regime VARCHAR,evaluation_period VARCHAR,date_from DATE,date_to DATE,ic DOUBLE,
 rank_ic DOUBLE,effect_sign INTEGER,stability DOUBLE,sample INTEGER,importance DOUBLE,
 oos_contribution DOUBLE,status VARCHAR,immutable BOOLEAN,created_at TIMESTAMP,
 PRIMARY KEY(run_id,feature,instrument,horizon,regime,evaluation_period)
);
CREATE TABLE IF NOT EXISTS feature_dynamic_scorecards(
 run_id VARCHAR,feature VARCHAR,family VARCHAR,instrument VARCHAR,horizon INTEGER,
 long_run_ic DOUBLE,recent_ic DOUBLE,shrunk_ic DOUBLE,fold_stability DOUBLE,
 regimes_worked INTEGER,years_worked DOUBLE,sign_changes INTEGER,sample INTEGER,
 status VARCHAR,reason VARCHAR,PRIMARY KEY(run_id,feature,instrument,horizon)
);
CREATE TABLE IF NOT EXISTS feature_family_contribution(
 run_id VARCHAR,instrument VARCHAR,horizon INTEGER,family VARCHAR,features INTEGER,
 contribution DOUBLE,stable_features INTEGER,decaying_features INTEGER,status VARCHAR,
 PRIMARY KEY(run_id,instrument,horizon,family)
);
"""
