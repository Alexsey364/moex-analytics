"""Persistence for Stage 98 conditional path analytics."""

DDL = """
CREATE TABLE IF NOT EXISTS conditional_path_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,regime_run_id VARCHAR,
 forecast_run_id VARCHAR,path_version VARCHAR,paths BIGINT,curve_rows BIGINT,
 immutable BOOLEAN,production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,
 status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditional_analog_paths(
 run_id VARCHAR,secid VARCHAR,analog_date DATE,episode_id VARCHAR,scenario_role VARCHAR,
 session INTEGER,normalized_return DOUBLE,projected_price DOUBLE,weight DOUBLE,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,analog_date,session));
CREATE TABLE IF NOT EXISTS conditional_path_curves(
 run_id VARCHAR,secid VARCHAR,session INTEGER,status VARCHAR,weighted_median_return DOUBLE,
 weighted_median_price DOUBLE,expected_low DOUBLE,expected_high DOUBLE,
 plausible_low DOUBLE,plausible_high DOUBLE,stress_low DOUBLE,stress_high DOUBLE,
 raw_n INTEGER,effective_sample_size DOUBLE,evidence_status VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,session));
CREATE TABLE IF NOT EXISTS conditional_path_risk(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,status VARCHAR,raw_n INTEGER,
 effective_sample_size DOUBLE,median_mae DOUBLE,median_mfe DOUBLE,median_max_drawdown DOUBLE,
 median_time_to_trough DOUBLE,median_time_to_peak DOUBLE,underwater_frequency DOUBLE,
 recovery_frequency DOUBLE,median_recovery_time DOUBLE,new_high_after_recovery_frequency DOUBLE,
 fall_first_end_positive_frequency DOUBLE,dd3_frequency DOUBLE,dd5_frequency DOUBLE,
 dd10_frequency DOUBLE,dd15_frequency DOUBLE,dd20_frequency DOUBLE,evidence_status VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]

