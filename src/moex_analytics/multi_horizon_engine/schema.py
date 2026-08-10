"""Stage 57 immutable multi-horizon schema."""

DDL = """
CREATE TABLE IF NOT EXISTS multi_horizon_runs(
 run_id VARCHAR PRIMARY KEY,ranking_run_id VARCHAR,opportunity_run_id VARCHAR,cutoff DATE,
 started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,ablation_rows BIGINT,
 current_rows BIGINT,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS horizon_feature_ablation(
 run_id VARCHAR,horizon INTEGER,expert VARCHAR,feature_family VARCHAR,available BOOLEAN,
 validation_full_rank_ic DOUBLE,validation_ablated_rank_ic DOUBLE,validation_contribution DOUBLE,
 holdout_full_rank_ic DOUBLE,holdout_ablated_rank_ic DOUBLE,holdout_contribution DOUBLE,
 gate_status VARCHAR,reason VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,horizon,feature_family)
);
CREATE TABLE IF NOT EXISTS horizon_expert_policies(
 run_id VARCHAR,horizon INTEGER,expert VARCHAR,gate_rule VARCHAR,regime_expert_status VARCHAR,
 selected_families_json JSON,policy_hash VARCHAR,selection_sample VARCHAR,research_only BOOLEAN,
 immutable BOOLEAN,PRIMARY KEY(run_id,horizon)
);
CREATE TABLE IF NOT EXISTS current_horizon_term_structure(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,expert VARCHAR,
 expected_median DOUBLE,downside DOUBLE,relative_rank DOUBLE,evidence_quality VARCHAR,
 timing_status VARCHAR,term_structure_label VARCHAR,cross_horizon_interpretation VARCHAR,
 abstain BOOLEAN,status VARCHAR,reason VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,cutoff,secid,horizon)
);
"""
