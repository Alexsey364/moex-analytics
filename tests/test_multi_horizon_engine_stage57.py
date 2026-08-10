from __future__ import annotations

import duckdb

from moex_analytics.multi_horizon_engine.core import (
    ensure_schema,
    expert_for_horizon,
    interpretation,
    multi_horizon_status,
)


def test_horizon_gate_is_deterministic_and_not_outcome_based() -> None:
    assert expert_for_horizon(5) == "short_horizon_expert"
    assert expert_for_horizon(20) == "short_horizon_expert"
    assert expert_for_horizon(60) == "medium_horizon_expert"
    assert expert_for_horizon(120) == "long_horizon_expert"
    assert expert_for_horizon(250) == "long_horizon_expert"


def test_cross_horizon_difference_is_not_called_contradiction() -> None:
    assert interpretation(-.02, .10) == "long_term_interesting_short_term_timing_weak"
    assert interpretation(.02, -.10) == "short_term_strength_long_term_risk"
    assert interpretation(None, .10) == "insufficient_data"


def test_schema_preserves_ablation_and_term_structure() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert multi_horizon_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE horizon_feature_ablation").fetchall()}
    assert {"validation_contribution", "holdout_contribution", "gate_status", "immutable"} <= columns
