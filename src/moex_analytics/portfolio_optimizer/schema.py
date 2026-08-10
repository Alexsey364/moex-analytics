DDL = """
CREATE TABLE IF NOT EXISTS cash_aware_optimizer_runs(
 run_id VARCHAR PRIMARY KEY,opportunity_run_id VARCHAR,snapshot_id VARCHAR,cutoff DATE,
 started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,candidate_rows BIGINT,plan_rows BIGINT,
 backtest_rows BIGINT,details_json JSON,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS portfolio_tranche_candidates(
 run_id VARCHAR,secid VARCHAR,tranche DOUBLE,lot_size INTEGER,lots INTEGER,shares INTEGER,
 invested DOUBLE,cash_residual DOUBLE,new_weight DOUBLE,new_concentration DOUBLE,
 portfolio_volatility DOUBLE,downside_volatility DOUBLE,new_risk_contribution DOUBLE,
 sector_concentration DOUBLE,relative_opportunity DOUBLE,expected_median DOUBLE,
 tail_downside DOUBLE,timing VARCHAR,evidence VARCHAR,uncertainty_penalty DOUBLE,
 status VARCHAR,reason VARCHAR,immutable BOOLEAN,PRIMARY KEY(run_id,secid,tranche));
CREATE TABLE IF NOT EXISTS portfolio_allocation_plans(
 run_id VARCHAR,tranche DOUBLE,plan_rank INTEGER,allocation_json JSON,invested DOUBLE,
 cash_reserve DOUBLE,expected_median DOUBLE,tail_downside DOUBLE,concentration DOUBLE,
 portfolio_volatility DOUBLE,dominated BOOLEAN,robustness VARCHAR,status VARCHAR,
 research_only BOOLEAN,immutable BOOLEAN,PRIMARY KEY(run_id,tranche,plan_rank));
CREATE TABLE IF NOT EXISTS portfolio_optimizer_backtests(
 run_id VARCHAR,method VARCHAR,horizon INTEGER,periods INTEGER,terminal_wealth DOUBLE,cagr DOUBLE,
 volatility DOUBLE,downside_deviation DOUBLE,max_drawdown DOUBLE,turnover DOUBLE,costs DOUBLE,
 relative_hit_rate DOUBLE,worst_5pct DOUBLE,ex_post_regret DOUBLE,history_end DATE,
 executable_next_session BOOLEAN,research_only BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,method,horizon));
"""
