from __future__ import annotations

import numpy as np
import pandas as pd

from moex_analytics.statistical_models.core import FEATURES, _fit_one


def _sample(rows: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    frame = pd.DataFrame({"trade_date": pd.bdate_range("2018-01-01", periods=rows)})
    for index, feature in enumerate(FEATURES):
        frame[feature] = rng.normal(scale=1 + index / 20, size=rows)
    frame["forward_return"] = .01 * frame.return_20 - .005 * frame.realized_vol_20 + rng.normal(
        scale=.02, size=rows
    )
    return frame


def test_train_calibration_test_are_chronological_and_test_is_untouched() -> None:
    sample = _sample()
    metrics, predictions, coefficients = _fit_one(sample, "ridge")
    assert predictions.train_end.max() < predictions.trade_date.min()
    assert len(predictions) == metrics["oos_n"]
    assert 95 <= len(predictions) <= 100
    assert set(coefficients.feature) == set(FEATURES)
    assert np.isfinite(predictions.prediction).all()
    assert 0 <= metrics["sign_stability"] <= 1
    assert metrics["ci_low"] <= metrics["ci_high"]


def test_regularized_model_suite_uses_only_frozen_alpha_grid() -> None:
    sample = _sample()
    for model in ("ridge", "lasso", "elastic_net", "huber", "quantile_q50"):
        metrics, predictions, _ = _fit_one(sample, model)
        assert metrics["alpha"] in {.001, .01, .1, 1.0, 10.0}
        assert (predictions.split == "test").all()
        assert predictions.probability_up.isna().all()
