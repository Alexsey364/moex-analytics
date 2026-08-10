from __future__ import annotations

import duckdb
import pandas as pd

from moex_analytics.opportunity_engine.core import (
    ensure_schema,
    opportunity_status,
    pareto_pairs,
    quadrant,
)


def test_opportunity_quadrants_are_two_dimensional() -> None:
    assert quadrant(.8, .1, .5, .2) == "high_opportunity_low_downside"
    assert quadrant(.8, .3, .5, .2) == "high_opportunity_high_downside"
    assert quadrant(.2, .1, .5, .2) == "low_opportunity_low_downside"
    assert quadrant(.2, .3, .5, .2) == "low_opportunity_high_downside"


def test_pareto_requires_better_reward_and_no_worse_downside() -> None:
    frame = pd.DataFrame({"secid": ["A", "B", "C"], "expected_median": [.1, .05, .12],
                          "downside_axis": [.1, .2, .3]})
    pairs = {(left, right) for left, right, _, _ in pareto_pairs(frame)}
    assert ("A", "B") in pairs
    assert ("C", "A") not in pairs


def test_schema_has_reserve_abstention_and_no_magic_score() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert opportunity_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE opportunity_candidates").fetchall()}
    assert {"candidate_type", "opportunity_axis", "downside_axis", "abstain",
            "abstention_reason"} <= columns
