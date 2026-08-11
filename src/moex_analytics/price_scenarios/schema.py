DDL = """
CREATE TABLE IF NOT EXISTS price_scenario_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,projection_run_id VARCHAR,
 distribution_run_id VARCHAR,instruments INTEGER,branches INTEGER,methodology_version VARCHAR,
 status VARCHAR,immutable BOOLEAN,production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,
 details_json JSON);
CREATE TABLE IF NOT EXISTS price_scenario_layers(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,analog_status VARCHAR,analog_central_price DOUBLE,
 analog_q10_price DOUBLE,analog_q25_price DOUBLE,analog_q75_price DOUBLE,analog_q90_price DOUBLE,
 model_status VARCHAR,model_method VARCHAR,model_q10_price DOUBLE,model_q25_price DOUBLE,
 model_q50_price DOUBLE,model_q75_price DOUBLE,model_q90_price DOUBLE,consensus_status VARCHAR,
 consensus_reason VARCHAR,immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon));
CREATE TABLE IF NOT EXISTS price_scenario_branches(
 run_id VARCHAR,secid VARCHAR,branch VARCHAR,label VARCHAR,episodes INTEGER,
 medoid_analog_date DATE,terminal_prices_json JSON,max_drawdown DOUBLE,status VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,branch));
CREATE TABLE IF NOT EXISTS price_scenario_touch_memory(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,analog_count INTEGER,touch_down_5 INTEGER,
 touch_down_10 INTEGER,touch_up_5 INTEGER,touch_up_10 INTEGER,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
