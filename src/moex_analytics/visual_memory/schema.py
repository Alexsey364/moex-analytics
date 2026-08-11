DDL = """
CREATE TABLE IF NOT EXISTS visual_memory_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,scenario_run_id VARCHAR,
 snapshots INTEGER,methodology_version VARCHAR,production_unchanged BOOLEAN,
 probability_gate_unchanged BOOLEAN,immutable BOOLEAN,status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS visual_memory_snapshots(
 run_id VARCHAR,instrument VARCHAR,horizon INTEGER,comparison_mode VARCHAR,method VARCHAR,
 prehistory_window INTEGER,cutoff DATE,sample INTEGER,status VARCHAR,reason VARCHAR,
 current_path_json JSON,analog_paths_json JSON,bands_json JSON,cards_json JSON,
 summary_json JSON,why_json JSON,scenarios_json JSON,immutable BOOLEAN,
 PRIMARY KEY(run_id,instrument,horizon,comparison_mode));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
