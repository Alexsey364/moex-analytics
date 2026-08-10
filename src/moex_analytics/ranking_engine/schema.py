"""Stage 52 immutable ranking research schema."""

DDL = """
CREATE TABLE IF NOT EXISTS ranking_research_runs(
 run_id VARCHAR PRIMARY KEY,target_run_id VARCHAR,dataset_version VARCHAR,cutoff DATE,
 train_end DATE,validation_end DATE,holdout_start DATE,started_at TIMESTAMP,
 finished_at TIMESTAMP,status VARCHAR,panel_rows BIGINT,prediction_rows BIGINT,
 details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS ranking_model_policies(
 run_id VARCHAR,horizon INTEGER,model VARCHAR,validation_rank_ic DOUBLE,
 validation_ndcg DOUBLE,validation_top_quintile_spread DOUBLE,folds INTEGER,
 policy_hash VARCHAR,selected BOOLEAN,selection_sample VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,horizon,model)
);
CREATE TABLE IF NOT EXISTS ranking_oos_predictions(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,horizon INTEGER,model VARCHAR,
 predicted_score DOUBLE,predicted_rank DOUBLE,actual_rank DOUBLE,actual_return DOUBLE,
 imoex_return DOUBLE,sample_type VARCHAR,policy_hash VARCHAR,history_end DATE,
 immutable BOOLEAN,PRIMARY KEY(run_id,trade_date,secid,horizon,model,sample_type)
);
CREATE TABLE IF NOT EXISTS ranking_scorecards(
 run_id VARCHAR,horizon INTEGER,model VARCHAR,sample_type VARCHAR,observations BIGINT,
 dates INTEGER,rank_ic DOUBLE,spearman DOUBLE,ndcg DOUBLE,top_decile_spread DOUBLE,
 top_quintile_spread DOUBLE,bottom_decile_spread DOUBLE,top3_excess DOUBLE,
 top5_excess DOUBLE,top10_excess DOUBLE,top_k_hit_rate DOUBLE,ci_low DOUBLE,ci_high DOUBLE,
 status VARCHAR,PRIMARY KEY(run_id,horizon,model,sample_type)
);
CREATE TABLE IF NOT EXISTS ranking_topk_backtests(
 run_id VARCHAR,horizon INTEGER,model VARCHAR,k INTEGER,sample_type VARCHAR,
 periods INTEGER,mean_return DOUBLE,mean_excess_imoex DOUBLE,equal_eligible_return DOUBLE,
 turnover DOUBLE,commission_bps DOUBLE,execution_lag INTEGER,status VARCHAR,
 PRIMARY KEY(run_id,horizon,model,k,sample_type)
);
CREATE TABLE IF NOT EXISTS current_portfolio_ranking(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,relative_rank DOUBLE,
 rank_low DOUBLE,rank_high DOUBLE,tie_group INTEGER,model_agreement DOUBLE,
 historical_oos DOUBLE,live_evidence VARCHAR,status VARCHAR,reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,cutoff,secid,horizon)
);
"""
