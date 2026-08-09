"""Stage 31-33 research-only schema."""

DDL = """
CREATE TABLE IF NOT EXISTS corporate_action_candidate_episodes(
 episode_id VARCHAR PRIMARY KEY,secid VARCHAR,date_from DATE,date_to DATE,priority VARCHAR,
 flag_count INTEGER,boardids_json JSON,raw_price_before DOUBLE,raw_price_after DOUBLE,
 observed_ratio DOUBLE,candidate_ratio DOUBLE,ratio_error DOUBLE,candidate_type VARCHAR,
 evidence_status VARCHAR,review_status VARCHAR,created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS corporate_action_evidence(
 evidence_id VARCHAR PRIMARY KEY,episode_id VARCHAR,source VARCHAR,endpoint VARCHAR,
 retrieved_at TIMESTAMP,source_hash VARCHAR,publication_date DATE,effective_date DATE,
 action_type VARCHAR,ratio DOUBLE,old_security VARCHAR,new_security VARCHAR,
 validation_status VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS research_price_adjustments(
 adjustment_id VARCHAR PRIMARY KEY,secid VARCHAR,effective_date DATE,action_type VARCHAR,
 adjustment_factor DOUBLE,evidence_id VARCHAR,available_from TIMESTAMP,
 validation_status VARCHAR,created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS research_adjusted_prices(
 trade_date DATE,secid VARCHAR,boardid VARCHAR,raw_price DOUBLE,
 canonical_unadjusted_price DOUBLE,research_adjusted_price DOUBLE,
 cumulative_adjustment_factor DOUBLE,provenance_json JSON,version VARCHAR,
 PRIMARY KEY(trade_date,secid,boardid,version)
);
CREATE TABLE IF NOT EXISTS historical_quality_v2(
 secid VARCHAR PRIMARY KEY,priority VARCHAR,history_years DOUBLE,observations BIGINT,
 canonical_continuity DOUBLE,corporate_action_resolution DOUBLE,liquidity_coverage DOUBLE,
 numtrades_coverage DOUBLE,board_confidence DOUBLE,missingness DOUBLE,
 pit_context_coverage DOUBLE,fundamental_coverage DOUBLE,quality_score DOUBLE,
 training_tier VARCHAR,exclusion_reason VARCHAR,policy_version VARCHAR,calculated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS corporate_action_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 flags_before BIGINT,episodes BIGINT,auto_validated BIGINT,manual_review BIGINT,
 unresolved BIGINT,adjusted_rows BIGINT,tier_a INTEGER,tier_b INTEGER,tier_c INTEGER,
 excluded INTEGER,review_path VARCHAR,production_changes INTEGER,details_json JSON
);
"""
