from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.distribution_engine import core
from moex_analytics.distribution_engine.core import (
    _bucket,
    _conformal_radii,
    _pinball,
    distribution_metrics,
    distribution_status,
    ensure_schema,
    run_distribution_research,
)


def test_schema_is_immutable_and_empty_status_is_explicit() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert distribution_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE distribution_oos_predictions").fetchall()}
    assert {"q05", "q50", "q95", "probability_allowed", "policy_hash", "immutable"} <= columns


def test_pinball_and_interval_metrics() -> None:
    actual = np.array([-.1, 0, .1])
    assert _pinball(actual, actual, .5) == 0
    frame = pd.DataFrame({"actual_return": actual, "q05": [-.2, -.1, 0],
        "q10": [-.15, -.05, .02], "q25": [-.12, -.02, .05], "q50": actual,
        "q75": [-.05, .03, .12], "q90": [0, .08, .18], "q95": [.02, .1, .2]})
    result = distribution_metrics(frame)
    assert result["median_mae"] == 0
    assert 0 <= result["coverage_90"] <= 1


def test_temporal_conformal_uses_calibration_residuals() -> None:
    frame = pd.DataFrame({"actual_return": [0, .1, -.1, .2], "q50": [0, 0, 0, 0]})
    q50, q80, q90 = _conformal_radii(frame)
    assert 0 <= q50 <= q80 <= q90


def test_duckdb_date_boundary_is_normalized_for_pandas() -> None:
    values = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-03"]))
    boundary = pd.Timestamp(date(2024, 1, 2))
    assert values[values <= boundary].tolist() == [pd.Timestamp("2024-01-01")]


def test_material_move_is_qualitative_while_probability_is_gated() -> None:
    assert _bucket(.08, .05, "up") == "elevated"
    assert _bucket(-.08, .05, "down") == "elevated"
    assert _bucket(0, .05, "up") == "mixed"


def test_all_distribution_methods_return_ordered_quantiles() -> None:
    rng = np.random.default_rng(7)
    feature_names = core.FEATURES
    train = pd.DataFrame(rng.normal(size=(180, len(feature_names))), columns=feature_names)
    train["actual_return"] = rng.normal(0, .05, len(train))
    target = train.iloc[:8].copy()
    for method in core.METHODS:
        values = core._predict(method, train, target, 20)
        assert values.shape == (8, 7)
        assert np.all(np.diff(values, axis=1) >= -1e-12)
    with pytest.raises(ValueError, match="unknown"):
        core._predict("not-a-method", train, target, 20)


def test_full_run_keeps_policy_and_probability_gate_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
                "total_return_index DOUBLE,calculation_version VARCHAR)")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,"
                "close DOUBLE)")
    con.execute("CREATE TABLE ranking_research_runs(run_id VARCHAR,target_run_id VARCHAR,"
                "cutoff DATE,train_end DATE,validation_end DATE,holdout_start DATE,status VARCHAR,"
                "finished_at TIMESTAMP)")
    con.execute("CREATE TABLE predictive_target_observations(run_id VARCHAR,trade_date DATE,"
                "exit_date DATE,secid VARCHAR,horizon INTEGER,total_return DOUBLE)")
    dates = pd.bdate_range("2018-01-01", periods=300)
    securities = ("X5", "SBERP", "LKOH", "IMOEX")
    levels = {}
    for number, secid in enumerate(securities):
        values = 100 * np.cumprod(1 + .0002 * (number + 1) + np.sin(np.arange(300) / 11) * .001)
        levels[secid] = values
        con.executemany("INSERT INTO daily_returns VALUES (?,?,?,?)",
                        [[date, secid, float(value), "actual-dividends-v1"]
                         for date, value in zip(dates, values, strict=True)])
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?)",
                    [dates[-1], secid, float(values[-1])])
    con.execute("INSERT INTO ranking_research_runs VALUES "
                "('rank-run','target-run',?,?,?,?,'completed',current_timestamp)",
                [dates[-1], dates[199], dates[249], dates[250]])
    labels = []
    for idx, date_value in enumerate(dates[:-5]):
        for secid in securities[:-1]:
            value = float(levels[secid][idx + 5] / levels[secid][idx] - 1)
            labels.append(["target-run", date_value, dates[idx + 5], secid, 5, value])
    con.executemany("INSERT INTO predictive_target_observations VALUES (?,?,?,?,?,?)", labels)
    monkeypatch.setattr(core, "METHODS", ("historical_unconditional",))
    result = run_distribution_research(con)
    assert result["status"] == "completed"
    assert result["predictions"] > 0
    assert con.execute("SELECT bool_and(NOT probability_allowed AND immutable) "
                       "FROM distribution_oos_predictions").fetchone()[0] is True
    assert con.execute("SELECT sum(selected::int),bool_and(selection_sample='validation_only') "
                       "FROM distribution_method_policies").fetchone() == (1, True)
    assert run_distribution_research(con)["cached"] is True
