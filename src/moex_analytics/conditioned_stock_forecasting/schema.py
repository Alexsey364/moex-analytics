DDL = """
CREATE TABLE IF NOT EXISTS conditioned_stock_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,date_from DATE,date_to DATE,holdout_from DATE,
 instruments INTEGER,methodology_version VARCHAR,production_unchanged BOOLEAN,immutable BOOLEAN,
 status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditioned_stock_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,feature_block VARCHAR,observations BIGINT,
 baseline_mae DOUBLE,model_mae DOUBLE,improvement DOUBLE,return_correlation DOUBLE,
 status VARCHAR,details_json JSON,PRIMARY KEY(run_id,secid,horizon,feature_block));
CREATE TABLE IF NOT EXISTS conditioned_stock_oos(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,feature_block VARCHAR,trade_date DATE,
 actual_return DOUBLE,baseline_prediction DOUBLE,candidate_prediction DOUBLE,
 baseline_absolute_error DOUBLE,candidate_absolute_error DOUBLE,mae_gain DOUBLE,
 PRIMARY KEY(run_id,secid,horizon,feature_block,trade_date));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
    con.execute("ALTER TABLE conditioned_stock_scorecards ADD COLUMN IF NOT EXISTS ci_low DOUBLE")
    con.execute("ALTER TABLE conditioned_stock_scorecards ADD COLUMN IF NOT EXISTS ci_high DOUBLE")
    con.execute("ALTER TABLE conditioned_stock_scorecards ADD COLUMN IF NOT EXISTS fold_stable BOOLEAN")
