"""Stage 20.5 schemas."""

DDL = """
CREATE TABLE IF NOT EXISTS actual_document_inventory(
 document_id VARCHAR PRIMARY KEY, issuer VARCHAR, document_type VARCHAR,
 standard VARCHAR, period VARCHAR, publication_date DATE, url VARCHAR,
 mime VARCHAR, source_hash VARCHAR, size_bytes BIGINT, parser VARCHAR,
 status VARCHAR, local_path VARCHAR, discovered_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS actual_manual_review_candidates(
 candidate_id VARCHAR PRIMARY KEY, issuer VARCHAR, metric VARCHAR, period VARCHAR,
 publication_date DATE, document_url VARCHAR, source_hash VARCHAR, page VARCHAR,
 source_table VARCHAR, row_label VARCHAR, candidate_value DOUBLE, unit VARCHAR,
 reason VARCHAR, status VARCHAR, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS actual_backfill_checkpoints(
 checkpoint VARCHAR, run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
 before_json JSON, after_json JSON, new_rows BIGINT, status VARCHAR,
 PRIMARY KEY(checkpoint,run_id)
);
CREATE TABLE IF NOT EXISTS tradable_on_date_universe(
 trade_date DATE, secid VARCHAR, board VARCHAR, close DOUBLE, volume DOUBLE,
 value DOUBLE, inactive_at_audit BOOLEAN, source VARCHAR, loaded_at TIMESTAMP,
 PRIMARY KEY(trade_date,secid,board)
);
CREATE TABLE IF NOT EXISTS universe_pilot_runs(
 run_id VARCHAR PRIMARY KEY, securities INTEGER, inactive INTEGER, requests INTEGER,
 rows_received BIGINT, rows_inserted BIGINT, errors INTEGER, elapsed_seconds DOUBLE,
 disk_bytes BIGINT, started_at TIMESTAMP, finished_at TIMESTAMP, details_json JSON
);
CREATE TABLE IF NOT EXISTS futures_spec_documents(
 secid VARCHAR, valid_from DATE, valid_to DATE, underlying VARCHAR, lot DOUBLE,
 multiplier DOUBLE, price_step DOUBLE, step_value DOUBLE, currency VARCHAR,
 expiration DATE, source_url VARCHAR, source_hash VARCHAR, units_validated BOOLEAN,
 checked_at TIMESTAMP, PRIMARY KEY(secid,valid_from)
);
CREATE TABLE IF NOT EXISTS coverage_change_history(
 run_id VARCHAR PRIMARY KEY, captured_at TIMESTAMP, before_json JSON,
 after_json JSON, storage_before BIGINT, storage_after BIGINT
);
"""
