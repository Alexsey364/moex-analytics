"""Immutable portfolio-aware research schema."""

DDL = """
CREATE TABLE IF NOT EXISTS portfolio_learning_runs(
 run_id VARCHAR PRIMARY KEY,snapshot_id VARCHAR,created_at TIMESTAMP,status VARCHAR,
 runtime_seconds DOUBLE,candidates INTEGER,note VARCHAR
);
CREATE TABLE IF NOT EXISTS portfolio_marginal_candidates(
 run_id VARCHAR,secid VARCHAR,tranche DOUBLE,shares DOUBLE,lots INTEGER,invested DOUBLE,
 delta_weight DOUBLE,delta_volatility DOUBLE,delta_downside DOUBLE,delta_concentration DOUBLE,
 delta_risk_contribution DOUBLE,delta_sector_concentration DOUBLE,correlation_effect DOUBLE,
 predictive_attractiveness DOUBLE,fundamental_attractiveness DOUBLE,valuation DOUBLE,
 dividend DOUBLE,timing DOUBLE,standalone_risk DOUBLE,diversification_benefit DOUBLE,
 concentration_cost DOUBLE,portfolio_rank DOUBLE,eligible BOOLEAN,status VARCHAR,reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,tranche)
);
CREATE TABLE IF NOT EXISTS portfolio_learning_backtests(
 run_id VARCHAR,method VARCHAR,date_from DATE,date_to DATE,observations INTEGER,total_return DOUBLE,
 volatility DOUBLE,downside_volatility DOUBLE,max_drawdown DOUBLE,turnover DOUBLE,
 commissions DOUBLE,delayed_execution INTEGER,lot_aware BOOLEAN,research_only BOOLEAN,
 PRIMARY KEY(run_id,method)
);
"""
