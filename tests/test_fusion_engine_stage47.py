import duckdb
import pandas as pd
import pytest

from moex_analytics.fusion_engine.core import (
    MIN_WEIGHT_HISTORY,
    abstention_reason,
    adaptive_predictions,
    build_frozen_policy,
    disagreement_score,
    ensure_schema,
    frozen_holdout_predictions,
    fusion_status,
    performance_weights,
    policy_hash,
)


def test_performance_weights_do_not_need_future_or_holdout() -> None:
    short = pd.DataFrame({"a": [0.1], "b": [0.2], "actual_return": [0.0]})
    assert performance_weights(short, ["a", "b"]) == {"a": 0.5, "b": 0.5}
    history = pd.DataFrame({
        "a": [0.0] * MIN_WEIGHT_HISTORY,
        "b": [1.0] * MIN_WEIGHT_HISTORY,
        "actual_return": [0.0] * MIN_WEIGHT_HISTORY,
    })
    weights = performance_weights(history, ["a", "b"])
    assert weights["a"] > weights["b"]
    assert sum(weights.values()) == pytest.approx(1.0)


def test_disagreement_and_abstention_are_visible() -> None:
    assert disagreement_score([0.1, -0.1, -0.2]) > 0.8
    reason = abstention_reason(3, True, 1.0, True)
    assert reason == "weak_analog_sample,regime_novel,strong_model_disagreement,stale_data"
    assert abstention_reason(20, False, 0.1, False) is None


def test_schema_freezes_shadow_and_probability_gate_fields() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE fusion_oos_predictions_v2").fetchall()}
    assert {"information_end", "split", "evaluation_mode", "policy_hash",
            "shadow_only", "probability_allowed"} <= columns
    policy = {row[0] for row in con.execute("DESCRIBE fusion_policy_snapshots").fetchall()}
    assert {"validation_end", "holdout_start", "weights_json", "abstention_threshold",
            "calibration_version", "analog_policy_json", "scaler_version", "pca_version",
            "policy_hash", "immutable"} <= policy
    current = {row[0] for row in con.execute("DESCRIBE current_fusion_research").fetchall()}
    assert {"evidence_json", "abstained", "shadow_only", "probability_allowed"} <= current
    assert fusion_status(con) == {"latest": None}


def _group(rows: int = 200) -> pd.DataFrame:
    dates = pd.bdate_range("2010-01-01", periods=rows)
    values = pd.Series(range(rows), dtype=float) / 10000
    return pd.DataFrame({
        "secid": "SBERP", "horizon": 20, "cutoff": dates, "effective_n": 20,
        "baseline": values, "pooled": values * 0.8, "analog": values * 1.1,
        "actual_return": values * 0.9, "history_end": dates - pd.offsets.BDay(1),
        "regime": (pd.Series(range(rows)) % 2).to_numpy(),
        "event_active": (pd.Series(range(rows)) % 5 == 0).to_numpy(),
    })


def _prediction_at(rows: list[list], cutoff, variant: str) -> float:
    return next(row[6] for row in rows if row[3] == cutoff and row[4] == variant)


def test_holdout_policy_and_hash_are_immutable_to_holdout_outcomes() -> None:
    original = _group()
    payload, boundaries, digest = build_frozen_policy(original)
    predictions = frozen_holdout_predictions(original, payload, boundaries, digest)
    changed = original.copy()
    first_holdout = changed.index[changed.cutoff >= boundaries.holdout_start][0]
    changed.loc[first_holdout, "actual_return"] = 999.0
    payload_after, boundaries_after, digest_after = build_frozen_policy(changed)
    later = changed.loc[first_holdout + 1, "cutoff"]
    changed_predictions = frozen_holdout_predictions(
        changed, payload_after, boundaries_after, digest_after
    )
    assert digest_after == digest
    assert payload_after["weights"] == payload["weights"]
    assert payload_after["abstention_threshold"] == payload["abstention_threshold"]
    assert payload_after["calibration_version"] == payload["calibration_version"]
    assert payload_after["selected_variant"] == payload["selected_variant"]
    assert payload_after["analog_policy"] == payload["analog_policy"]
    assert payload_after["regime_policy"] == payload["regime_policy"]
    assert payload_after["scaler_version"] == payload["scaler_version"]
    assert payload_after["pca_version"] == payload["pca_version"]
    assert _prediction_at(predictions, later, "oos_performance_weighted") == _prediction_at(
        changed_predictions, later, "oos_performance_weighted"
    )
    assert {row[15] for row in predictions} == {digest}


def test_adaptive_pseudo_oos_is_separate_and_can_change() -> None:
    original = _group()
    adaptive = adaptive_predictions(original)
    changed = original.copy()
    first_holdout = int(len(changed) * 0.8)
    changed.loc[first_holdout, "actual_return"] = 999.0
    later = changed.loc[first_holdout + 1, "cutoff"]
    changed_adaptive = adaptive_predictions(changed)
    assert _prediction_at(adaptive, later, "oos_performance_weighted") != _prediction_at(
        changed_adaptive, later, "oos_performance_weighted"
    )
    assert {row[5] for row in adaptive} == {"pseudo_oos_adaptive"}


def test_policy_hash_is_canonical_and_invalid_run_is_excluded() -> None:
    assert policy_hash({"b": 2, "a": 1}) == policy_hash({"a": 1, "b": 2})
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute(
        "INSERT INTO predictive_fusion_runs VALUES "
        "('bad','event',now(),now(),'invalid_temporal_leakage',1,1,'v','{}')"
    )
    assert fusion_status(con) == {"latest": None}
