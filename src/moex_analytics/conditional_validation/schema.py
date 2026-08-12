"""Persistence for Stage 100 frozen historical replay and calibration."""

DDL = """
CREATE TABLE IF NOT EXISTS conditional_validation_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,validation_version VARCHAR,
 config_signature VARCHAR,feature_version VARCHAR,regime_version VARCHAR,
 similarity_version VARCHAR,weighting_version VARCHAR,calibration_version VARCHAR,
 random_seed INTEGER,replay_rows BIGINT,scorecards BIGINT,immutable BOOLEAN,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditional_replay_forecasts(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,evaluation_date DATE,phase VARCHAR,
 variant VARCHAR,similarity_threshold DOUBLE,evaluation_regime VARCHAR,status VARCHAR,raw_n INTEGER,
 effective_sample_size DOUBLE,predicted_return DOUBLE,actual_return DOUBLE,
 raw_low60 DOUBLE,raw_high60 DOUBLE,raw_low80 DOUBLE,raw_high80 DOUBLE,
 raw_up_frequency DOUBLE,no_change_return DOUBLE,unconditional_return DOUBLE,
 momentum_return DOUBLE,mean_reversion_return DOUBLE,history_end DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon,evaluation_date,variant,similarity_threshold));
CREATE TABLE IF NOT EXISTS conditional_calibration_mappings(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,variant VARCHAR,selected_threshold DOUBLE,
 calibration_n INTEGER,radius60 DOUBLE,radius80 DOUBLE,probability_method VARCHAR,
 probability_coef DOUBLE,probability_intercept DOUBLE,frozen_before_test BOOLEAN,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon,variant));
CREATE TABLE IF NOT EXISTS conditional_validation_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,variant VARCHAR,oos_n INTEGER,
 mae DOUBLE,median_ae DOUBLE,directional_accuracy DOUBLE,brier DOUBLE,
 coverage60 DOUBLE,median_width60 DOUBLE,coverage80 DOUBLE,median_width80 DOUBLE,
 no_change_mae DOUBLE,unconditional_mae DOUBLE,momentum_mae DOUBLE,mean_reversion_mae DOUBLE,
 median_ess DOUBLE,selected_threshold DOUBLE,reliability VARCHAR,reliability_reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon,variant));
CREATE TABLE IF NOT EXISTS conditional_regime_validation(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,regime_bucket VARCHAR,oos_n INTEGER,
 mae DOUBLE,no_change_mae DOUBLE,coverage60 DOUBLE,status VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon,regime_bucket));
CREATE TABLE IF NOT EXISTS conditional_calibrated_forecasts(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,current_price DOUBLE,center_price DOUBLE,
 expected60_low DOUBLE,expected60_high DOUBLE,plausible80_low DOUBLE,plausible80_high DOUBLE,
 stress_low DOUBLE,stress_high DOUBLE,up_probability DOUBLE,probability_published BOOLEAN,
 raw_n INTEGER,effective_sample_size DOUBLE,reliability VARCHAR,range_status VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
