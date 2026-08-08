"""Persistence schema for stage 20 historical-data governance."""

DDL = """
CREATE TABLE IF NOT EXISTS historical_data_coverage(
    instrument VARCHAR, dataset_family VARCHAR, dataset_id VARCHAR,
    source VARCHAR, access_class VARCHAR, license VARCHAR,
    earliest_date DATE, latest_date DATE, observation_count BIGINT,
    expected_frequency VARCHAR, completeness DOUBLE, pit_status VARCHAR,
    revision_support BOOLEAN, survivorship_safe BOOLEAN, current_status VARCHAR,
    analytical_priority VARCHAR, estimated_value_for_prediction VARCHAR,
    estimated_cost VARCHAR, blocker VARCHAR, recommended_action VARCHAR,
    pit_integrity_score DOUBLE, audited_at TIMESTAMP,
    PRIMARY KEY(instrument, dataset_family, dataset_id)
);
CREATE TABLE IF NOT EXISTS historical_issuer_mapping(
    issuer_group VARCHAR, secid VARCHAR, valid_from DATE, valid_to DATE,
    relation_type VARCHAR, validated BOOLEAN, source VARCHAR, evidence VARCHAR,
    PRIMARY KEY(issuer_group, secid, valid_from)
);
CREATE TABLE IF NOT EXISTS historical_sector_membership(
    secid VARCHAR, sector VARCHAR, valid_from DATE, valid_to DATE,
    source VARCHAR, official BOOLEAN, pit_status VARCHAR,
    PRIMARY KEY(secid, sector, valid_from)
);
CREATE TABLE IF NOT EXISTS historical_corporate_actions(
    action_id VARCHAR PRIMARY KEY, issuer_group VARCHAR, secid_before VARCHAR,
    secid_after VARCHAR, action_type VARCHAR, effective_date DATE,
    announced_at TIMESTAMPTZ, ratio DOUBLE, source VARCHAR, document_hash VARCHAR,
    validation_status VARCHAR, notes VARCHAR
);
CREATE TABLE IF NOT EXISTS historical_dividend_audit(
    secid VARCHAR, record_date DATE, dps DOUBLE, recommendation_date DATE,
    approval_date DATE, payment_date DATE, status VARCHAR, currency VARCHAR,
    share_class VARCHAR, source VARCHAR, validation_status VARCHAR,
    issue VARCHAR, PRIMARY KEY(secid, record_date, source)
);
CREATE TABLE IF NOT EXISTS external_factor_catalog(
    factor_id VARCHAR PRIMARY KEY, family VARCHAR, source VARCHAR, endpoint VARCHAR,
    license VARCHAR, access_class VARCHAR, timestamp_rule VARCHAR,
    pit_status VARCHAR, current_status VARCHAR, notes VARCHAR, checked_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS futures_contract_validation(
    secid VARCHAR PRIMARY KEY, family VARCHAR, spot_scale DOUBLE,
    futures_scale DOUBLE, multiplier DOUBLE, lot DOUBLE, currency VARCHAR,
    expiration DATE, carry_assumptions VARCHAR, units_validated BOOLEAN,
    basis_enabled BOOLEAN, source VARCHAR, checked_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS historical_data_quality_issues(
    issue_id VARCHAR PRIMARY KEY, dataset_id VARCHAR, instrument VARCHAR,
    issue_type VARCHAR, severity VARCHAR, details VARCHAR, detected_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS data_value_ablation_results(
    instrument VARCHAR, horizon INTEGER, block_name VARCHAR, same_sample_rows BIGINT,
    folds INTEGER, embargo INTEGER, baseline_metric DOUBLE, candidate_metric DOUBLE,
    improvement DOUBLE, ci_low DOUBLE, ci_high DOUBLE, oos BOOLEAN,
    status VARCHAR, run_hash VARCHAR, details_json JSON,
    PRIMARY KEY(instrument, horizon, block_name, run_hash)
);
CREATE TABLE IF NOT EXISTS historical_storage_audit(
    audited_at TIMESTAMP PRIMARY KEY, duckdb_bytes BIGINT, raw_bytes BIGINT,
    processed_bytes BIGINT, cache_bytes BIGINT, backfill_estimate_bytes BIGINT,
    intraday_estimate_bytes BIGINT, options_estimate_bytes BIGINT,
    retention_policy VARCHAR
);
CREATE TABLE IF NOT EXISTS historical_audit_runs(
    run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
    status VARCHAR, downloaded_rows BIGINT, details_json JSON
);
"""
