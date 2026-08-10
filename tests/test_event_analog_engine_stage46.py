from datetime import UTC, datetime

import duckdb
import pandas as pd
import pytest

from moex_analytics.event_analog_engine import core
from moex_analytics.event_analog_engine.core import (
    ensure_schema,
    event_analog_status,
    event_is_available,
    summarize_subset,
)


def test_surprise_event_not_available_before_publication() -> None:
    event = {"available_from": datetime(2022, 2, 24, 8, tzinfo=UTC)}
    assert not event_is_available(event, pd.Timestamp("2022-02-24 07:59", tz="UTC"))
    assert event_is_available(event, pd.Timestamp("2022-02-24 08:00", tz="UTC"))
    assert not event_is_available({"available_from": None}, pd.Timestamp("2022-02-24"))


def test_event_subset_requires_effective_sample() -> None:
    result = summarize_subset(pd.Series([0.1, -0.1, 0.2]))
    assert result["status"] == "insufficient_data"
    assert "five" in result["reason"]


def test_event_summary_is_descriptive() -> None:
    result = summarize_subset(pd.Series([0.1, 0.2, -0.1, 0.3, 0.05]))
    assert result["status"] == "ready"
    assert result["positive"] == 0.8
    assert "not causal" in result["reason"]


def test_schema_and_empty_status() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    assert {"event_analog_runs", "analog_event_profiles", "current_event_contexts",
            "event_conditioned_distributions"} <= tables
    assert event_analog_status(con) == {"latest": None}


def test_profile_schema_preserves_pit_fields() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE analog_event_profiles").fetchall()}
    assert {"surprise_event", "available_from", "pit_safe", "event_state"} <= columns


def test_naive_cutoff_is_normalized_to_event_timezone() -> None:
    event = {"available_from": datetime(2022, 2, 24, 8, tzinfo=UTC)}
    assert event_is_available(event, pd.Timestamp("2022-02-24 08:00"))


def test_run_requires_completed_trajectory_source() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE analog_trajectory_runs("
        "run_id VARCHAR,analog_run_id VARCHAR,cutoff DATE,status VARCHAR,finished_at TIMESTAMP)"
    )
    with pytest.raises(ValueError, match="Stage 45"):
        core.run_event_conditioning(con)


def test_failed_run_is_auditable(monkeypatch) -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE analog_trajectory_runs("
        "run_id VARCHAR,analog_run_id VARCHAR,cutoff DATE,status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO analog_trajectory_runs VALUES "
        "('trajectory','analog',DATE '2024-01-01','completed',now())"
    )
    monkeypatch.setattr(core, "_write_profiles", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        core.run_event_conditioning(con)
    status, details = con.execute("SELECT status,details_json FROM event_analog_runs").fetchone()
    assert status == "failed"
    assert "RuntimeError" in details
