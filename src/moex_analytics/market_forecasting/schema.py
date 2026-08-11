"""Stage 72 research persistence."""

DDL = """
CREATE TABLE IF NOT EXISTS market_forecast_runs(
 run_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, state_run_id VARCHAR, date_from DATE, date_to DATE,
 frozen_train_to DATE, frozen_validation_to DATE, holdout_from DATE, observations BIGINT,
 methodology_version VARCHAR, production_unchanged BOOLEAN, probability_gate_unchanged BOOLEAN,
 immutable BOOLEAN, status VARCHAR, details_json JSON
);
CREATE TABLE IF NOT EXISTS market_forecast_scorecards(
 run_id VARCHAR, horizon INTEGER, model VARCHAR, sample VARCHAR, observations BIGINT,
 balanced_accuracy DOUBLE, mcc DOUBLE, brier DOUBLE, return_mae DOUBLE, return_correlation DOUBLE,
 drawdown_mae DOUBLE, volatility_mae DOUBLE, baseline_balanced_accuracy DOUBLE,
 improvement_vs_baseline DOUBLE, status VARCHAR, details_json JSON,
 PRIMARY KEY(run_id,horizon,model,sample)
);
CREATE TABLE IF NOT EXISTS market_forecast_predictions(
 run_id VARCHAR, horizon INTEGER, model VARCHAR, trade_date DATE, sample VARCHAR,
 actual_class INTEGER, predicted_class INTEGER, actual_return DOUBLE, predicted_return DOUBLE,
 actual_drawdown DOUBLE, predicted_drawdown DOUBLE, actual_volatility DOUBLE, predicted_volatility DOUBLE,
 probability_published BOOLEAN, PRIMARY KEY(run_id,horizon,model,trade_date)
);
"""


def ensure_schema(con: object) -> None:
    con.execute(DDL)  # type: ignore[attr-defined]
