from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.timing_engine import core
from moex_analytics.timing_engine.core import (
    _bootstrap_delta,
    ensure_schema,
    first_signal_entry,
    timing_status,
)


def test_signals_execute_next_session_and_never_future_minimum() -> None:
    issuer = np.array([100, 99, 97, 96, 101, 103], dtype=float)
    market = np.array([100, 100, 102, 103, 103, 104], dtype=float)
    assert first_signal_entry("BUY_NOW", 0, 5, issuer, market) == (0, 1)
    assert first_signal_entry("WAIT_3", 0, 5, issuer, market) == (2, 3)
    assert first_signal_entry("WAIT_10", 0, 5, issuer, market) == (None, None)
    assert first_signal_entry("BUY_AFTER_DIP_2", 0, 5, issuer, market) == (2, 3)
    assert first_signal_entry("BUY_AFTER_MARKET_CONFIRMATION", 0, 5, issuer, market) == (2, 3)
    signal, entry = first_signal_entry("BUY_AFTER_RELATIVE_STRENGTH_CONFIRMATION", 2, 5, issuer, market)
    assert entry is None or entry == signal + 1


def test_no_signal_means_no_entry() -> None:
    flat = np.ones(12) * 100
    assert first_signal_entry("BUY_AFTER_DIP_3", 0, 10, flat, flat) == (None, None)


def test_bootstrap_is_deterministic() -> None:
    values = np.linspace(-0.1, 0.2, 100)
    assert _bootstrap_delta(values) == _bootstrap_delta(values)


def test_schema_records_no_hindsight_and_broker_order_flag() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert timing_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE current_timing_intelligence").fetchall()}
    assert {"broker_order", "evidence", "holdout_ci_low", "immutable"} <= columns


def test_buy_now_is_identity_on_issuer_session_index() -> None:
    issuer = np.array([100, 101, 102], dtype=float)
    market = np.array([100, 100, 101], dtype=float)
    signal, entry = first_signal_entry("BUY_NOW", 0, 2, issuer, market)
    assert (signal, entry) == (0, 1)


def test_full_timing_run_freezes_validation_policy_and_buy_now_baseline(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
        "total_return_index DOUBLE,calculation_version VARCHAR)"
    )
    con.execute(
        "CREATE TABLE regime_timeline_v2(run_id VARCHAR,trade_date DATE,regime INTEGER,selected BOOLEAN)"
    )
    con.execute(
        "CREATE TABLE ranking_research_runs(run_id VARCHAR,target_run_id VARCHAR,"
        "cutoff DATE,train_end DATE,validation_end DATE,holdout_start DATE,status VARCHAR,"
        "finished_at TIMESTAMP)"
    )
    dates = pd.bdate_range("2018-01-01", periods=360)
    for number, secid in enumerate(("AAA", "BBB", "IMOEX")):
        values = 100 * np.cumprod(1 + 0.0003 * (number + 1) + np.sin(np.arange(360) / 8) * 0.003)
        con.executemany(
            "INSERT INTO daily_returns VALUES (?,?,?,'actual-dividends-v1')",
            [(date, secid, float(value)) for date, value in zip(dates, values, strict=True)],
        )
    con.executemany(
        "INSERT INTO regime_timeline_v2 VALUES ('regime',?,?,true)",
        [(date, idx % 2) for idx, date in enumerate(dates)],
    )
    con.execute(
        "INSERT INTO ranking_research_runs VALUES ('rank','target',?,?,?,?, 'completed',current_timestamp)",
        [dates[-1], dates[199], dates[279], dates[280]],
    )
    monkeypatch.setattr(core, "PORTFOLIO", ("AAA", "BBB"))
    monkeypatch.setattr(core, "HORIZONS", (5, 20))
    result = core.run_timing_research(con)
    assert result["status"] == "completed"
    assert result["outcomes"] > 0
    buy_now = con.execute(
        "SELECT max(abs(delta_vs_buy_now)) FROM timing_policy_scorecards WHERE policy='BUY_NOW'"
    ).fetchone()[0]
    assert buy_now == pytest.approx(0.0)
    assert (
        con.execute(
            "SELECT bool_and(selection_sample='validation_only') FROM timing_policy_selections"
        ).fetchone()[0]
        is True
    )
    assert (
        con.execute("SELECT bool_and(NOT broker_order) FROM current_timing_intelligence").fetchone()[0]
        is True
    )
    assert core.run_timing_research(con)["cached"] is True
