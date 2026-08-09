import duckdb
import numpy as np
import pandas as pd

import moex_analytics.feature_learning.core as learning


def _sample(rows=800):
    rng = np.random.default_rng(24)
    index = pd.bdate_range("2018-01-01", periods=rows)
    feature = rng.normal(size=rows)
    return pd.DataFrame(
        {
            "signal": feature,
            "forward_return": 0.25 * feature + rng.normal(scale=0.5, size=rows),
            "regime": np.where(feature > 0, "positive", "negative"),
        },
        index=index,
    )


def test_feature_correlations_folds_and_shrinkage_status():
    sample = _sample()
    ic, rank_ic, count = learning._correlations(sample, "signal")
    assert count == 800
    assert ic > 0 and rank_ic > 0
    folds = learning._fold_values(sample, "signal")
    assert len(folds) == 5
    status, _ = learning._classify(rank_ic, rank_ic, folds, count)
    assert status in {"stable_positive", "regime_dependent"}
    assert set(learning._periods(sample)) == {"expanding", "5y", "3y"}


def test_feature_status_taxonomy():
    assert learning._classify(float("nan"), 0.0, [], 10)[0] == "insufficient_sample"
    assert learning._classify(0.05, 0.005, [0.05] * 5, 500)[0] == "decaying"
    assert learning._classify(0.001, 0.001, [0.001] * 5, 500)[0] == "noise"
    assert learning._classify(0.05, 0.04, [0.1, -0.1, 0.1, -0.1], 500)[0] == "sign_flip"


def test_feature_learning_run_is_immutable_and_research_only(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2025-01-01')")
    raw = _sample().rename(columns={"signal": "ret_1"})
    raw["close"] = 100 * (1 + raw.ret_1.clip(-0.05, 0.05) / 100).cumprod()
    raw["high"] = raw.close * 1.01
    raw["low"] = raw.close * 0.99
    raw["vol_20"] = 0.2
    raw["moex_imoex"] = np.arange(len(raw)) + 1000
    monkeypatch.setattr(learning, "INSTRUMENTS", ("SBERP",))
    monkeypatch.setattr(learning, "HORIZONS", (5,))
    monkeypatch.setattr(learning, "FAMILIES", {"ret_1": "technical"})
    monkeypatch.setattr(learning, "_macro", lambda _con: pd.DataFrame())
    monkeypatch.setattr(learning, "_build_frame", lambda *_args: raw)
    result = learning.run_feature_learning(con)
    assert result["automatic_production_change"] is False
    assert con.execute("SELECT bool_and(immutable) FROM feature_performance_history").fetchone()[0]
    assert learning.feature_learning_status(con)["latest"][1] == "completed"


def test_empty_feature_learning_status():
    con = duckdb.connect(":memory:")
    assert learning.feature_learning_status(con) == {"latest": None, "statuses": []}
