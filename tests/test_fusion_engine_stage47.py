import duckdb
import pandas as pd
import pytest

from moex_analytics.fusion_engine.core import (
    MIN_WEIGHT_HISTORY,
    abstention_reason,
    disagreement_score,
    ensure_schema,
    fusion_status,
    performance_weights,
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
    columns = {row[0] for row in con.execute("DESCRIBE fusion_oos_predictions").fetchall()}
    assert {"train_end", "holdout", "shadow_only", "probability_allowed"} <= columns
    current = {row[0] for row in con.execute("DESCRIBE current_fusion_research").fetchall()}
    assert {"evidence_json", "abstained", "shadow_only", "probability_allowed"} <= current
    assert fusion_status(con) == {"latest": None}
