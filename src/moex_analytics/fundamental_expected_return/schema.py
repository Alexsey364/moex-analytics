DDL = """
CREATE TABLE IF NOT EXISTS fundamental_return_runs(run_id VARCHAR PRIMARY KEY,version VARCHAR,
 started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,rows BIGINT,details_json JSON);
CREATE TABLE IF NOT EXISTS fundamental_return_estimates(run_id VARCHAR,as_of_date DATE,secid VARCHAR,
 horizon INTEGER,current_price DOUBLE,dividend_component DOUBLE,earnings_component DOUBLE,
 rerating_component DOUBLE,expected_total_return DOUBLE,fair_value_low DOUBLE,fair_value_high DOUBLE,
 reliability DOUBLE,status VARCHAR,range_type VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,as_of_date,secid,horizon));
CREATE TABLE IF NOT EXISTS fundamental_return_backtests(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
 observations INTEGER,rank_ic DOUBLE,mae DOUBLE,baseline_mae DOUBLE,status VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,secid,horizon));
"""
