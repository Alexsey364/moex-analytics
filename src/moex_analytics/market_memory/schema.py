"""DuckDB schema for immutable historical analog research."""

DDL = """
CREATE TABLE IF NOT EXISTS market_memory_runs(
  run_id VARCHAR PRIMARY KEY, dataset_id VARCHAR, created_at TIMESTAMP, status VARCHAR,
  instruments JSON, horizons JSON, runtime_seconds DOUBLE, analogs INTEGER, note VARCHAR
);
CREATE TABLE IF NOT EXISTS market_analog_episodes(
  run_id VARCHAR, instrument VARCHAR, horizon INTEGER, method VARCHAR,
  cutoff_date DATE, analog_date DATE, distance DOUBLE, similarity VARCHAR,
  forward_return DOUBLE, max_drawdown DOUBLE, mfe DOUBLE, episode_rank INTEGER,
  independent BOOLEAN, immutable BOOLEAN, created_at TIMESTAMP,
  PRIMARY KEY(run_id,instrument,horizon,method,cutoff_date,analog_date)
);
CREATE TABLE IF NOT EXISTS market_analog_scorecards(
  run_id VARCHAR, instrument VARCHAR, horizon INTEGER, method VARCHAR,
  cutoff_date DATE, sample INTEGER, similarity VARCHAR, median_return DOUBLE,
  q10 DOUBLE, q25 DOUBLE, q75 DOUBLE, q90 DOUBLE, positive_fraction DOUBLE,
  median_drawdown DOUBLE, median_mfe DOUBLE, oos_mae DOUBLE, baseline_mae DOUBLE,
  oos_value_add DOUBLE, status VARCHAR, reason VARCHAR,
  PRIMARY KEY(run_id,instrument,horizon,method)
);
"""
