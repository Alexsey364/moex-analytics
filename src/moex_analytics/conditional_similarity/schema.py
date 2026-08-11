"""Persistence for Stage 95 conditional state similarity research."""

DDL = """
CREATE TABLE IF NOT EXISTS conditional_similarity_runs(
 run_id VARCHAR PRIMARY KEY, created_at TIMESTAMPTZ, cutoff DATE, feature_version VARCHAR,
 similarity_version VARCHAR, config_signature VARCHAR, instruments INTEGER,
 candidates BIGINT, accepted BIGINT, immutable BOOLEAN, production_unchanged BOOLEAN,
 probability_gate_unchanged BOOLEAN, status VARCHAR, details_json JSON);
CREATE TABLE IF NOT EXISTS conditional_state_coverage(
 run_id VARCHAR, secid VARCHAR, family VARCHAR, feature_count INTEGER, coverage DOUBLE,
 missing_count BIGINT, first_valid_date DATE, last_valid_date DATE, immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,family));
CREATE TABLE IF NOT EXISTS conditional_analog_diagnostics(
 run_id VARCHAR, secid VARCHAR, analog_date DATE, representative_date DATE, episode_id VARCHAR,
 total_distance DOUBLE, total_similarity DOUBLE, price_similarity DOUBLE,
 volatility_similarity DOUBLE, market_similarity DOUBLE, rates_similarity DOUBLE,
 fx_commodities_similarity DOUBLE, sector_similarity DOUBLE, fundamental_similarity DOUBLE,
 regime_compatibility DOUBLE, eligibility VARCHAR, rejection_reason VARCHAR,
 available_families_json JSON, missing_families_json JSON, family_breakdown_json JSON,
 history_end DATE, immutable BOOLEAN, PRIMARY KEY(run_id,secid,analog_date));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]

