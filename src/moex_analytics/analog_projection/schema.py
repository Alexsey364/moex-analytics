DDL = """
CREATE TABLE IF NOT EXISTS analog_projection_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,scenario_run_id VARCHAR,
 trajectory_run_id VARCHAR,instruments INTEGER,eligible_instruments INTEGER,
 methodology_version VARCHAR,status VARCHAR,immutable BOOLEAN,production_unchanged BOOLEAN,
 probability_gate_unchanged BOOLEAN,details_json JSON);
CREATE TABLE IF NOT EXISTS analog_projected_paths(
 run_id VARCHAR,secid VARCHAR,analog_date DATE,relative_session INTEGER,source_trade_date DATE,
 historical_return DOUBLE,current_price DOUBLE,projected_price DOUBLE,similarity DOUBLE,
 is_medoid BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,analog_date,relative_session));
CREATE TABLE IF NOT EXISTS analog_projection_bands(
 run_id VARCHAR,secid VARCHAR,relative_session INTEGER,current_price DOUBLE,analog_count INTEGER,
 q10_price DOUBLE,q25_price DOUBLE,median_price DOUBLE,q75_price DOUBLE,q90_price DOUBLE,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,relative_session));
CREATE TABLE IF NOT EXISTS analog_projection_horizons(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,status VARCHAR,current_price DOUBLE,
 central_price DOUBLE,median_return DOUBLE,q10_price DOUBLE,q25_price DOUBLE,q75_price DOUBLE,
 q90_price DOUBLE,analog_count INTEGER,median_max_drawdown DOUBLE,worst_return DOUBLE,
 best_return DOUBLE,above_count INTEGER,medoid_analog_date DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
