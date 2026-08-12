DDL = """
CREATE TABLE IF NOT EXISTS dynamic_ensemble_runs(run_id VARCHAR PRIMARY KEY,version VARCHAR,
 cutoff DATE,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,rows BIGINT,details_json JSON);
CREATE TABLE IF NOT EXISTS dynamic_ensemble_components(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
 component VARCHAR,prediction DOUBLE,reliability VARCHAR,weight DOUBLE,included BOOLEAN,reason VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,component));
CREATE TABLE IF NOT EXISTS dynamic_ensemble_forecasts(run_id VARCHAR,cutoff DATE,secid VARCHAR,
 horizon INTEGER,expected_return DOUBLE,median_return DOUBLE,lower_range DOUBLE,upper_range DOUBLE,
 expected_drawdown DOUBLE,probability_up DOUBLE,probability_allowed BOOLEAN,disagreement DOUBLE,
 confidence DOUBLE,status VARCHAR,best_model VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,cutoff,secid,horizon));
"""
