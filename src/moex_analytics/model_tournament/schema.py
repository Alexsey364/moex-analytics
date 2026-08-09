"""Stage 23 immutable tournament schema."""

DDL = """
CREATE TABLE IF NOT EXISTS tournament_runs(
 run_id VARCHAR PRIMARY KEY,dataset_version VARCHAR,created_at TIMESTAMP,status VARCHAR,
 instruments_json JSON,horizons_json JSON,neutral_policy VARCHAR,holdout_fraction DOUBLE,
 runtime_seconds DOUBLE,models_tested INTEGER,folds INTEGER,notes VARCHAR
);
CREATE TABLE IF NOT EXISTS tournament_folds(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,fold INTEGER,train_from DATE,train_to DATE,
 validation_from DATE,validation_to DATE,test_from DATE,test_to DATE,embargo INTEGER,
 train_n INTEGER,validation_n INTEGER,test_n INTEGER,PRIMARY KEY(run_id,secid,horizon,fold)
);
CREATE TABLE IF NOT EXISTS tournament_results(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,family VARCHAR,split VARCHAR,
 n INTEGER,effective_n DOUBLE,balanced_accuracy DOUBLE,roc_auc DOUBLE,brier DOUBLE,
 log_loss DOUBLE,mae DOUBLE,rmse DOUBLE,rank_ic DOUBLE,spearman DOUBLE,ece DOUBLE,
 coverage_50 DOUBLE,coverage_80 DOUBLE,coverage_90 DOUBLE,baseline_model VARCHAR,
 baseline_score DOUBLE,advantage DOUBLE,ci_low DOUBLE,ci_high DOUBLE,fold_wins INTEGER,
 regime_stability DOUBLE,p_value DOUBLE,fdr_q DOUBLE,permutation_pass BOOLEAN,
 noise_pass BOOLEAN,probability_allowed BOOLEAN,status VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,secid,horizon,model,split)
);
CREATE TABLE IF NOT EXISTS tournament_predictions(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,split VARCHAR,fold INTEGER,
 trade_date DATE,actual_direction INTEGER,predicted_direction INTEGER,probability DOUBLE,
 actual_return DOUBLE,predicted_return DOUBLE,regime VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,model,split,fold,trade_date)
);
CREATE TABLE IF NOT EXISTS tournament_leaderboard(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,baseline VARCHAR,best_linear VARCHAR,
 best_tree VARCHAR,best_regime VARCHAR,best_pooled VARCHAR,best_ranking VARCHAR,
 best_ensemble VARCHAR,winner VARCHAR,status VARCHAR,reason VARCHAR,
 PRIMARY KEY(run_id,secid,horizon)
);
"""
