"""Stage 55 immutable timing research schema."""

DDL = """
CREATE TABLE IF NOT EXISTS timing_research_runs(
 run_id VARCHAR PRIMARY KEY,target_run_id VARCHAR,ranking_run_id VARCHAR,dataset_version VARCHAR,
 cutoff DATE,train_end DATE,validation_end DATE,holdout_start DATE,started_at TIMESTAMP,
 finished_at TIMESTAMP,status VARCHAR,outcome_rows BIGINT,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS timing_policy_definitions(
 policy VARCHAR,version VARCHAR,signal_rule VARCHAR,execution_rule VARCHAR,max_wait INTEGER,
 commission_bps DOUBLE,perfect_hindsight BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(policy,version)
);
CREATE TABLE IF NOT EXISTS timing_policy_outcomes(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,horizon INTEGER,policy VARCHAR,signal_date DATE,
 entry_date DATE,exit_date DATE,entered BOOLEAN,wait_sessions INTEGER,entry_index DOUBLE,
 net_return DOUBLE,buy_now_return DOUBLE,entry_improvement DOUBLE,missed_upside DOUBLE,
 max_drawdown DOUBLE,volatility_state VARCHAR,regime INTEGER,sample_type VARCHAR,
 history_end DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,trade_date,secid,horizon,policy)
);
CREATE TABLE IF NOT EXISTS timing_policy_scorecards(
 run_id VARCHAR,horizon INTEGER,policy VARCHAR,sample_type VARCHAR,context VARCHAR,
 context_value VARCHAR,cases BIGINT,entered_cases BIGINT,no_entry_rate DOUBLE,
 mean_return DOUBLE,median_entry_improvement DOUBLE,mean_missed_upside DOUBLE,
 mean_max_drawdown DOUBLE,delta_vs_buy_now DOUBLE,ci_low DOUBLE,ci_high DOUBLE,status VARCHAR,
 PRIMARY KEY(run_id,horizon,policy,sample_type,context,context_value)
);
CREATE TABLE IF NOT EXISTS timing_policy_selections(
 run_id VARCHAR,horizon INTEGER,policy VARCHAR,validation_delta DOUBLE,validation_ci_low DOUBLE,
 selected BOOLEAN,policy_hash VARCHAR,selection_sample VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,horizon,policy)
);
CREATE TABLE IF NOT EXISTS current_timing_intelligence(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,selected_policy VARCHAR,
 validation_delta DOUBLE,holdout_delta DOUBLE,holdout_ci_low DOUBLE,holdout_ci_high DOUBLE,
 no_entry_rate DOUBLE,median_entry_improvement DOUBLE,mean_missed_upside DOUBLE,
 evidence VARCHAR,timing_status VARCHAR,reason VARCHAR,broker_order BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,cutoff,secid,horizon)
);
"""
