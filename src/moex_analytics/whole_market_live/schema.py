DDL = """
CREATE TABLE IF NOT EXISTS whole_market_live_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,market_rows INTEGER,
 sector_rows INTEGER,stock_rows INTEGER,methodology_version VARCHAR,immutable BOOLEAN,
 probability_allowed BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS live_market_forecasts(
 forecast_id VARCHAR PRIMARY KEY,run_id VARCHAR,created_at TIMESTAMPTZ,cutoff DATE,
 instrument VARCHAR,horizon INTEGER,qualitative_state VARCHAR,median_return DOUBLE,
 downside_range DOUBLE,upside_range DOUBLE,regime VARCHAR,model_version VARCHAR,
 immutable BOOLEAN,probability_allowed BOOLEAN,status VARCHAR,input_hash VARCHAR);
CREATE TABLE IF NOT EXISTS live_sector_rank_forecasts(
 forecast_id VARCHAR PRIMARY KEY,run_id VARCHAR,created_at TIMESTAMPTZ,cutoff DATE,
 sector VARCHAR,horizon INTEGER,predicted_rank INTEGER,score DOUBLE,model_version VARCHAR,
 immutable BOOLEAN,status VARCHAR,input_hash VARCHAR);
CREATE TABLE IF NOT EXISTS live_stock_rank_forecasts(
 forecast_id VARCHAR PRIMARY KEY,run_id VARCHAR,created_at TIMESTAMPTZ,cutoff DATE,
 secid VARCHAR,horizon INTEGER,predicted_rank INTEGER,qualitative_state VARCHAR,
 predicted_return DOUBLE,model_version VARCHAR,immutable BOOLEAN,probability_allowed BOOLEAN,
 status VARCHAR,input_hash VARCHAR);
CREATE TABLE IF NOT EXISTS whole_market_live_outcomes(
 forecast_id VARCHAR PRIMARY KEY,matured_at DATE,actual_return DOUBLE,direction_correct BOOLEAN,
 absolute_error DOUBLE,evaluated_at TIMESTAMPTZ,status VARCHAR,immutable BOOLEAN);
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
