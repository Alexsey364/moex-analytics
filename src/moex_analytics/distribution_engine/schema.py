"""Stage 53 immutable return-distribution schema."""

DDL = """
CREATE TABLE IF NOT EXISTS distribution_research_runs(
 run_id VARCHAR PRIMARY KEY,ranking_run_id VARCHAR,target_run_id VARCHAR,
 dataset_version VARCHAR,cutoff DATE,train_end DATE,validation_end DATE,
 holdout_start DATE,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 prediction_rows BIGINT,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS distribution_method_policies(
 run_id VARCHAR,horizon INTEGER,method VARCHAR,validation_pinball DOUBLE,
 validation_coverage_50 DOUBLE,validation_coverage_80 DOUBLE,validation_coverage_90 DOUBLE,
 selection_score DOUBLE,selected BOOLEAN,policy_hash VARCHAR,calibration_q50 DOUBLE,
 calibration_q80 DOUBLE,calibration_q90 DOUBLE,selection_sample VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,horizon,method)
);
CREATE TABLE IF NOT EXISTS distribution_oos_predictions(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,horizon INTEGER,method VARCHAR,
 q05 DOUBLE,q10 DOUBLE,q25 DOUBLE,q50 DOUBLE,q75 DOUBLE,q90 DOUBLE,q95 DOUBLE,
 conformal50_low DOUBLE,conformal50_high DOUBLE,conformal80_low DOUBLE,conformal80_high DOUBLE,
 conformal90_low DOUBLE,conformal90_high DOUBLE,actual_return DOUBLE,current_price DOUBLE,
 expected_upside DOUBLE,expected_downside DOUBLE,upside_downside_ratio DOUBLE,
 expected_shortfall_10 DOUBLE,expected_shortfall_5 DOUBLE,material_up_5_bucket VARCHAR,
 material_down_5_bucket VARCHAR,probability_allowed BOOLEAN,sample_type VARCHAR,
 policy_hash VARCHAR,history_end DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,trade_date,secid,horizon,method,sample_type)
);
CREATE TABLE IF NOT EXISTS distribution_scorecards(
 run_id VARCHAR,horizon INTEGER,method VARCHAR,sample_type VARCHAR,observations BIGINT,
 median_mae DOUBLE,pinball_loss DOUBLE,crps_approx DOUBLE,coverage_50 DOUBLE,
 coverage_80 DOUBLE,coverage_90 DOUBLE,tail_10_coverage DOUBLE,tail_5_coverage DOUBLE,
 baseline_delta DOUBLE,status VARCHAR,
 PRIMARY KEY(run_id,horizon,method,sample_type)
);
CREATE TABLE IF NOT EXISTS current_return_distributions(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,method VARCHAR,current_price DOUBLE,
 q05_return DOUBLE,q10_return DOUBLE,q25_return DOUBLE,q50_return DOUBLE,q75_return DOUBLE,
 q90_return DOUBLE,q95_return DOUBLE,q05_price DOUBLE,q10_price DOUBLE,q25_price DOUBLE,
 q50_price DOUBLE,q75_price DOUBLE,q90_price DOUBLE,q95_price DOUBLE,downside_10 DOUBLE,
 upside_downside_ratio DOUBLE,material_up_5_bucket VARCHAR,material_down_5_bucket VARCHAR,
 probability_allowed BOOLEAN,status VARCHAR,reason VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,cutoff,secid,horizon)
);
"""
