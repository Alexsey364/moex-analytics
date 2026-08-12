DDL = """
CREATE TABLE IF NOT EXISTS macro_sensitivity_runs(run_id VARCHAR PRIMARY KEY,version VARCHAR,
 started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,rows BIGINT,details_json JSON);
CREATE TABLE IF NOT EXISTS macro_sensitivity_results(run_id VARCHAR,secid VARCHAR,factor VARCHAR,
 rolling_window INTEGER,observations INTEGER,sensitivity DOUBLE,sign_stability DOUBLE,
 oos_improvement DOUBLE,
 status VARCHAR,contribution_allowed BOOLEAN,details_json JSON,
 PRIMARY KEY(run_id,secid,factor,rolling_window));
"""
