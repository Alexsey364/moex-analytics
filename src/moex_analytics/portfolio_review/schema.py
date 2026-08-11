DDL = """
CREATE TABLE IF NOT EXISTS portfolio_review_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,verdict_run_id VARCHAR,
 instruments INTEGER,methodology_version VARCHAR,consistency_hash VARCHAR,
 production_unchanged BOOLEAN,immutable BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS portfolio_review_allocations(
 run_id VARCHAR,amount DOUBLE,allocation_json JSON,cash_reserve DOUBLE,status VARCHAR,reason VARCHAR,
 PRIMARY KEY(run_id,amount));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
