import duckdb
import numpy as np
import pandas as pd

from moex_analytics.regime_intelligence.core import (
    MODEL_CANDIDATE_COLUMNS,
    RegimeCandidateRecord,
    _durations,
    _novelty,
    _scale_train,
    _transition_matrix,
    ensure_schema,
    fit_candidate,
    save_candidate,
)


def test_scaling_is_fit_on_train_only():
    train = pd.DataFrame({"a": [0.0, 1.0, 2.0, 3.0], "b": [1.0, 1.5, 2.0, 2.5]})
    full = pd.concat([train, pd.DataFrame({"a": [10_000.0], "b": [-10_000.0]})], ignore_index=True)
    train_x, full_x = _scale_train(train, full)
    train_x_again, _ = _scale_train(train, train)
    np.testing.assert_allclose(train_x, train_x_again)
    assert abs(full_x[-1]).max() == 12


def test_regime_models_return_only_requested_states():
    rng = np.random.default_rng(42)
    train = np.r_[rng.normal(-2, 0.2, (60, 2)), rng.normal(2, 0.2, (60, 2))]
    for algorithm in ("kmeans", "gaussian_mixture", "gaussian_hmm"):
        labels = fit_candidate(train, train, algorithm, 2)
        assert len(labels) == len(train)
        assert set(labels) <= {0, 1}


def test_markov_transition_and_duration_are_temporal_not_forward_return_labels():
    labels = np.array([0, 0, 1, 1, 1, 0])
    matrix = _transition_matrix(labels, 2)
    np.testing.assert_allclose(matrix.sum(axis=1), 1)
    assert _durations(labels).tolist() == [1, 2, 1, 2, 3, 1]


def test_novelty_uses_training_distribution():
    train = np.array([[0.0, 0.0], [0.1, -0.1], [-0.1, 0.1], [0.05, 0.05]])
    all_rows = np.r_[train, [[10.0, 10.0]]]
    _, percentiles, labels = _novelty(train, all_rows)
    assert percentiles[-1] == 1
    assert labels[-1] == "historically_novel"


def test_candidate_explicit_persistence_schema_and_idempotency():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    record = RegimeCandidateRecord(
        run_id="run",
        algorithm="gaussian_hmm",
        k=3,
        train_rows=800,
        test_rows=200,
        silhouette_train=0.21,
        silhouette_test=0.18,
        persistence=0.91,
        min_cluster_share=0.12,
        oos_reproducibility=0.97,
        selection_score=0.54,
        selected=True,
        status="stable",
    )
    save_candidate(con, record)
    save_candidate(con, record)
    assert con.execute("SELECT count(*) FROM regime_model_candidates").fetchone()[0] == 1
    columns = tuple(row[1] for row in con.execute("PRAGMA table_info('regime_model_candidates')").fetchall())
    assert columns == MODEL_CANDIDATE_COLUMNS
    stored = con.execute(
        "SELECT algorithm,k,train_rows,test_rows,selection_score,selected,status FROM regime_model_candidates"
    ).fetchone()
    assert stored == ("gaussian_hmm", 3, 800, 200, 0.54, True, "stable")
