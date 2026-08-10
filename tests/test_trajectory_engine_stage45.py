from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.trajectory_engine import core
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


def test_full_trajectory_run_uses_real_prices_and_train_only_replays(monkeypatch) -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute(
        "CREATE TABLE analog_search_runs_v3(run_id VARCHAR,cutoff DATE,status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE historical_analogs_v3(run_id VARCHAR,analog_type VARCHAR,"
        "secid VARCHAR,method VARCHAR,path_window INTEGER,analog_date DATE,"
        "similarity_score DOUBLE,data_quality DOUBLE)"
    )
    dates = pd.bdate_range("2015-01-01", periods=1100)
    prices = 100 * np.cumprod(1 + 0.0004 + np.sin(np.arange(1100) / 13) * 0.003)
    con.executemany(
        "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
        [(date, "AAA", float(price)) for date, price in zip(dates, prices, strict=True)],
    )
    con.execute(
        "INSERT INTO analog_search_runs_v3 VALUES ('analog','2026-08-07','completed',current_timestamp)"
    )
    for analog_date in dates[100:106]:
        con.execute(
            "INSERT INTO historical_analogs_v3 VALUES ('analog','issuer','AAA','robust',20,?,.8,.9)",
            [analog_date],
        )
    monkeypatch.setattr(core, "INSTRUMENTS", ("AAA",))
    monkeypatch.setattr(core, "HORIZONS", (5, 20))
    result = core.run_trajectory_forecasting(con)
    assert result["status"] == "completed"
    assert result["trajectories"] > 0
    assert result["distributions"] == 2
    assert result["replays"] > 0
    assert (
        con.execute(
            "SELECT bool_and(source_trade_date>analog_date) FROM analog_forward_trajectories"
        ).fetchone()[0]
        is True
    )
    assert (
        con.execute("SELECT bool_and(train_only AND history_end<cutoff) FROM analog_oos_replays").fetchone()[
            0
        ]
        is True
    )
    assert (
        con.execute("SELECT count(*) FROM analog_terminal_distributions WHERE status='ready'").fetchone()[0]
        == 2
    )
