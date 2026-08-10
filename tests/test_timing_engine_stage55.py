from __future__ import annotations

import duckdb
import numpy as np

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
    signal, entry = first_signal_entry(
        "BUY_AFTER_RELATIVE_STRENGTH_CONFIRMATION", 2, 5, issuer, market
    )
    assert entry is None or entry == signal + 1


def test_no_signal_means_no_entry() -> None:
    flat = np.ones(12) * 100
    assert first_signal_entry("BUY_AFTER_DIP_3", 0, 10, flat, flat) == (None, None)


def test_bootstrap_is_deterministic() -> None:
    values = np.linspace(-.1, .2, 100)
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
