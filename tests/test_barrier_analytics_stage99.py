import duckdb
import numpy as np

from moex_analytics.barrier_analytics.core import evaluate_barriers, first_passage
from moex_analytics.barrier_analytics.schema import ensure_schema


def test_first_passage_upper_lower_and_neither():
    assert first_passage([0, 0.02, 0.06, -0.10], 0.05, 0.05, 3) == ("upper", 2)
    assert first_passage([0, -0.02, -0.06, 0.10], 0.05, 0.05, 3) == ("lower", 2)
    assert first_passage([0, 0.01, -0.01], 0.05, 0.05, 2) == ("neither", None)


def test_weighted_barrier_frequencies_sum_to_one_and_support_asymmetry():
    paths = [
        np.array([0, 0.06, 0.11]),
        np.array([0, -0.06, -0.11]),
        np.array([0, 0.01, 0.02]),
    ]
    result = evaluate_barriers(paths, [0.5, 0.3, 0.2], 0.10, 0.05, 2)
    assert np.isclose(result["upper"] + result["lower"] + result["neither"], 1.0)
    assert result["upper"] == 0.5 and result["lower"] == 0.3 and result["neither"] == 0.2
    assert result["time_upper"] == 2 and result["time_lower"] == 1


def test_empty_evidence_and_probability_gate_schema():
    result = evaluate_barriers([], [], 0.05, 0.05, 20)
    assert result["raw_n"] == 0 and result["upper"] is None
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {
        row[1] for row in con.execute("PRAGMA table_info('conditional_barrier_results')").fetchall()
    }
    assert {"evidence_status", "probability_published", "neither_frequency"} <= columns
