DDL = """
CREATE TABLE IF NOT EXISTS analog_search_runs_v3(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 cutoff DATE,state_run_id VARCHAR,contexts INTEGER,methods_json JSON,analogs BIGINT,
 methodology_version VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS analog_contexts_v3(
 run_id VARCHAR,analog_type VARCHAR,secid VARCHAR,cutoff DATE,feature_count INTEGER,
 history_rows BIGINT,current_regime INTEGER,current_novelty VARCHAR,data_quality DOUBLE,
 status VARCHAR,reason VARCHAR,eligible_rows BIGINT,required_coverage DOUBLE,
 PRIMARY KEY(run_id,analog_type,secid)
);
CREATE TABLE IF NOT EXISTS historical_analogs_v3(
 run_id VARCHAR,analog_type VARCHAR,secid VARCHAR,method VARCHAR,path_window INTEGER,
 cutoff DATE,analog_date DATE,episode_rank INTEGER,distance DOUBLE,distance_percentile DOUBLE,
 similarity_score DOUBLE,feature_coverage DOUBLE,regime_agreement BOOLEAN,
 sector_agreement BOOLEAN,event_state_agreement BOOLEAN,data_quality DOUBLE,
 independent BOOLEAN,why_similar_json JSON,why_different_json JSON,
 PRIMARY KEY(run_id,analog_type,secid,method,path_window,analog_date)
);
CREATE TABLE IF NOT EXISTS analog_method_diagnostics_v3(
 run_id VARCHAR,analog_type VARCHAR,secid VARCHAR,method VARCHAR,path_window INTEGER,
 candidates BIGINT,independent_selected INTEGER,median_distance DOUBLE,
 effective_n DOUBLE,status VARCHAR,train_only BOOLEAN,requested_k INTEGER,effective_k INTEGER,
 condition_number DOUBLE,reason VARCHAR,
 PRIMARY KEY(run_id,analog_type,secid,method,path_window)
);
"""
