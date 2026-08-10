import duckdb
import numpy as np
import pandas as pd

from moex_analytics.validation_engine.core import (
    block_bootstrap_delta,
    ensure_schema,
    evidence_status,
    validation_metrics,
    validation_status,
)
from moex_analytics.validation_engine.strict_analogs import (
    analog_outcomes,
    feature_frame,
    fit_transform_policy,
    prediction_record,
)


def test_validation_metrics_are_oos_observation_metrics() -> None:
    frame = pd.DataFrame({
        "predicted_return": [0.1, -0.1, 0.2, -0.2],
        "actual_return": [0.2, -0.2, -0.1, 0.1],
        "abstained": [False, False, True, True],
    })
    metrics = validation_metrics(frame)
    assert metrics["observations"] == 4
    assert metrics["sign_accuracy"] == 0.5
    assert metrics["abstention_rate"] == 0.5


def test_block_bootstrap_respects_overlapping_horizon_blocks() -> None:
    model = np.full(200, 0.1)
    baseline = np.full(200, 0.2)
    estimate, low, high = block_bootstrap_delta(model, baseline, block_length=20, iterations=100)
    assert estimate > 0
    assert low > 0
    assert high > 0


def test_evidence_status_does_not_promote_non_significant_result() -> None:
    assert evidence_status("existing_plus_analog", 0.01, -0.01, True) == "WEAK_EVIDENCE"
    assert evidence_status("existing_plus_analog", -0.01, -0.02, True) == "NO_EVIDENCE"
    assert evidence_status("existing_plus_analog", 0.01, 0.001, True) == "ANALOG_USEFUL"
    assert evidence_status("oos_performance_weighted", 0.01, 0.001, True) == "SHADOW_CANDIDATE"


def test_schema_records_untouched_holdout_contract() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE analog_method_validation_status").fetchall()}
    assert "holdout_touched_for_selection" in columns
    selection = {row[0] for row in con.execute("DESCRIBE analog_method_selection_v2").fetchall()}
    assert {"selected_k", "validation_end", "scaler_hash", "regime_model_hash",
            "similarity_hash", "policy_hash", "holdout_touched_for_selection"} <= selection
    assert validation_status(con) == {"latest": None}


def test_frozen_analog_library_ignores_earlier_holdout_outcome() -> None:
    dates = pd.bdate_range("2010-01-01", periods=500)
    prices = pd.Series(100 * np.exp(np.cumsum(np.sin(np.arange(500) / 20) / 100)), index=dates)
    features = feature_frame(prices)
    validation_end = dates[349]
    policy = fit_transform_policy(features, validation_end)
    cutoff = dates[420]
    before = analog_outcomes(
        prices, features, cutoff, 20, 20, "path_only", policy, validation_end, set()
    )
    changed = prices.copy()
    changed.iloc[380] *= 10
    after = analog_outcomes(
        changed, feature_frame(changed), cutoff, 20, 20, "path_only",
        policy, validation_end, set(),
    )
    assert prediction_record(before) == prediction_record(after)


def test_holdout_method_policy_hash_and_library_end_are_persisted() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE analog_strict_predictions_v2").fetchall()}
    assert {"policy_hash", "library_end", "split", "probability_allowed"} <= columns
