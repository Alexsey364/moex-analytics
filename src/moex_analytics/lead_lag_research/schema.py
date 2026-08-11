DDL = """
CREATE TABLE IF NOT EXISTS lead_lag_runs(run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,date_from DATE,
date_to DATE,instruments INTEGER,signals INTEGER,methodology_version VARCHAR,causality_claimed BOOLEAN,
immutable BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS lead_lag_scorecards(run_id VARCHAR,secid VARCHAR,signal VARCHAR,lag INTEGER,
horizon INTEGER,train_correlation DOUBLE,holdout_correlation DOUBLE,mutual_information DOUBLE,
coefficient DOUBLE,observations BIGINT,status VARCHAR,PRIMARY KEY(run_id,secid,signal,lag,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
