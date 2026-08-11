DDL = """
CREATE TABLE IF NOT EXISTS market_analog_fusion_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,cutoff DATE,source_analog_run VARCHAR,
 observations BIGINT,instruments INTEGER,methodology_version VARCHAR,immutable BOOLEAN,
 production_unchanged BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS market_analog_fusion_oos(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,cutoff DATE,analog_prediction DOUBLE,
 fused_prediction DOUBLE,actual_return DOUBLE,analog_error DOUBLE,fused_error DOUBLE,
 direction_correct BOOLEAN,train_end DATE,market_feature DOUBLE,sector_feature DOUBLE,
 event_context_available BOOLEAN,PRIMARY KEY(run_id,secid,horizon,cutoff));
CREATE TABLE IF NOT EXISTS market_analog_fusion_scorecards(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,observations BIGINT,analog_mae DOUBLE,
 fused_mae DOUBLE,improvement DOUBLE,direction_accuracy DOUBLE,status VARCHAR,
 PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
