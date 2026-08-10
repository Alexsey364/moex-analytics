from pathlib import Path

import duckdb

from moex_analytics.research_pipeline.core import (
    STEPS,
    ensure_schema,
    research_status,
)


def test_pipeline_checkpoint_schema_and_order() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert STEPS == ("data", "regimes", "analog_states", "analog_paths", "event_conditioning",
                     "fusion", "validation", "report")
    columns = {row[0] for row in con.execute(
        "DESCRIBE historical_analog_research_checkpoints"
    ).fetchall()}
    assert {"run_id", "step", "status", "result_json", "runtime_seconds"} <= columns
    assert research_status(con) == {"latest": None}


def test_windows_runner_is_thin_and_real() -> None:
    text = Path("RUN_HISTORICAL_ANALOG_RESEARCH.bat").read_text(encoding="utf-8")
    assert "run-historical-analog-research" in text
    assert "dry-run" not in text.lower()
    assert 'cd /d "%~dp0"' in text
    assert '"%PYTHON_EXE%" -m moex_analytics.cli' in text


def test_invalid_runs_are_not_completed_evidence() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE predictive_fusion_runs(run_id VARCHAR,status VARCHAR)")
    con.execute("INSERT INTO predictive_fusion_runs VALUES ('bad','invalid_temporal_leakage')")
    assert con.execute(
        "SELECT count(*) FROM predictive_fusion_runs WHERE status='completed'"
    ).fetchone()[0] == 0
