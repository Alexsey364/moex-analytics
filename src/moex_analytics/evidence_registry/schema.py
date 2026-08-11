DDL = """
CREATE TABLE IF NOT EXISTS evidence_registry_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,blocks BIGINT,instruments INTEGER,
 methodology_version VARCHAR,production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,
 immutable BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS evidence_registry_blocks(
 run_id VARCHAR,instrument VARCHAR,horizon INTEGER,block_type VARCHAR,model_version VARCHAR,
 sample_n BIGINT,effective_n DOUBLE,oos_metric DOUBLE,baseline_metric DOUBLE,
 absolute_improvement DOUBLE,relative_improvement DOUBLE,ci_low DOUBLE,ci_high DOUBLE,
 fold_stable BOOLEAN,subperiod_stable BOOLEAN,regime_stable BOOLEAN,data_quality VARCHAR,
 freshness VARCHAR,live_n BIGINT,evidence_status VARCHAR,decision_eligible BOOLEAN,reason VARCHAR,
 details_json JSON,PRIMARY KEY(run_id,instrument,horizon,block_type));
CREATE TABLE IF NOT EXISTS evidence_decision_audit(
 run_id VARCHAR,instrument VARCHAR,horizon INTEGER,block_type VARCHAR,used BOOLEAN,role VARCHAR,
 reason VARCHAR,PRIMARY KEY(run_id,instrument,horizon,block_type));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
