DDL = """
CREATE TABLE IF NOT EXISTS portfolio_scenario_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,source_state_run VARCHAR,
 episodes INTEGER,branches INTEGER,methodology_version VARCHAR,immutable BOOLEAN,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS portfolio_scenario_roots(
 run_id VARCHAR PRIMARY KEY,cutoff DATE,market_regime VARCHAR,breadth_json JSON,rates_json JSON,
 fx_json JSON,oil_json JSON,volatility_json JSON,news_overlay_json JSON,news_weights_changed BOOLEAN,
 immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS portfolio_scenario_branches(
 run_id VARCHAR,branch_id VARCHAR,label VARCHAR,episodes INTEGER,total_episodes INTEGER,
 median_imoex_return DOUBLE,median_drawdown DOUBLE,median_rvi_change DOUBLE,
 median_rub_change DOUBLE,median_rgbi_change DOUBLE,sector_outcomes_json JSON,
 representative_date DATE,historical_frequency_text VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,branch_id));
CREATE TABLE IF NOT EXISTS portfolio_scenario_paths(
 run_id VARCHAR,branch_id VARCHAR,analog_date DATE,relative_session INTEGER,
 source_trade_date DATE,normalized_imoex DOUBLE,observed BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,branch_id,analog_date,relative_session));
CREATE TABLE IF NOT EXISTS portfolio_scenario_sensitivities(
 run_id VARCHAR,branch_id VARCHAR,secid VARCHAR,episodes INTEGER,median_return DOUBLE,
 median_relative_return DOUBLE,median_drawdown DOUBLE,resilience VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,branch_id,secid));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
