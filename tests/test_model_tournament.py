import duckdb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import moex_analytics.model_tournament.core as tournament


def _frame(rows=900):
    rng = np.random.default_rng(23)
    index = pd.bdate_range("2018-01-01", periods=rows)
    returns = rng.normal(0.0003, 0.015, rows)
    close = 100 * np.cumprod(1 + returns)
    frame = pd.DataFrame(index=index)
    frame["close"] = close
    frame["high"] = close * (1 + rng.uniform(0, 0.02, rows))
    frame["low"] = close * (1 - rng.uniform(0, 0.02, rows))
    frame["vol_20"] = pd.Series(returns, index=index).rolling(20).std() * np.sqrt(252)
    frame["ret_20"] = pd.Series(close, index=index).pct_change(20)
    frame["regime"] = np.where(frame.ret_20 >= 0, "trend", "weak")
    for feature in tournament.FAMILIES:
        if feature not in frame:
            frame[feature] = rng.normal(size=rows)
    frame["moex_imoex"] = 1000 * np.cumprod(1 + rng.normal(0.0002, 0.01, rows))
    return frame


def test_holdout_is_untouched_and_embargoed():
    development, holdout = tournament.development_holdout(1000, 60)
    assert development[-1] + 60 < holdout[0]
    folds = tournament.walk_forward_folds(len(development), 60)
    assert folds
    for fold in folds:
        assert fold["validation"][0] - fold["train"][-1] >= 60
        assert fold["test"][0] - fold["validation"][-1] >= 60
        assert fold["test"][-1] <= development[-1]


def test_common_sample_and_bootstrap_are_deterministic():
    frame = tournament._add_targets(_frame(), 20)
    sample = frame[frame.direction >= 0].dropna(subset=["forward_return"])
    model = make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=500)
    )
    output = tournament._fit_oos(sample, ["ret_20", "vol_20"], 20, model)
    assert len(output.y) == len(output.p) == len(output.actual)
    baseline = np.full(len(output.y), np.mean(output.y))
    first = tournament._bootstrap_advantage(np.asarray(output.y), np.asarray(output.p), baseline)
    second = tournament._bootstrap_advantage(np.asarray(output.y), np.asarray(output.p), baseline)
    assert first == second


def test_false_discovery_qvalues_are_monotone():
    values = tournament._bh_qvalues([0.001, 0.02, 0.5, 0.04])
    assert values[0] <= values[1] <= values[3] <= values[2]
    assert all(0 <= value <= 1 for value in values)


def test_tournament_run_is_research_only(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2025-01-01')")
    frame = _frame(800)
    monkeypatch.setattr(tournament, "_macro", lambda _con: pd.DataFrame())
    monkeypatch.setattr(tournament, "_build_frame", lambda _con, _secid, _macro_frame: frame)
    estimator = make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=500)
    )
    monkeypatch.setattr(tournament, "_models", lambda: {"logistic": ("linear", estimator)})
    result = tournament.run_tournament(con, instruments=("SBERP",), horizons=(5,))
    assert result["automatic_promotion"] is False
    assert result["models_tested"] >= 1
    assert con.execute("SELECT count(*) FROM tournament_leaderboard").fetchone()[0] == 1
    assert (
        con.execute("SELECT count(*) FROM tournament_results WHERE split='untouched_holdout'").fetchone()[0]
        == 1
    )


def test_previous_running_tournament_is_marked_interrupted(monkeypatch):
    con = duckdb.connect(":memory:")
    tournament.ensure_schema(con)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2025-01-01')")
    con.execute(
        """INSERT INTO tournament_runs VALUES
        ('old','data',current_timestamp,'running','[]','[]','frozen',0.15,NULL,0,0,'partial')"""
    )
    monkeypatch.setattr(tournament, "_macro", lambda _con: pd.DataFrame())
    tournament.run_tournament(con, instruments=(), horizons=())
    status = con.execute("SELECT status FROM tournament_runs WHERE run_id='old'").fetchone()[0]
    assert status == "interrupted"


def test_final_gate_rejects_pseudo_oos_winner_when_holdout_fails():
    con = duckdb.connect(":memory:")
    tournament.ensure_schema(con)
    result = [
        "run",
        "SBERP",
        20,
        "model",
        "linear",
        "pseudo_oos",
        100,
        80,
        0.6,
        0.6,
        0.2,
        0.6,
        0.01,
        0.02,
        0.1,
        0.1,
        0.05,
        None,
        None,
        None,
        "baseline",
        0.5,
        0.1,
        0.02,
        0.18,
        3,
        0.9,
        0.01,
        0.05,
        True,
        True,
        False,
        "shadow_candidate",
        "{}",
    ]
    con.execute("INSERT INTO tournament_results VALUES (" + ",".join("?" for _ in result) + ")", result)
    holdout = result.copy()
    holdout[5] = "untouched_holdout"
    holdout[21:25] = [0.5, -0.01, -0.04, 0.02]
    holdout[32] = "rejected"
    con.execute("INSERT INTO tournament_results VALUES (" + ",".join("?" for _ in holdout) + ")", holdout)
    con.execute(
        """INSERT INTO tournament_leaderboard VALUES
        ('run','SBERP',20,'baseline','model',NULL,NULL,NULL,NULL,NULL,
        'model','shadow_candidate','before holdout')"""
    )
    tournament._apply_final_gates(con, "run")
    winner = con.execute("SELECT winner FROM tournament_leaderboard").fetchone()[0]
    assert winner == "unconditional"


def test_baseline_regime_pooled_and_holdout_branches():
    frame = tournament._add_targets(_frame(), 20)
    sample = frame[frame.direction >= 0].dropna(subset=["forward_return"])
    features = ["ret_20", "vol_20"]
    estimator = make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=500)
    )
    for kind in ("unconditional", "historical_conditional", "momentum", "mean_reversion"):
        assert tournament._baseline_oos(sample, 20, kind, split="validation").y
        assert tournament._baseline_holdout(sample, 20, kind).y
    assert tournament._fit_regime_oos(sample, features, 20, estimator, split="validation").y
    pool = {"A": sample, "B": sample.copy()}
    assert tournament._fit_pooled_oos(sample, pool, features, 20, estimator).y
    assert tournament._holdout(sample, features, 20, estimator).y
    assert tournament._holdout_regime(sample, features, 20, estimator).y
    assert tournament._holdout_pooled(sample, pool, features, 20, estimator).y


def test_insufficient_splits_and_empty_status():
    assert tournament.development_holdout(100, 60)[0].size == 0
    assert tournament.walk_forward_folds(100, 60) == []
    con = duckdb.connect(":memory:")
    assert tournament.tournament_status(con) == {"latest": None, "leaderboard": []}


def test_status_can_read_without_schema_mutation():
    con = duckdb.connect(":memory:")
    tournament.ensure_schema(con)
    assert tournament.tournament_status(con, ensure=False) == {
        "latest": None,
        "leaderboard": [],
    }
