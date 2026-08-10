"""Stage 47 persistence schema."""

DDL = """
CREATE TABLE IF NOT EXISTS predictive_fusion_runs(
 run_id VARCHAR PRIMARY KEY,event_run_id VARCHAR,created_at TIMESTAMP,finished_at TIMESTAMP,
 status VARCHAR,oos_rows BIGINT,current_rows BIGINT,methodology_version VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS fusion_evidence_blocks(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,cutoff DATE,block VARCHAR,direction INTEGER,
 strength DOUBLE,confidence DOUBLE,effective_n INTEGER,oos_quality DOUBLE,live_quality DOUBLE,
 value DOUBLE,status VARCHAR,informational_only BOOLEAN,details_json JSON,
 PRIMARY KEY(run_id,secid,horizon,cutoff,block)
);
CREATE TABLE IF NOT EXISTS fusion_oos_predictions(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,cutoff DATE,variant VARCHAR,
 predicted_return DOUBLE,actual_return DOUBLE,absolute_error DOUBLE,direction_correct BOOLEAN,
 disagreement DOUBLE,abstained BOOLEAN,abstention_reason VARCHAR,train_end DATE,
 holdout BOOLEAN,shadow_only BOOLEAN,probability_allowed BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon,cutoff,variant)
);
CREATE TABLE IF NOT EXISTS current_fusion_research(
 run_id VARCHAR,secid VARCHAR,horizon INTEGER,cutoff DATE,signal VARCHAR,
 predicted_return DOUBLE,disagreement VARCHAR,abstained BOOLEAN,abstention_reason VARCHAR,
 evidence_json JSON,status VARCHAR,shadow_only BOOLEAN,probability_allowed BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon)
);
"""
