DDL = """
CREATE TABLE IF NOT EXISTS daily_decision_states(
 snapshot_id VARCHAR,cutoff DATE,secid VARCHAR,status VARCHAR,horizon_states_json JSON,
 rank_group VARCHAR,risk_state VARCHAR,market_state VARCHAR,sector_state VARCHAR,
 analog_state VARCHAR,news_state VARCHAR,portfolio_action VARCHAR,top_reasons_json JSON,
 source_report_id VARCHAR,immutable BOOLEAN,PRIMARY KEY(snapshot_id,secid));
CREATE TABLE IF NOT EXISTS daily_decision_changes(
 snapshot_id VARCHAR,previous_snapshot_id VARCHAR,cutoff DATE,secid VARCHAR,
 change_state VARCHAR,material BOOLEAN,changed_blocks_json JSON,reasons_json JSON,
 immutable BOOLEAN,PRIMARY KEY(snapshot_id,secid));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
