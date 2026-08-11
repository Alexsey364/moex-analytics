DDL = """
CREATE TABLE IF NOT EXISTS daily_intelligence_snapshots(
 snapshot_id VARCHAR PRIMARY KEY,cutoff DATE,created_at TIMESTAMPTZ,compatibility VARCHAR,
 component_hash VARCHAR,compatibility_hash VARCHAR,fast_current INTEGER,fast_total INTEGER,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,immutable BOOLEAN,
 source_update_run VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS daily_intelligence_components(
 snapshot_id VARCHAR,component VARCHAR,family VARCHAR,cutoff DATE,status VARCHAR,reason VARCHAR,
 source_id VARCHAR,component_hash VARCHAR,required_for_current BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(snapshot_id,component));
CREATE TABLE IF NOT EXISTS daily_analog_contexts(
 snapshot_id VARCHAR,instrument VARCHAR,comparison_mode VARCHAR,current_cutoff DATE,
 analog_source_cutoff DATE,source_visual_run VARCHAR,current_path_json JSON,status VARCHAR,
 reason VARCHAR,immutable BOOLEAN,PRIMARY KEY(snapshot_id,instrument,comparison_mode));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
