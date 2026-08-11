DDL = """
CREATE TABLE IF NOT EXISTS conditioned_stock_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,date_from DATE,date_to DATE,holdout_from DATE,
 instruments INTEGER,methodology_version VARCHAR,production_unchanged BOOLEAN,immutable BOOLEAN,
 status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditioned_stock_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,feature_block VARCHAR,observations BIGINT,
 baseline_mae DOUBLE,model_mae DOUBLE,improvement DOUBLE,return_correlation DOUBLE,
 status VARCHAR,details_json JSON,PRIMARY KEY(run_id,secid,horizon,feature_block));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
