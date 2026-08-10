import duckdb
import numpy as np
import pandas as pd

from moex_analytics.analog_engine.core import (
    MAHALANOBIS_ROWS_PER_FEATURE,
    MIN_COVERAGE,
    MIN_DTW_PREHISTORY_MULTIPLIER,
    MIN_MAHALANOBIS_ROWS,
    MIN_PCA_ROWS,
    context_policy,
    dtw_distance,
    ensure_schema,
    filter_eligible_dates,
    independent_nearest,
    method_distances,
    path_distances,
    robust_scale,
    state_distances,
)


def test_state_transforms_are_fit_only_on_history():
    history = pd.DataFrame(
        {"a": np.linspace(0, 10, 100), "b": np.sin(np.linspace(0, 8, 100))},
        index=pd.date_range("2020-01-01", periods=100),
    )
    current = pd.Series({"a": 25, "b": 1.2})
    train, _point = robust_scale(history, current)
    changed = current.copy()
    changed["a"] = 1_000_000
    train_again, _ = robust_scale(history, changed)
    np.testing.assert_allclose(train, train_again)
    for method in ("robust_euclidean", "mahalanobis", "cosine", "pca"):
        assert len(state_distances(history, current, method)) == 100


def test_path_matching_never_reads_after_cutoff():
    series = pd.Series(np.arange(1, 101, dtype=float), index=pd.date_range("2020-01-01", periods=100))
    first = path_distances(series, 79, 20, "cosine")
    series.iloc[80:] = 1_000_000
    second = path_distances(series, 79, 20, "cosine")
    pd.testing.assert_series_equal(first, second)


def test_dtw_and_episode_independence():
    assert dtw_distance(np.array([1, 2, 3]), np.array([1, 2, 3])) == 0
    dates = pd.date_range("2020-01-01", periods=100)
    selected = independent_nearest(pd.Series(np.arange(100), index=dates), separation=20, limit=5)
    positions = [dates.get_loc(date) for date in selected.index]
    assert all(abs(left - right) >= 20 for i, left in enumerate(positions) for right in positions[i + 1 :])


def test_context_survives_optional_missing_and_empty_is_explicit():
    frame = pd.DataFrame({"ret_20": np.arange(800), "volatility_20": 1.0, "optional": np.nan})
    ready = context_policy(frame, ["ret_20", "volatility_20"], ["optional"])
    assert ready["status"] == "ready"
    assert "optional" not in ready["features"]
    empty = context_policy(pd.DataFrame(), ["ret_20"], [])
    assert empty["status"] == "insufficient_context"


def test_required_missing_and_sparse_current_are_not_neutral_signals():
    missing = context_policy(pd.DataFrame({"optional": range(800)}), ["ret_20"], ["optional"])
    assert missing["status"] == "insufficient_context"
    frame = pd.DataFrame({"ret_20": np.arange(800, dtype=float), "volatility_20": 1.0})
    frame.loc[799, ["ret_20", "volatility_20"]] = np.nan
    sparse = context_policy(frame, ["ret_20", "volatility_20"], [])
    assert sparse["status"] == "insufficient_feature_coverage"


def test_limited_independent_episodes_are_not_duplicated():
    dates = pd.date_range("2020-01-01", periods=50)
    selected = independent_nearest(pd.Series(np.arange(50), index=dates), separation=20, limit=20)
    assert len(selected) == 3


def test_singular_covariance_and_small_pca_are_safe():
    dates = pd.date_range("2020-01-01", periods=100)
    singular = pd.DataFrame({"a": np.ones(100), "b": np.ones(100)}, index=dates)
    distances, status, _condition, reason = method_distances(singular, singular.iloc[-1], "mahalanobis")
    assert distances.empty
    assert status == "numerical_failure"
    assert "covariance" in reason
    euclidean, euclidean_status, _, _ = method_distances(singular, singular.iloc[-1], "robust_euclidean")
    assert euclidean_status == "ready"
    assert np.isfinite(euclidean).all()
    tiny = singular.iloc[:2]
    pca, pca_status, _, _ = method_distances(tiny, tiny.iloc[-1], "pca")
    assert pca.empty
    assert pca_status == "method_unavailable"


def test_short_dtw_and_empty_filters_return_empty_not_fake_analogs():
    short = pd.Series(range(10), index=pd.date_range("2020-01-01", periods=10))
    assert path_distances(short, 9, 20, "dtw").empty
    assert independent_nearest(pd.Series(dtype=float)).empty
    assert independent_nearest(pd.Series([np.nan])).empty


def test_schema_migration_and_partial_run_cleanup_contract():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    context_columns = {row[1] for row in con.execute("PRAGMA table_info('analog_contexts_v3')").fetchall()}
    diagnostic_columns = {
        row[1] for row in con.execute("PRAGMA table_info('analog_method_diagnostics_v3')").fetchall()
    }
    assert {"status", "reason", "eligible_rows", "required_coverage"} <= context_columns
    assert {"requested_k", "effective_k", "condition_number", "reason"} <= diagnostic_columns


def test_method_eligibility_policy_is_frozen():
    assert MIN_MAHALANOBIS_ROWS == 50
    assert MAHALANOBIS_ROWS_PER_FEATURE == 5
    assert MIN_PCA_ROWS == 20
    assert MIN_DTW_PREHISTORY_MULTIPLIER == 2
    assert MIN_COVERAGE == 0.60
    short = pd.DataFrame({"a": np.arange(19), "b": np.arange(19) ** 2})
    assert method_distances(short, short.iloc[-1], "pca")[1] == "method_unavailable"
    below = pd.DataFrame({"a": np.arange(49), "b": np.sin(np.arange(49))})
    assert method_distances(below, below.iloc[-1], "mahalanobis")[1] == "method_unavailable"
    zero = pd.DataFrame({"a": np.zeros(60), "b": np.zeros(60)})
    assert method_distances(zero, zero.iloc[-1], "cosine")[1] == "method_unavailable"


def test_empty_regime_and_event_filters_are_valid_empty_results():
    frame = pd.DataFrame({"value": [1, 2]}, index=pd.date_range("2020-01-01", periods=2))
    assert filter_eligible_dates(frame, set()).empty
    assert filter_eligible_dates(frame, {pd.Timestamp("1999-01-01")}).empty


def test_failed_run_state_can_be_rebuilt_by_deterministic_run_id():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute(
        "INSERT INTO analog_search_runs_v3 "
        "(run_id,created_at,status,contexts,analogs) VALUES ('same',current_timestamp,'failed',1,2)"
    )
    con.execute(
        "INSERT INTO analog_contexts_v3 "
        "(run_id,analog_type,secid,status) VALUES ('same','market','MARKET','ready')"
    )
    for table in (
        "analog_search_runs_v3",
        "analog_contexts_v3",
        "historical_analogs_v3",
        "analog_method_diagnostics_v3",
    ):
        con.execute(f"DELETE FROM {table} WHERE run_id='same'")
    assert con.execute("SELECT count(*) FROM analog_search_runs_v3").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM analog_contexts_v3").fetchone()[0] == 0
