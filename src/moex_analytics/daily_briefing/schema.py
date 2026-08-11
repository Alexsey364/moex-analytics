DDL = """
CREATE TABLE IF NOT EXISTS daily_investor_briefings(
 briefing_id VARCHAR PRIMARY KEY,snapshot_id VARCHAR,cutoff DATE,created_at TIMESTAMPTZ,
 previous_briefing_id VARCHAR,payload_json JSON,markdown_text VARCHAR,html_text VARCHAR,
 input_hash VARCHAR,markdown_path VARCHAR,html_path VARCHAR,immutable BOOLEAN,
 production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN);
CREATE TABLE IF NOT EXISTS daily_briefing_comparisons(
 briefing_id VARCHAR PRIMARY KEY,previous_briefing_id VARCHAR,market_change VARCHAR,
 portfolio_changes_json JSON,status_changes INTEGER,reason_changes INTEGER,immutable BOOLEAN);
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
