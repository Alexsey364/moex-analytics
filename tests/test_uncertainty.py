import duckdb
import numpy as np
import pandas as pd

import moex_analytics.uncertainty.core as uncertainty
from moex_analytics.model_tournament.schema import DDL as TOURNAMENT_DDL


def test_temporal_calibration_and_metrics_are_finite():
    rng = np.random.default_rng(26)
    train_p = rng.uniform(0.1, 0.9, 300)
    train_y = (rng.random(300) < train_p).astype(int)
    test_p = rng.uniform(0.1, 0.9, 100)
    test_y = (rng.random(100) < test_p).astype(int)
    for method in uncertainty.METHODS:
        calibrated = uncertainty._temporal_calibrate(train_y, train_p, test_p, method)
        assert len(calibrated) == len(test_p)
        assert np.isfinite(calibrated).all()
        assert 0 <= uncertainty._ece(test_y, calibrated) <= 1
        uncertainty._calibration_line(test_y, calibrated)


def test_prediction_intervals_report_empirical_coverage():
    train_actual = np.arange(200) / 100
    train_predicted = train_actual + 0.1
    test_actual = np.arange(100) / 100
    test_predicted = test_actual + 0.1
    result = uncertainty._intervals(train_actual, train_predicted, test_actual, test_predicted)
    assert set(result) == {0.5, 0.8, 0.9}
    assert all(0 <= coverage <= 1 for coverage, _width in result.values())


def test_probability_gate_is_fail_closed():
    allowed, reason = uncertainty._gate(20, 0.9, 0.1, 0.2, 0.01, 1.0, True, True)
    assert not allowed and "effective sample" in reason
    allowed, reason = uncertainty._gate(200, 0.6, 0.15, 0.25, 0.03, 1.0, True, True)
    assert allowed and reason == "all gates passed"


def test_calibration_status_empty():
    con = duckdb.connect(":memory:")
    assert uncertainty.calibration_status(con) == {"latest": None}


def test_calibration_run_writes_immutable_fail_closed_audits():
    con = duckdb.connect(":memory:")
    con.execute(TOURNAMENT_DDL)
    con.execute(
        """INSERT INTO tournament_runs VALUES
        ('source','data',current_timestamp,'completed','[]','[]','frozen',0.2,1,1,1,'test')"""
    )
    rng = np.random.default_rng(260)
    dates = pd.bdate_range("2020-01-01", periods=240)
    probabilities = rng.uniform(0.2, 0.8, len(dates))
    directions = (rng.random(len(dates)) < probabilities).astype(int)
    rows = [
        (
            "source",
            "SBERP",
            20,
            "logistic",
            "pseudo_oos",
            1,
            date.date(),
            int(direction),
            int(probability >= 0.5),
            float(probability),
            float(direction) / 100,
            float(probability - 0.5) / 100,
            "normal",
        )
        for date, probability, direction in zip(dates, probabilities, directions, strict=True)
    ]
    con.executemany("INSERT INTO tournament_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    result = uncertainty.run_calibration_audit(con)
    assert result["production_change"] is False
    assert result["audits"] == 3
    assert con.execute("SELECT bool_and(immutable) FROM probability_calibration_audit").fetchone()[0]
    assert uncertainty.calibration_status(con)["latest"][1] == "completed"
