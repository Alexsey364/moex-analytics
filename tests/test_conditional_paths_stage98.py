import duckdb
import numpy as np

from moex_analytics.conditional_paths.core import _risk_row, path_statistics
from moex_analytics.conditional_paths.schema import ensure_schema


def test_path_projection_risk_trough_peak_and_recovery():
    path = np.array([0.0, -0.05, -0.10, -0.02, 0.01, 0.08])
    result = path_statistics(path)
    assert result["mae"] == -0.10
    assert result["mfe"] == 0.08
    assert result["time_to_trough"] == 2
    assert result["time_to_peak"] == 5
    assert result["recovered"] and result["recovery_time"] == 4
    assert result["new_high_after_recovery"]
    assert result["fall_first_end_positive"]


def test_drawdown_frequencies_are_weighted_and_evidence_gated():
    up = np.r_[0.0, np.linspace(0.01, 0.20, 20)]
    down = np.r_[0.0, np.linspace(-0.01, -0.20, 20)]
    row = _risk_row("run", "TEST", 20, [up, down], np.array([0.75, 0.25]), 3.0)
    assert row[4] == 2
    assert np.isclose(row[17], 0.25)  # drawdown >=5%
    assert np.isclose(row[18], 0.25)  # drawdown >=10%
    assert row[21] == "insufficient_evidence"


def test_empty_path_and_schema_keep_stress_separate():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {
        row[1] for row in con.execute("PRAGMA table_info('conditional_path_curves')").fetchall()
    }
    assert {"expected_low", "plausible_low", "stress_low", "stress_high"} <= columns
