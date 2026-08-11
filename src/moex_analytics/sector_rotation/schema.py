DDL = """
CREATE TABLE IF NOT EXISTS sector_rotation_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,date_from DATE,date_to DATE,holdout_from DATE,
 sectors INTEGER,observations BIGINT,methodology_version VARCHAR,immutable BOOLEAN,status VARCHAR,
 details_json JSON);
CREATE TABLE IF NOT EXISTS sector_rotation_scores(
 run_id VARCHAR,trade_date DATE,sector VARCHAR,horizon INTEGER,momentum_score DOUBLE,
 predicted_rank INTEGER,actual_excess_return DOUBLE,actual_rank INTEGER,sample VARCHAR,
 PRIMARY KEY(run_id,trade_date,sector,horizon));
CREATE TABLE IF NOT EXISTS sector_rotation_scorecards(
 run_id VARCHAR,horizon INTEGER,sample VARCHAR,observations BIGINT,rank_ic DOUBLE,
 top_bottom_spread DOUBLE,baseline_rank_ic DOUBLE,status VARCHAR,PRIMARY KEY(run_id,horizon,sample));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
