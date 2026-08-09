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
CREATE TABLE IF NOT EXISTS training_universe_runs(
 dataset_version VARCHAR PRIMARY KEY,created_at TIMESTAMP,cutoff DATE,status VARCHAR,
 raw_securities INTEGER,eligible_securities INTEGER,rows BIGINT,dates BIGINT,
 feature_schema_hash VARCHAR,quality_policy_version VARCHAR,corporate_action_version VARCHAR,
 frozen BOOLEAN,production_changes INTEGER,details_json JSON
);
CREATE TABLE IF NOT EXISTS historical_training_eligibility(
 dataset_version VARCHAR,trade_date DATE,secid VARCHAR,horizon INTEGER,
 quality_tier VARCHAR,liquidity_tier VARCHAR,history_rows BIGINT,median_turnover_20 DOUBLE,
 eligible BOOLEAN,exclusion_reason VARCHAR,issuer_group VARCHAR,
 PRIMARY KEY(dataset_version,trade_date,secid,horizon)
);
CREATE TABLE IF NOT EXISTS historical_training_panel(
 dataset_version VARCHAR,trade_date DATE,secid VARCHAR,issuer_group VARCHAR,
 quality_tier VARCHAR,liquidity_tier VARCHAR,currently_inactive BOOLEAN,price DOUBLE,
 return_1d DOUBLE,turnover_20 DOUBLE,liquidity_percentile DOUBLE,breadth_balance DOUBLE,
 market_state VARCHAR,key_rate DOUBLE,usd_rub DOUBLE,sector_proxy DOUBLE,
 fundamental_available BOOLEAN,dividend_yield DOUBLE,futures_oi_change DOUBLE,
 target_5 DOUBLE,target_20 DOUBLE,target_60 DOUBLE,target_120 DOUBLE,target_250 DOUBLE,
 excess_5 DOUBLE,excess_20 DOUBLE,excess_60 DOUBLE,excess_120 DOUBLE,excess_250 DOUBLE,
 rank_5 DOUBLE,rank_20 DOUBLE,rank_60 DOUBLE,rank_120 DOUBLE,rank_250 DOUBLE,
 PRIMARY KEY(dataset_version,trade_date,secid)
);
CREATE TABLE IF NOT EXISTS breadth4_daily(
 dataset_version VARCHAR,universe_kind VARCHAR,trade_date DATE,constituents INTEGER,
 advancing INTEGER,declining INTEGER,equal_weight_return DOUBLE,return_dispersion DOUBLE,
 momentum_dispersion DOUBLE,liquidity_dispersion DOUBLE,drawdown_gt_20 INTEGER,
 PRIMARY KEY(dataset_version,universe_kind,trade_date)
);
CREATE TABLE IF NOT EXISTS clean_relearning_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 dataset_version VARCHAR,benchmark_hash VARCHAR,experiments INTEGER,results INTEGER,
 shadow_candidates INTEGER,probability_approved INTEGER,runtime_seconds DOUBLE,
 production_changes INTEGER,details_json JSON
);
CREATE TABLE IF NOT EXISTS clean_relearning_benchmarks(
 benchmark_hash VARCHAR PRIMARY KEY,frozen_at TIMESTAMP,source_commits VARCHAR,
 source_tables_json JSON,summary_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS clean_relearning_results(
 run_id VARCHAR,experiment VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,
 rows BIGINT,effective_n DOUBLE,folds INTEGER,baseline_balanced_accuracy DOUBLE,
 balanced_accuracy DOUBLE,sign_accuracy DOUBLE,roc_auc DOUBLE,brier DOUBLE,
 return_mae DOUBLE,return_rmse DOUBLE,rank_ic DOUBLE,spearman DOUBLE,
 improvement DOUBLE,ci_low DOUBLE,ci_high DOUBLE,fold_wins INTEGER,
 status VARCHAR,probability_allowed BOOLEAN,details_json JSON,
 PRIMARY KEY(run_id,experiment,secid,horizon,model)
);
CREATE TABLE IF NOT EXISTS clean_relearning_impact(
 run_id VARCHAR,impact_family VARCHAR,before_value DOUBLE,after_value DOUBLE,
 difference DOUBLE,status VARCHAR,evidence_json JSON,
 PRIMARY KEY(run_id,impact_family)
);
CREATE TABLE IF NOT EXISTS quality_promotion_queue(
 run_id VARCHAR,secid VARCHAR,current_tier VARCHAR,target_tier VARCHAR,priority INTEGER,
 observations BIGINT,history_years DOUBLE,unresolved_episodes INTEGER,
 blocking_issues_json JSON,missing_evidence_json JSON,queue_status VARCHAR,
 created_at TIMESTAMP,PRIMARY KEY(run_id,secid)
);
CREATE TABLE IF NOT EXISTS quality_evidence_attempts(
 attempt_id VARCHAR PRIMARY KEY,run_id VARCHAR,secid VARCHAR,source VARCHAR,endpoint VARCHAR,
 retrieved_at TIMESTAMP,http_status INTEGER,document_hash VARCHAR,evidence_kind VARCHAR,
 validation_status VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS quality_expansion_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 tier_a_before INTEGER,tier_b_before INTEGER,tier_a_after INTEGER,tier_b_after INTEGER,
 candidates INTEGER,requests INTEGER,validated_resolutions INTEGER,unresolved_after INTEGER,
 panel_version VARCHAR,stop_reason VARCHAR,production_changes INTEGER,details_json JSON
);
CREATE TABLE IF NOT EXISTS issuer_pit_fundamental_states(
 trade_date DATE,issuer_group VARCHAR,secid VARCHAR,metric VARCHAR,period_end DATE,
 publication_date DATE,available_from TIMESTAMP,value DOUBLE,unit VARCHAR,source VARCHAR,
 validation_status VARCHAR,PRIMARY KEY(trade_date,issuer_group,secid,metric)
);
CREATE TABLE IF NOT EXISTS issuer_derived_fundamental_features(
 trade_date DATE,issuer_group VARCHAR,periods_available INTEGER,growth_score DOUBLE,
 margin_trend DOUBLE,fcf_trend DOUBLE,debt_trend DOUBLE,roe_trend DOUBLE,payout_trend DOUBLE,
 valuation_history_score DOUBLE,quality_status VARCHAR,PRIMARY KEY(trade_date,issuer_group)
);
CREATE TABLE IF NOT EXISTS issuer_sector_context_daily(
 trade_date DATE,issuer_group VARCHAR,secid VARCHAR,sector_series VARCHAR,sector_value DOUBLE,
 sector_return_20 DOUBLE,sector_return_60 DOUBLE,sector_volatility_60 DOUBLE,
 sector_drawdown DOUBLE,relative_strength_20 DOUBLE,relative_strength_60 DOUBLE,
 source VARCHAR,pit_status VARCHAR,PRIMARY KEY(trade_date,issuer_group,secid)
);
CREATE TABLE IF NOT EXISTS issuer_context_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 issuers INTEGER,fundamental_state_rows BIGINT,derived_rows BIGINT,sector_rows BIGINT,
 issuers_five_periods INTEGER,requests INTEGER,runtime_seconds DOUBLE,
 production_changes INTEGER,details_json JSON
);
"""
