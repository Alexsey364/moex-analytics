"""Immutable/checkpointed controlled learning schema."""

DDL = """
CREATE TABLE IF NOT EXISTS learning_cycle_runs(
 run_id VARCHAR PRIMARY KEY,dataset_id VARCHAR,created_at TIMESTAMP,finished_at TIMESTAMP,
 status VARCHAR,current_stage VARCHAR,runtime_seconds DOUBLE,resumed BOOLEAN,
 production_changes INTEGER,note VARCHAR
);
CREATE TABLE IF NOT EXISTS learning_cycle_checkpoints(
 run_id VARCHAR,stage INTEGER,stage_name VARCHAR,status VARCHAR,started_at TIMESTAMP,
 finished_at TIMESTAMP,component_run_id VARCHAR,details_json JSON,
 PRIMARY KEY(run_id,stage)
);
CREATE TABLE IF NOT EXISTS controlled_daily_runs(
 run_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,status VARCHAR,new_rows INTEGER,
 new_forecasts INTEGER,matured INTEGER,models_checked INTEGER,feature_scorecards_updated INTEGER,
 degradation_warning BOOLEAN,challenger_progress VARCHAR,retrained BOOLEAN,
 production_changes INTEGER,details_json JSON
);
CREATE TABLE IF NOT EXISTS learning_model_versions(
 version_id VARCHAR PRIMARY KEY,source_run_id VARCHAR,secid VARCHAR,horizon INTEGER,
 model VARCHAR,status VARCHAR,created_at TIMESTAMP,immutable BOOLEAN,production BOOLEAN
);
CREATE TABLE IF NOT EXISTS model_champion_table(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,current_champion VARCHAR,best_challenger VARCHAR,
 historical_oos_advantage DOUBLE,live_n INTEGER,live_score DOUBLE,status VARCHAR,
 probability_allowed BOOLEAN,PRIMARY KEY(run_id,secid,horizon)
);
CREATE TABLE IF NOT EXISTS learning_promotion_review(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,minimum_live_n INTEGER,
 live_n INTEGER,oos_advantage BOOLEAN,live_advantage BOOLEAN,calibration BOOLEAN,
 regime_stability BOOLEAN,no_severe_drift BOOLEAN,abstention_benefit BOOLEAN,
 status VARCHAR,automatic_promotion BOOLEAN,reason VARCHAR,
 PRIMARY KEY(run_id,secid,horizon,model)
);
CREATE TABLE IF NOT EXISTS learning_journal_v2(
 run_id VARCHAR,forecast_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,
 prediction_date DATE,actual_return DOUBLE,direction_correct BOOLEAN,regime VARCHAR,
 meta_confidence DOUBLE,abstention_should_have_happened BOOLEAN,diagnostic VARCHAR,
 causal_claim BOOLEAN,immutable BOOLEAN,PRIMARY KEY(run_id,forecast_id)
);
CREATE TABLE IF NOT EXISTS knowledge_changes(
 run_id VARCHAR,component VARCHAR,change_text VARCHAR,evidence VARCHAR,status VARCHAR,
 PRIMARY KEY(run_id,component,change_text)
);
"""
