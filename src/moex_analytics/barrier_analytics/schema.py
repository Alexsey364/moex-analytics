"""Persistence for Stage 99 first-passage analytics."""

DDL = """
CREATE TABLE IF NOT EXISTS conditional_barrier_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,path_run_id VARCHAR,
 barrier_version VARCHAR,config_signature VARCHAR,rows_created BIGINT,immutable BOOLEAN,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditional_barrier_results(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,upper_barrier DOUBLE,lower_barrier DOUBLE,
 is_symmetric BOOLEAN,status VARCHAR,raw_n INTEGER,effective_sample_size DOUBLE,
 upper_first_frequency DOUBLE,lower_first_frequency DOUBLE,neither_frequency DOUBLE,
 upper_first_count INTEGER,lower_first_count INTEGER,neither_count INTEGER,
 median_time_upper DOUBLE,median_time_lower DOUBLE,evidence_status VARCHAR,
 probability_published BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon,upper_barrier,lower_barrier));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
