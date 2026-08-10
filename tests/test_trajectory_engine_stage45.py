from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.trajectory_engine.core import (
    MIN_EFFECTIVE_N,
    ensure_schema,
    normalize_forward_path,
    terminal_statistics,
    trajectory_status,
)


def test_normalized_path_is_actual_and_starts_at_t0() -> None:
    prices = pd.Series([8.0, 10.0, 11.0, 9.0], index=pd.date_range("2020-01-01", periods=4))
    result = normalize_forward_path(prices, 1, 2)
    assert result.tolist() == pytest.approx([100.0, 110.0, 90.0])
    assert result.index.tolist() == prices.index[1:4].tolist()


def test_normalized_path_never_invents_missing_future() -> None:
    prices = pd.Series([10.0, 12.0], index=pd.date_range("2020-01-01", periods=2))
    assert len(normalize_forward_path(prices, 1, 250)) == 1
    assert normalize_forward_path(prices, 2).empty


def test_terminal_distribution_and_consensus() -> None:
    index = pd.date_range("2020-01-01", periods=6)
    returns = pd.Series([0.10, 0.12, 0.08, 0.11, 0.09, -0.01], index=index)
    adverse = pd.Series([-0.03] * 6, index=index)
    favorable = pd.Series([0.15] * 6, index=index)
    result = terminal_statistics(returns, adverse, favorable)
    assert result["status"] == "ready"
    assert result["n"] == 6
    assert result["positive"] == 5 / 6
    assert result["consensus"] == "stronger"
    assert result["adverse"] == -0.03


def test_sparse_terminal_distribution_is_explicit() -> None:
    values = pd.Series(np.arange(MIN_EFFECTIVE_N - 1, dtype=float))
    result = terminal_statistics(values, values, values)
    assert result == {"status": "insufficient_data", "reason": "fewer than five matured episodes"}


def test_schema_and_empty_status() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    expected = {
        "analog_trajectory_runs",
        "analog_forward_trajectories",
        "analog_terminal_distributions",
        "analog_oos_replays",
    }
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    assert expected <= tables
    assert trajectory_status(con) == {"latest": None}


def test_replay_schema_encodes_train_only_history_boundary() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE analog_oos_replays").fetchall()}
    assert {"cutoff", "history_end", "train_only", "actual_return"} <= columns
