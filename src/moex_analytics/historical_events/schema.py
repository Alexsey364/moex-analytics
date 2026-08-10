"""Stage 42 immutable event and provenance schema."""

DDL = """
CREATE TABLE IF NOT EXISTS historical_events(
 event_id VARCHAR PRIMARY KEY,event_family VARCHAR,event_type VARCHAR,event_subtype VARCHAR,
 title VARCHAR,short_description VARCHAR,event_start TIMESTAMPTZ,event_end TIMESTAMPTZ,
 announcement_time TIMESTAMPTZ,effective_time TIMESTAMPTZ,available_from TIMESTAMPTZ,
 country VARCHAR,region VARCHAR,issuer VARCHAR,sector VARCHAR,market_scope VARCHAR,
 expected_or_scheduled BOOLEAN,surprise_event BOOLEAN,severity_class VARCHAR,
 source VARCHAR,source_type VARCHAR,official_source BOOLEAN,source_reference VARCHAR,
 retrieved_at TIMESTAMP,content_hash VARCHAR,validation_status VARCHAR,pit_status VARCHAR,
 notes VARCHAR,source_table VARCHAR,source_record_id VARCHAR
);
CREATE TABLE IF NOT EXISTS historical_event_sources(
 source_id VARCHAR PRIMARY KEY,name VARCHAR,event_families_json JSON,source_type VARCHAR,
 official_source BOOLEAN,base_reference VARCHAR,license_status VARCHAR,machine_readable BOOLEAN,
 mass_download_allowed BOOLEAN,pit_capability VARCHAR,limitations VARCHAR,checked_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS historical_crisis_episodes(
 episode_id VARCHAR PRIMARY KEY,label VARCHAR,event_start DATE,event_end DATE,categories_json JSON,
 source_reference VARCHAR,validation_status VARCHAR,explainability_only BOOLEAN,notes VARCHAR
);
CREATE TABLE IF NOT EXISTS historical_event_timeline(
 trade_date DATE,event_id VARCHAR,secid VARCHAR,days_since_event INTEGER,
 days_until_scheduled_event INTEGER,event_state VARCHAR,pit_safe BOOLEAN,
 calculation_version VARCHAR,calculated_at TIMESTAMP,
 PRIMARY KEY(trade_date,event_id,secid,calculation_version)
);
CREATE TABLE IF NOT EXISTS historical_event_quality_issues(
 issue_id VARCHAR PRIMARY KEY,event_id VARCHAR,issue_type VARCHAR,severity VARCHAR,
 description VARCHAR,detected_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS historical_event_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 source_rows BIGINT,events_written BIGINT,timeline_rows BIGINT,issues BIGINT,
 methodology_version VARCHAR,details_json JSON
);
"""
