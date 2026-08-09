import duckdb
import numpy as np
import pandas as pd

import moex_analytics.meta_learning.core as meta
from moex_analytics.model_tournament.schema import DDL as TOURNAMENT_DDL


def _predictions(rows=400):
    rng = np.random.default_rng(27)
    probability = rng.uniform(0.1, 0.9, rows)
    actual = (rng.random(rows) < probability).astype(int)
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2020-01-01", periods=rows),
            "actual_direction": actual,
            "predicted_direction": probability >= 0.5,
            "probability": probability,
            "actual_return": rng.normal(0, 0.02, rows),
            "predicted_return": (probability - 0.5) / 10,
            "regime": np.where(np.arange(rows) % 3, "normal", "volatile"),
            "model_disagreement": rng.uniform(0, 0.2, rows),
        }
    )


def test_meta_targets_come_from_primary_predictions():
    frame = meta._meta_frame(_predictions())
    assert frame.primary_correct.equals(frame.actual_direction == frame.predicted_direction)
    assert frame.return_scale.iloc[0] != frame.return_scale.iloc[0]


def test_meta_fit_and_selective_thresholds_are_train_only():
    frame = meta._meta_frame(_predictions())
    train, test = frame.iloc[:280], frame.iloc[280:]
    train_confidence = meta._fit_meta(train, train)
    test_confidence = meta._fit_meta(train, test)
    curve = meta._selective_curve(train_confidence, test_confidence, test.primary_correct.to_numpy())
    assert [row[0] for row in curve] == [1.0, 0.7, 0.5, 0.3]
    assert curve[0][1] == 0.0
    assert all(row[4] <= len(test) for row in curve)


def test_abstention_policy_is_fail_closed():
    assert meta._policy(0.9, True) == "abstain"
    assert meta._policy(0.4, False) == "abstain"
    assert meta._policy(0.5, False) == "publish_with_caution"
    assert meta._policy(0.8, False) == "publish_signal"


def test_empty_meta_status():
    con = duckdb.connect(":memory:")
    assert meta.meta_learning_status(con) == {"latest": None}


def test_meta_learning_run_uses_only_pseudo_oos_and_never_changes_production():
    con = duckdb.connect(":memory:")
    con.execute(TOURNAMENT_DDL)
    con.execute(
        """INSERT INTO tournament_runs VALUES
        ('source','data',current_timestamp,'completed','[]','[]','frozen',0.2,1,1,1,'test')"""
    )
    con.execute(
        """INSERT INTO tournament_leaderboard VALUES
        ('source','SBERP',20,'unconditional',NULL,NULL,NULL,NULL,NULL,NULL,
        'unconditional','shadow_candidate','test')"""
    )
    frame = _predictions(420)
    rows = [
        (
            "source",
            "SBERP",
            20,
            "unconditional",
            "pseudo_oos",
            1,
            row.trade_date.date(),
            int(row.actual_direction),
            int(row.predicted_direction),
            float(row.probability),
            float(row.actual_return),
            float(row.predicted_return),
            row.regime,
        )
        for row in frame.itertuples()
    ]
    con.executemany("INSERT INTO tournament_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    result = meta.run_meta_learning(con)
    assert result["models"] == 1
    assert result["production_change"] is False
    assert con.execute("SELECT bool_and(immutable) FROM meta_oos_predictions").fetchone()[0]
    assert meta.meta_learning_status(con)["latest"][1] == "completed"
