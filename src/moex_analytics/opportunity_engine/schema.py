"""Stage 56 two-dimensional opportunity persistence."""

DDL = """
CREATE TABLE IF NOT EXISTS opportunity_research_runs(
 run_id VARCHAR PRIMARY KEY,distribution_run_id VARCHAR,ranking_run_id VARCHAR,
 scenario_run_id VARCHAR,timing_run_id VARCHAR,cutoff DATE,started_at TIMESTAMP,
 finished_at TIMESTAMP,status VARCHAR,candidate_rows BIGINT,details_json JSON,immutable BOOLEAN
);
CREATE TABLE IF NOT EXISTS opportunity_candidates(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,candidate_type VARCHAR,
 expected_median DOUBLE,upper_quartile DOUBLE,lower_quartile DOUBLE,tail_downside DOUBLE,
 relative_rank DOUBLE,rank_low DOUBLE,rank_high DOUBLE,timing_status VARCHAR,
 timing_evidence VARCHAR,scenario_applicability VARCHAR,fundamental_confidence VARCHAR,
 valuation_status VARCHAR,portfolio_weight DOUBLE,risk_contribution DOUBLE,
 diversification_status VARCHAR,opportunity_axis DOUBLE,downside_axis DOUBLE,
 quadrant VARCHAR,evidence_quality VARCHAR,evidence_opacity DOUBLE,abstain BOOLEAN,
 abstention_reason VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,cutoff,secid,horizon)
);
CREATE TABLE IF NOT EXISTS opportunity_pareto_dominance(
 run_id VARCHAR,cutoff DATE,horizon INTEGER,dominant_secid VARCHAR,dominated_secid VARCHAR,
 expected_return_advantage DOUBLE,downside_advantage DOUBLE,label VARCHAR,
 research_only BOOLEAN,immutable BOOLEAN,
 PRIMARY KEY(run_id,cutoff,horizon,dominant_secid,dominated_secid)
);
"""
