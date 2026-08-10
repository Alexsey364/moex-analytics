from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.scenario_engine.core import (
    classify_scenario,
    ensure_schema,
    medoid_date,
    scenario_status,
)


def test_fixed_scenario_rules() -> None:
    assert classify_scenario(np.array([-.06, -.02, .03])) == "dip_then_recover"
    assert classify_scenario(np.array([.01, .02, .04])) == "growth_without_deep_drawdown"
    assert classify_scenario(np.array([-.01, -.02, -.04])) == "continued_decline"
    assert classify_scenario(np.array([.01, -.01, .005])) == "sideways"
    assert classify_scenario(np.array([.08, -.08, -.04])) == "volatile_mixed"


def test_medoid_is_an_actual_historical_episode() -> None:
    paths = {pd.Timestamp("2020-01-01"): np.array([0, .01, .02]),
             pd.Timestamp("2021-01-01"): np.array([0, .011, .021]),
             pd.Timestamp("2022-01-01"): np.array([0, -.1, .2])}
    selected = medoid_date(paths)
    assert selected in paths
    assert selected != pd.Timestamp("2022-01-01")
    assert medoid_date({}) is None


def test_schema_labels_frequency_and_paths_honestly() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert scenario_status(con) == {"latest": None}
    summary = {row[0] for row in con.execute("DESCRIBE scenario_tree_summaries").fetchall()}
    paths = {row[0] for row in con.execute("DESCRIBE scenario_representative_paths").fetchall()}
    assert {"historical_frequency", "applicability", "reason", "immutable"} <= summary
    assert {"medoid_analog_date", "actual_historical_path", "immutable"} <= paths
