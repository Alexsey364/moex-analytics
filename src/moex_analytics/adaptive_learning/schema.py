"""Immutable schemas for Stage 22 predictive research."""

DDL = """
CREATE TABLE IF NOT EXISTS adaptive_research_runs(
 run_id VARCHAR PRIMARY KEY,dataset_version VARCHAR,code_version VARCHAR,created_at TIMESTAMP,
 instruments_json JSON,horizons_json JSON,status VARCHAR,runtime_seconds DOUBLE,
 rows_total BIGINT,models_trained INTEGER,folds INTEGER,notes VARCHAR
);
CREATE TABLE IF NOT EXISTS adaptive_feature_registry(
 dataset_version VARCHAR,feature VARCHAR,family VARCHAR,source VARCHAR,
 available_from_rule VARCHAR,version VARCHAR,pit_status VARCHAR,description VARCHAR,
 PRIMARY KEY(dataset_version,feature)
);
CREATE TABLE IF NOT EXISTS adaptive_data_sufficiency(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,rows BIGINT,effective_n DOUBLE,
 years DOUBLE,regimes INTEGER,missingness DOUBLE,features INTEGER,
 feature_observation_ratio DOUBLE,quality_status VARCHAR,corporate_action_flags INTEGER,
 PRIMARY KEY(run_id,secid,horizon)
);
CREATE TABLE IF NOT EXISTS adaptive_targets(
 run_id VARCHAR,trade_date DATE,secid VARCHAR,horizon INTEGER,forward_return DOUBLE,
 direction INTEGER,neutral BOOLEAN,excess_imoex DOUBLE,excess_sector DOUBLE,
 mae DOUBLE,mfe DOUBLE,touch_up_3 BOOLEAN,touch_up_5 BOOLEAN,touch_up_10 BOOLEAN,
 touch_down_3 BOOLEAN,touch_down_5 BOOLEAN,touch_down_10 BOOLEAN,
 relative_rank DOUBLE,PRIMARY KEY(run_id,trade_date,secid,horizon)
);
CREATE TABLE IF NOT EXISTS adaptive_folds(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,scope VARCHAR,fold INTEGER,
 train_from DATE,train_to DATE,validation_from DATE,validation_to DATE,
 test_from DATE,test_to DATE,embargo INTEGER,train_rows INTEGER,
 validation_rows INTEGER,test_rows INTEGER,PRIMARY KEY(run_id,secid,horizon,scope,fold)
);
CREATE TABLE IF NOT EXISTS adaptive_fold_predictions(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,scope VARCHAR,model VARCHAR,fold INTEGER,
 trade_date DATE,actual_direction INTEGER,predicted_direction INTEGER,
 probability DOUBLE,probability_allowed BOOLEAN,actual_return DOUBLE,
 predicted_return DOUBLE,q10 DOUBLE,q25 DOUBLE,q50 DOUBLE,q75 DOUBLE,q90 DOUBLE,
 interval_90_low DOUBLE,interval_90_high DOUBLE,regime VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,scope,model,fold,trade_date)
);
CREATE TABLE IF NOT EXISTS adaptive_model_leaderboard(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,scope VARCHAR,model VARCHAR,
 model_class VARCHAR,observations INTEGER,folds INTEGER,balanced_accuracy DOUBLE,
 roc_auc DOUBLE,brier DOUBLE,log_loss DOUBLE,baseline_balanced_accuracy DOUBLE,
 baseline_brier DOUBLE,delta_balanced_accuracy DOUBLE,return_mae DOUBLE,
 return_rmse DOUBLE,rank_ic DOUBLE,coverage_50 DOUBLE,coverage_80 DOUBLE,
 coverage_90 DOUBLE,calibration_slope DOUBLE,calibration_intercept DOUBLE,ece DOUBLE,
 regime_stability DOUBLE,fold_wins INTEGER,probability_allowed BOOLEAN,
 confidence DOUBLE,status VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,secid,horizon,scope,model)
);
CREATE TABLE IF NOT EXISTS adaptive_feature_importance(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,scope VARCHAR,model VARCHAR,
 feature VARCHAR,family VARCHAR,importance DOUBLE,fold_stability DOUBLE,
 coefficient_sign DOUBLE,decay_horizon INTEGER,status VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,scope,model,feature)
);
CREATE TABLE IF NOT EXISTS adaptive_feature_ablation(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,family VARCHAR,
 full_score DOUBLE,ablated_score DOUBLE,delta DOUBLE,ci_low DOUBLE,ci_high DOUBLE,status VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,family)
);
CREATE TABLE IF NOT EXISTS adaptive_ranking_results(
 run_id VARCHAR,horizon INTEGER,scope VARCHAR,observations INTEGER,rank_ic DOUBLE,
 top_decile_spread DOUBLE,bottom_decile_spread DOUBLE,ndcg DOUBLE,
 best_secids_json JSON,worst_secids_json JSON,status VARCHAR,
 PRIMARY KEY(run_id,horizon,scope)
);
CREATE TABLE IF NOT EXISTS adaptive_model_registry(
 registry_id VARCHAR PRIMARY KEY,run_id VARCHAR,model VARCHAR,model_version VARCHAR,
 scope VARCHAR,secid VARCHAR,horizon INTEGER,features_json JSON,training_end DATE,
 validation_metrics_json JSON,oos_metrics_json JSON,live_metrics_json JSON,
 regimes_json JSON,status VARCHAR,created_at TIMESTAMP,immutable BOOLEAN,
 automatic_promotion BOOLEAN
);
CREATE TABLE IF NOT EXISTS adaptive_promotion_review(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,historical_oos_json JSON,
 pseudo_oos_json JSON,live_n INTEGER,live_metrics_json JSON,calibration_json JSON,
 regime_stability DOUBLE,drift_status VARCHAR,baseline_advantage DOUBLE,
 recommendation VARCHAR,reason VARCHAR,created_at TIMESTAMP,
 PRIMARY KEY(run_id,secid,horizon,model)
);
"""
