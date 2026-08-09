import duckdb
import numpy as np
import pandas as pd

import moex_analytics.portfolio_learning.core as learning
from moex_analytics.model_tournament.schema import DDL as TOURNAMENT_DDL
from moex_analytics.portfolio_research.schema import DDL as PORTFOLIO_DDL


def test_marginal_impact_is_decomposed():
    weights = np.array([0.6, 0.4])
    covariance = np.array([[0.04, 0.01], [0.01, 0.09]])
    result = learning._marginal_impact(weights, covariance, 1, 0.1)
    assert set(result) == {
        "delta_weight",
        "delta_volatility",
        "delta_concentration",
        "delta_risk_contribution",
        "correlation_effect",
    }
    assert result["delta_weight"] > 0


def test_backtest_includes_cost_and_no_future_shift():
    returns = pd.DataFrame({"A": [0.01, -0.02, 0.03], "B": [0.0, 0.01, -0.01]})
    result = learning._backtest(returns, np.array([0.5, 0.5]), commission=0.001)
    assert result["observations"] == 3
    assert result["commissions"] == 0.001
    assert result["total_return"] < (1.005 * 0.995 * 1.01 - 1)


def test_portfolio_metrics_and_status_empty():
    volatility, contribution = learning._portfolio_metrics(np.array([0.5, 0.5]), np.eye(2))
    assert volatility > 0
    assert np.isclose(contribution.sum(), 1)
    con = duckdb.connect(":memory:")
    assert learning.portfolio_learning_status(con) == {"latest": None}


def test_portfolio_learning_run_is_research_only_and_cash_is_available():
    con = duckdb.connect(":memory:")
    con.execute(PORTFOLIO_DDL)
    con.execute(TOURNAMENT_DDL)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute(
        """CREATE TABLE portfolio_action_map(
        snapshot_id VARCHAR,secid VARCHAR,valuation_status VARCHAR,dividend_status VARCHAR,
        fundamental_confidence VARCHAR,allowed_action VARCHAR)"""
    )
    con.execute(
        """INSERT INTO portfolio_snapshots VALUES
        ('snapshot','2025-01-01',current_timestamp,'hash',100000,0,'ok')"""
    )
    for secid, weight, price, action in (
        ("AAA", 0.6, 100.0, "consider"),
        ("BBB", 0.4, 200.0, "do_not_increase"),
    ):
        con.execute(
            "INSERT INTO portfolio_instruments(secid,lot_size,sector) VALUES (?,?,?)",
            [secid, 10, "sector"],
        )
        con.execute(
            "INSERT INTO portfolio_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ["snapshot", secid, 100, price, price, 100000 * weight, weight, None, None, True, 20],
        )
        con.execute(
            "INSERT INTO portfolio_action_map VALUES (?,?,?,?,?,?)",
            ["snapshot", secid, "attractive", "confirmed", "high", action],
        )
    dates = pd.bdate_range("2020-01-01", periods=800)
    rng = np.random.default_rng(280)
    for secid in ("AAA", "BBB"):
        close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, len(dates)))
        con.executemany(
            "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
            [(date.date(), secid, float(value)) for date, value in zip(dates, close, strict=True)],
        )
    result = learning.run_portfolio_learning(con)
    assert result["orders_created"] == 0
    assert result["production_change"] is False
    assert (
        con.execute("SELECT count(*) FROM portfolio_learning_backtests WHERE method='cash'").fetchone()[0]
        == 1
    )
    assert con.execute("SELECT bool_and(immutable) FROM portfolio_marginal_candidates").fetchone()[0]
