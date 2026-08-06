import duckdb
import numpy as np
import pytest

from moex_analytics.unblocked_experiment.core import (
    CAPS,
    DATASETS,
    apply_platt,
    direction_metrics,
    effective_sample_size,
    ensure_schema,
    fit_logistic,
    fit_platt,
    fit_ridge,
    horizon_allowed,
    label_permutation_sanity,
    predict_linear,
    predict_logistic,
    probability_policy,
    random_noise_sanity,
    return_metrics,
    sigmoid,
    temporal_folds,
    train_only_preprocess,
)


def test_optional_feature_families_and_no_sector_blocking():
    assert DATASETS["A"] == ("technical",)
    assert "financial_sector" not in {block for blocks in DATASETS.values() for block in blocks}
    assert "options" in DATASETS["A+F"] and "fundamentals" in DATASETS["A+G"]


def test_horizon_specific_requirements():
    assert horizon_allowed("A+D+E", 1)
    assert not horizon_allowed("A+D+E", 250)
    assert not horizon_allowed("A+G", 1)
    assert horizon_allowed("A+B+C+G", 250)


def test_feature_caps():
    assert CAPS["technical"] <= 35 and CAPS["futures"] <= 15 and CAPS["options"] <= 10


def test_train_only_imputation_and_correlation_filtering():
    train = np.array([[1, 1, np.nan], [2, 2, 1], [3, 3, 2], [4, 4, 3]], float)
    test = np.array([[100, 999, np.nan]], float)
    xtr, xte, names, state = train_only_preprocess(train, test, ["a", "duplicate", "missing"], feature_cap=3)
    assert len(names) == 2 and not np.isnan(xtr).any() and not np.isnan(xte).any()
    assert state["medians"][-1] != 999


def test_purged_folds_and_embargo():
    folds = temporal_folds(1500, 20, n_folds=4, min_train=500)
    assert len(folds) >= 3
    for fold in folds:
        assert fold["train"][-1] < fold["validation"][0] - 19
        assert fold["validation"][-1] < fold["test"][0]
        assert fold["purge"] == 20 and fold["embargo"] == 20


def test_insufficient_temporal_sample():
    assert temporal_folds(100, 20, min_train=500) == []


def test_logistic_and_calibration():
    x = np.array([[-2], [-1], [1], [2]], float)
    y = np.array([0, 0, 1, 1])
    coef = fit_logistic(x, y, iterations=300)
    p = predict_logistic(x, coef)
    assert p[0] < p[-1]
    platt = fit_platt(p, y)
    calibrated = apply_platt(p, platt)
    assert np.all((calibrated > 0) & (calibrated < 1))


def test_ridge_return_model():
    x = np.arange(20, dtype=float)[:, None]
    y = 2 * x[:, 0] + 1
    coef = fit_ridge(x, y, alpha=0.01)
    pred = predict_linear(x, coef)
    assert return_metrics(y, pred)["rmse"] < 0.1


def test_direction_metrics():
    metrics = direction_metrics([0, 0, 1, 1], [0.1, 0.4, 0.6, 0.9])
    assert metrics["balanced_accuracy"] == 1
    assert metrics["brier"] < 0.2 and metrics["ece"] < 0.3


def test_probability_policy_hides_weak_probability():
    weak = {"brier": 0.26, "ece": 0.12}
    assert not probability_policy(weak, 500, 4, 5, 0.01)["allowed"]
    good = {"brier": 0.20, "ece": 0.05}
    assert probability_policy(good, 500, 4, 5, 0.001)["allowed"]


def test_label_permutation_and_random_noise_sanity():
    rng = np.random.default_rng(7)
    x = rng.normal(size=(300, 2))
    y = rng.integers(0, 2, size=300)
    assert label_permutation_sanity(x, y)
    assert random_noise_sanity(x, y)


def test_effective_sample_size():
    assert effective_sample_size([1, 0, 1, 0, 1]) >= 1


def test_schema_and_immutable_shadow_key():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    values = ["id", None, None, 1, "A", "m", "up", None, 0.1, "v", "h", True]
    con.execute("insert into sber_shadow_forecasts values (?,?,?,?,?,?,?,?,?,?,?,?)", values)
    con.execute("insert or ignore into sber_shadow_forecasts values (?,?,?,?,?,?,?,?,?,?,?,?)", values)
    assert con.execute("select count(*) from sber_shadow_forecasts").fetchone()[0] == 1


def test_no_change_schema_rerun():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    ensure_schema(con)
    assert con.execute("select count(*) from sber_experiment_results").fetchone()[0] == 0


def test_sigmoid_stable():
    values = sigmoid(np.array([-1000, 0, 1000]))
    assert values[0] >= 0 and values[-1] <= 1 and values[1] == pytest.approx(0.5)
