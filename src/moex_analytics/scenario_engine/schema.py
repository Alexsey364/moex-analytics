"""Stage 54 immutable scenario-tree schema."""

DDL = """
CREATE TABLE IF NOT EXISTS scenario_research_runs(
 run_id VARCHAR PRIMARY KEY,analog_run_id VARCHAR,trajectory_run_id VARCHAR,event_run_id VARCHAR,
 cutoff DATE,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,match_rows BIGINT,
 prehistory_rows BIGINT,episode_rows BIGINT,summary_rows BIGINT,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS scenario_multiscale_matches(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,analog_date DATE,short_distance DOUBLE,
 medium_distance DOUBLE,long_distance DOUBLE,combined_distance DOUBLE,similarity_score DOUBLE,
 regime_agreement BOOLEAN,event_agreement BOOLEAN,feature_coverage DOUBLE,independent BOOLEAN,
 applicability VARCHAR,gaps_json JSON,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,method,analog_date)
);
CREATE TABLE IF NOT EXISTS scenario_prehistory_points(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,analog_date DATE,series_type VARCHAR,
 path_window INTEGER,relative_session INTEGER,source_trade_date DATE,normalized_value DOUBLE,
 observed BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,method,analog_date,series_type,path_window,relative_session)
);
CREATE TABLE IF NOT EXISTS scenario_episodes(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,analog_date DATE,horizon INTEGER,
 scenario VARCHAR,terminal_return DOUBLE,max_adverse DOUBLE,max_favorable DOUBLE,
 event_family VARCHAR,systemic_shock BOOLEAN,regime_agreement BOOLEAN,event_agreement BOOLEAN,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,method,analog_date,horizon)
);
CREATE TABLE IF NOT EXISTS scenario_tree_summaries(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,horizon INTEGER,subset VARCHAR,
 scenario VARCHAR,episodes INTEGER,historical_frequency DOUBLE,median_return DOUBLE,
 q10 DOUBLE,q25 DOUBLE,q75 DOUBLE,q90 DOUBLE,median_adverse DOUBLE,medoid_analog_date DATE,
 applicability VARCHAR,status VARCHAR,reason VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,method,horizon,subset,scenario)
);
CREATE TABLE IF NOT EXISTS scenario_representative_paths(
 run_id VARCHAR,secid VARCHAR,method VARCHAR,horizon INTEGER,subset VARCHAR,
 scenario VARCHAR,medoid_analog_date DATE,forward_session INTEGER,normalized_value DOUBLE,
 source_trade_date DATE,actual_historical_path BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,method,horizon,subset,scenario,forward_session)
);
CREATE TABLE IF NOT EXISTS current_scenario_intelligence(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,leading_scenario VARCHAR,
 scenarios INTEGER,independent_episodes INTEGER,applicability VARCHAR,event_novelty VARCHAR,
 median_return DOUBLE,q10 DOUBLE,q90 DOUBLE,filtered_median_return DOUBLE,
 filtered_label VARCHAR,probability_allowed BOOLEAN,status VARCHAR,reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,cutoff,secid,horizon)
);
"""
