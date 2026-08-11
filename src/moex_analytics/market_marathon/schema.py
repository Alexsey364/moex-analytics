DDL = """
CREATE TABLE IF NOT EXISTS market_marathon_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMPTZ,finished_at TIMESTAMPTZ,cutoff DATE,
 dataset_hash VARCHAR,dataset_json JSON,status VARCHAR,runtime_seconds DOUBLE,
 methodology_version VARCHAR,max_runtime_seconds INTEGER,production_changes INTEGER,
 probability_gate_changed BOOLEAN,details_json JSON);
CREATE TABLE IF NOT EXISTS market_marathon_checkpoints(
 run_id VARCHAR,step VARCHAR,started_at TIMESTAMPTZ,finished_at TIMESTAMPTZ,status VARCHAR,
 result_json JSON,error VARCHAR,PRIMARY KEY(run_id,step));
CREATE TABLE IF NOT EXISTS market_dashboard_snapshots(
 snapshot_id VARCHAR PRIMARY KEY,run_id VARCHAR,created_at TIMESTAMPTZ,cutoff DATE,
 market_json JSON,sectors_json JSON,stocks_json JSON,live_json JSON,immutable BOOLEAN);
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
