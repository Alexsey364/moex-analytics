DDL = """
CREATE TABLE IF NOT EXISTS decision_outcome_records(
 decision_id VARCHAR PRIMARY KEY,source_type VARCHAR,decision_date DATE,secid VARCHAR,
 decision_type VARCHAR,source_snapshot_id VARCHAR,source_report_id VARCHAR,created_at TIMESTAMPTZ,
 immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS canonical_live_decisions(
 decision_date DATE,secid VARCHAR,decision_id VARCHAR UNIQUE,first_snapshot_id VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(decision_date,secid));
CREATE TABLE IF NOT EXISTS decision_realized_outcomes(
 decision_id VARCHAR,horizon INTEGER,maturity_date DATE,absolute_return DOUBLE,
 relative_return DOUBLE,max_drawdown DOUBLE,mfe DOUBLE,volatility DOUBLE,rank_change DOUBLE,
 objective_metric VARCHAR,outcome_status VARCHAR,evaluated_at TIMESTAMPTZ,immutable BOOLEAN,
 PRIMARY KEY(decision_id,horizon));
CREATE TABLE IF NOT EXISTS decision_outcome_scorecards(
 source_type VARCHAR,decision_type VARCHAR,horizon INTEGER,observations INTEGER,
 median_return DOUBLE,median_relative_return DOUBLE,median_drawdown DOUBLE,median_mfe DOUBLE,
 objective_metric VARCHAR,sample_status VARCHAR,calculated_at TIMESTAMPTZ,
 PRIMARY KEY(source_type,decision_type,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
