DDL = """
CREATE TABLE IF NOT EXISTS whole_market_tournament_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,cutoff DATE,entries INTEGER,
 instruments INTEGER,horizons_json JSON,methodology_version VARCHAR,immutable BOOLEAN,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS whole_market_tournament_entries(
 run_id VARCHAR,scope VARCHAR,instrument VARCHAR,horizon INTEGER,variant VARCHAR,
 metric VARCHAR,observations BIGINT,score DOUBLE,baseline_score DOUBLE,improvement DOUBLE,
 ci_low DOUBLE,ci_high DOUBLE,p_value DOUBLE,adjusted_p_value DOUBLE,subperiod_stable BOOLEAN,
 regime_stable BOOLEAN,permutation_passed BOOLEAN,status VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,scope,instrument,horizon,variant,metric));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
