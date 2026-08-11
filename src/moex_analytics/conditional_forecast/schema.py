"""Persistence for Stage 96 weighted conditional forecasts."""

DDL = """
CREATE TABLE IF NOT EXISTS conditional_forecast_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,similarity_run_id VARCHAR,
 weighting_version VARCHAR,config_signature VARCHAR,instruments INTEGER,forecasts INTEGER,
 immutable BOOLEAN,production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,
 status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditional_analog_weights(
 run_id VARCHAR,secid VARCHAR,analog_date DATE,episode_id VARCHAR,similarity_component DOUBLE,
 regime_component DOUBLE,reliability_component DOUBLE,raw_weight DOUBLE,normalized_weight DOUBLE,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,analog_date));
CREATE TABLE IF NOT EXISTS conditional_forecast_horizons(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,status VARCHAR,current_price DOUBLE,raw_n INTEGER,
 effective_sample_size DOUBLE,max_weight DOUBLE,weighted_mean_return DOUBLE,
 weighted_median_return DOUBLE,center_price DOUBLE,expected_low DOUBLE,expected_high DOUBLE,
 plausible_low DOUBLE,plausible_high DOUBLE,weighted_up_frequency DOUBLE,
 weighted_down_frequency DOUBLE,median_max_drawdown DOUBLE,stress_low DOUBLE,stress_high DOUBLE,
 loo_center_min DOUBLE,loo_center_max DOUBLE,loo_width_sensitivity DOUBLE,
 loo_up_sensitivity DOUBLE,robustness_status VARCHAR,evidence_status VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]

