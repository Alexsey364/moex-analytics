import duckdb
import numpy as np
import pandas as pd

import moex_analytics.training_quality.issuer_evidence as module
from moex_analytics.training_quality.issuer_evidence import (
    EXPERIMENTS,
    HORIZONS,
    MARKET,
    SECIDS,
    _evaluate,
    _folds,
    _issuer,
    issuer_evidence_status,
    run_issuer_evidence_research,
)
from moex_analytics.training_quality.schema import DDL


def test_stage36_matrix_is_frozen_and_probability_remains_gated():
    assert HORIZONS == (20, 60, 120, 250)
    assert len(SECIDS) == 9
    assert set(EXPERIMENTS) == {
        "market_only", "market_sector", "market_fundamentals",
        "market_sector_fundamentals", "pooled_transfer_issuer_context",
    }


def test_stage36_walk_forward_evaluation_uses_only_prior_blocks():
    n = 1500
    signal = np.sin(np.arange(n) / 13)
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2010-01-01", periods=n, freq="D").date,
        "return_20": signal,
        "return_60": np.roll(signal, 2),
        "volatility_20": np.abs(signal) + 0.1,
        "log_turnover": 10 + signal,
        "target": signal + np.random.default_rng(36).normal(0, 0.05, n),
    })
    folds = _folds(frame)
    assert folds
    for train, test in folds:
        assert frame.loc[train, "trade_date"].max() < frame.loc[test, "trade_date"].min()
    result = _evaluate(frame, MARKET)
    assert result["rows"] > 500
    assert result["folds"] >= 3
    assert result["ba"] > result["baseline"]
    assert _issuer("SBERP") == "SBER"


def test_stage36_empty_and_single_class_are_no_evidence():
    assert _evaluate(pd.DataFrame({"target": [1.0]}), MARKET)["status"] == "NO_EVIDENCE"
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2020-01-01", periods=400).date,
        "return_20": np.arange(400),
        "target": np.ones(400),
    })
    assert _evaluate(frame, MARKET)["status"] == "NO_EVIDENCE"
    assert _issuer("UNKNOWN") == "UNKNOWN"


def test_stage36_orchestration_is_research_only(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.execute(
        "INSERT INTO clean_relearning_runs(run_id,started_at,status) "
        "VALUES ('clean',current_timestamp,'completed')"
    )
    con.execute(
        "INSERT INTO issuer_context_runs(run_id,started_at,status) "
        "VALUES ('context',current_timestamp,'completed')"
    )
    sample = pd.DataFrame({"trade_date": [pd.Timestamp("2026-01-01")], "target": [0.01]})
    monkeypatch.setattr(module, "SECIDS", ("SBERP",))
    monkeypatch.setattr(module, "HORIZONS", (20,))
    monkeypatch.setattr(module, "EXPERIMENTS", {
        "market_only": module.MARKET,
        "market_sector": module.MARKET + module.SECTOR,
    })
    monkeypatch.setattr(module, "_frame", lambda *_: sample)

    def fake_evaluate(_frame, features, target_secid=None):
        del _frame, target_secid
        context = "sector_return_20" in features
        return {
            "rows": 600, "folds": 4, "baseline": 0.5, "ba": 0.58 if context else 0.51,
            "mae": 0.02, "rmse": 0.03, "rank_ic": 0.1, "spearman": 0.1,
            "improvement": 0.08 if context else 0.01, "low": 0.02 if context else -0.01,
            "high": 0.14, "wins": 4, "fold_stability": 1.0, "regime_stability": None,
            "status": "WEAK_EVIDENCE", "features": list(features),
        }

    monkeypatch.setattr(module, "_evaluate", fake_evaluate)
    progress = []
    result = run_issuer_evidence_research(con, progress.append)
    assert result["results"] == 2
    assert result["shadow_candidates"] == 1
    assert result["probability_approved"] == result["production_changes"] == 0
    assert progress == ["SBERP 20"]
    assert dict(issuer_evidence_status(con)["statuses"])["SHADOW_CANDIDATE"] == 1
