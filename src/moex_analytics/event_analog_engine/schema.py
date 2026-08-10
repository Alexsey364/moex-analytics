"""Stage 46 persistence schema."""

DDL = """
CREATE TABLE IF NOT EXISTS event_analog_runs(
 run_id VARCHAR PRIMARY KEY,trajectory_run_id VARCHAR,cutoff DATE,created_at TIMESTAMP,
 finished_at TIMESTAMP,status VARCHAR,profile_rows BIGINT,distribution_rows BIGINT,
 methodology_version VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS analog_event_profiles(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,path_window INTEGER,analog_date DATE,
 event_id VARCHAR,event_family VARCHAR,event_type VARCHAR,event_state VARCHAR,
 surprise_event BOOLEAN,available_from TIMESTAMPTZ,pit_safe BOOLEAN,
 PRIMARY KEY(run_id,secid,method,path_window,analog_date,event_id)
);
CREATE TABLE IF NOT EXISTS current_event_contexts(
 run_id VARCHAR,secid VARCHAR,cutoff DATE,event_families_json JSON,
 nearest_scheduled_family VARCHAR,days_until_scheduled INTEGER,historical_matches INTEGER,
 novelty_status VARCHAR,confidence_adjustment VARCHAR,status VARCHAR,reason VARCHAR,
 PRIMARY KEY(run_id,secid)
);
CREATE TABLE IF NOT EXISTS event_conditioned_distributions(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,path_window INTEGER,horizon INTEGER,
 subset VARCHAR,event_family VARCHAR,effective_n INTEGER,median_return DOUBLE,
 q25 DOUBLE,q75 DOUBLE,positive_fraction DOUBLE,dispersion DOUBLE,status VARCHAR,reason VARCHAR,
 PRIMARY KEY(run_id,secid,method,path_window,horizon,subset,event_family)
);
"""
