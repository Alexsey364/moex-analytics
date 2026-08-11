DDL = """
CREATE TABLE IF NOT EXISTS state_similarity_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,instruments INTEGER,
 matches INTEGER,validations INTEGER,methodology_version VARCHAR,immutable BOOLEAN,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS state_similarity_matches(
 run_id VARCHAR,secid VARCHAR,analog_type VARCHAR,analog_date DATE,rank INTEGER,
 distance DOUBLE,similarity DOUBLE,factors_json JSON,history_end DATE,independent BOOLEAN,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,analog_type,analog_date));
CREATE TABLE IF NOT EXISTS state_similarity_outcomes(
 run_id VARCHAR,secid VARCHAR,analog_type VARCHAR,analog_date DATE,horizon INTEGER,
 terminal_return DOUBLE,relative_return DOUBLE,max_drawdown DOUBLE,mfe DOUBLE,volatility DOUBLE,
 observed_until DATE,immutable BOOLEAN,PRIMARY KEY(run_id,secid,analog_type,analog_date,horizon));
CREATE TABLE IF NOT EXISTS state_similarity_validation(
 run_id VARCHAR,secid VARCHAR,analog_type VARCHAR,horizon INTEGER,observations INTEGER,
 mae DOUBLE,baseline_mae DOUBLE,mae_improvement DOUBLE,median_downside DOUBLE,
 scenario_usefulness DOUBLE,status VARCHAR,combined_weight_allowed BOOLEAN,train_only BOOLEAN,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,analog_type,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
