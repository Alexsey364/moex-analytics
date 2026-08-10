"""Stage 45 persistence schema."""

DDL = """
CREATE TABLE IF NOT EXISTS analog_trajectory_runs(
 run_id VARCHAR PRIMARY KEY, analog_run_id VARCHAR, cutoff DATE, created_at TIMESTAMP,
 finished_at TIMESTAMP, status VARCHAR, trajectory_rows BIGINT, distribution_rows BIGINT,
 replay_rows BIGINT, methodology_version VARCHAR, details_json JSON
);
CREATE TABLE IF NOT EXISTS analog_forward_trajectories(
 run_id VARCHAR, secid VARCHAR, method VARCHAR, path_window INTEGER, analog_date DATE,
 forward_session INTEGER, normalized_price DOUBLE, forward_return DOUBLE,
 source_trade_date DATE, similarity_score DOUBLE, data_quality DOUBLE,
 PRIMARY KEY(run_id,secid,method,path_window,analog_date,forward_session)
);
CREATE TABLE IF NOT EXISTS analog_terminal_distributions(
 run_id VARCHAR, secid VARCHAR, method VARCHAR, path_window INTEGER, horizon INTEGER,
 effective_n INTEGER, mean_return DOUBLE, median_return DOUBLE, q10 DOUBLE, q25 DOUBLE,
 q50 DOUBLE, q75 DOUBLE, q90 DOUBLE, positive_fraction DOUBLE, negative_fraction DOUBLE,
 mean_adverse_excursion DOUBLE, mean_favorable_excursion DOUBLE, dispersion DOUBLE,
 consensus_status VARCHAR, current_price DOUBLE, terminal_reference DOUBLE,
 status VARCHAR, reason VARCHAR,
 PRIMARY KEY(run_id,secid,method,path_window,horizon)
);
CREATE TABLE IF NOT EXISTS analog_oos_replays(
 run_id VARCHAR,secid VARCHAR,cutoff DATE,horizon INTEGER,effective_n INTEGER,
 forecast_median_return DOUBLE,actual_return DOUBLE,direction_correct BOOLEAN,
 absolute_error DOUBLE,baseline_return DOUBLE,history_end DATE,train_only BOOLEAN,
 status VARCHAR,
 PRIMARY KEY(run_id,secid,cutoff,horizon)
);
"""
