DDL = """
CREATE TABLE IF NOT EXISTS portfolio_verdict_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,evidence_run_id VARCHAR,
 instruments INTEGER,methodology_version VARCHAR,production_unchanged BOOLEAN,
 probability_gate_unchanged BOOLEAN,immutable BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS portfolio_horizon_verdicts(
 run_id VARCHAR,instrument VARCHAR,horizon INTEGER,directional_state VARCHAR,
 evidence_strength VARCHAR,relative_group VARCHAR,relative_rank INTEGER,market_effect VARCHAR,
 sector_effect VARCHAR,strongest_evidence VARCHAR,analog_effect VARCHAR,news_effect VARCHAR,
 downside_state VARCHAR,portfolio_concentration VARCHAR,live_evidence VARCHAR,
 top_for_json JSON,top_against_json JSON,improve_json JSON,worsen_json JSON,
 conflict BOOLEAN,decision_eligible_blocks_json JSON,PRIMARY KEY(run_id,instrument,horizon));
CREATE TABLE IF NOT EXISTS portfolio_final_verdicts(
 run_id VARCHAR,instrument VARCHAR,current_status VARCHAR,portfolio_action VARCHAR,risk_status VARCHAR,
 human_verdict VARCHAR,top_for_json JSON,top_against_json JSON,improve_json JSON,worsen_json JSON,
 PRIMARY KEY(run_id,instrument));
CREATE TABLE IF NOT EXISTS investment_allocation_views(
 run_id VARCHAR,instrument VARCHAR,investment_status VARCHAR,investment_reason VARCHAR,
 allocation_status VARCHAR,allocation_reason VARCHAR,portfolio_mode VARCHAR,
 current_weight DOUBLE,target_weight DOUBLE,max_weight DOUBLE,allow_buy BOOLEAN,
 investment_inputs_json JSON,allocation_inputs_json JSON,immutable BOOLEAN,
 PRIMARY KEY(run_id,instrument));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
