import duckdb
import numpy as np

from moex_analytics.conditional_forecast.core import (
    _leave_one_out,
    effective_sample_size,
    weighted_quantile,
)
from moex_analytics.conditional_forecast.schema import ensure_schema


def test_weighted_quantiles_and_effective_sample_size_known_values():
    values = [1.0, 2.0, 10.0]
    weights = [0.2, 0.6, 0.2]
    assert weighted_quantile(values, weights, 0.5) == 2.0
    assert weighted_quantile(values, weights, 0.9) == 10.0
    assert effective_sample_size([0.5, 0.5]) == 2.0
    assert np.isclose(effective_sample_size(weights), 1 / 0.44)


def test_weight_edge_cases_are_safe():
    assert np.isnan(weighted_quantile([], [], 0.5))
    assert np.isnan(weighted_quantile([1], [0], 0.5))
    assert effective_sample_size([0, 0]) == 0


def test_leave_one_episode_out_reports_sensitivity():
    result = _leave_one_out(np.array([-0.1, 0.0, 0.2]), np.array([0.2, 0.3, 0.5]), 100.0)
    assert result[0] <= result[1]
    assert result[2] >= 0
    assert result[3] >= 0
    assert _leave_one_out(np.array([0.1]), np.array([1.0]), 100.0) == (None, None, None, None)


def test_schema_uses_named_weight_and_distribution_fields():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {
        row[1] for row in con.execute("PRAGMA table_info('conditional_forecast_horizons')").fetchall()
    }
    assert {
        "effective_sample_size",
        "max_weight",
        "expected_low",
        "plausible_low",
        "stress_low",
        "evidence_status",
    } <= columns
