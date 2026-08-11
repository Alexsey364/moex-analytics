"""Persistence for Stage 97 multidimensional PIT regimes."""

DDL = """
CREATE TABLE IF NOT EXISTS conditional_regime_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ,cutoff DATE,similarity_run_id VARCHAR,
 regime_version VARCHAR,config_signature VARCHAR,timeline_rows BIGINT,analog_rows BIGINT,
 immutable BOOLEAN,production_unchanged BOOLEAN,probability_gate_unchanged BOOLEAN,
 status VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS conditional_regime_timeline(
 run_id VARCHAR,secid VARCHAR,trade_date DATE,market_trend VARCHAR,volatility_regime VARCHAR,
 rates_regime VARCHAR,stock_state VARCHAR,evidence_json JSON,history_end DATE,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,trade_date));
CREATE TABLE IF NOT EXISTS regime_conditioned_analogs(
 run_id VARCHAR,secid VARCHAR,analog_date DATE,episode_id VARCHAR,similarity_eligibility VARCHAR,
 regime_compatibility DOUBLE,scenario_role VARCHAR,eligible_for_center BOOLEAN,reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,secid,analog_date));
CREATE TABLE IF NOT EXISTS conditional_regime_transitions(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,matched_states INTEGER,transition_frequency DOUBLE,
 crisis_frequency DOUBLE,status VARCHAR,immutable BOOLEAN,PRIMARY KEY(run_id,secid,horizon));
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]

