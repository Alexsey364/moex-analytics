import duckdb
import numpy as np

from moex_analytics.portfolio_optimizer.core import (
    ensure_schema,
    is_dominated,
    lot_allocation,
    optimizer_status,
    portfolio_metrics,
)


def test_lot_rounding_cost_and_cash_residual() -> None:
    lots, shares, invested, cash = lot_allocation(100_000, 282.74, 10, .0015)
    assert shares == lots * 10
    assert invested + cash == 100_000
    assert 0 <= cash < 282.74 * 10 * 1.0015


def test_risk_contribution_and_pareto() -> None:
    volatility, contribution = portfolio_metrics(np.array([.5, .5]), np.eye(2) * .04)
    assert volatility > 0
    assert contribution.sum() == 1
    assert is_dominated((.05, .2, .3), (.06, .1, .3))


def test_cash_is_first_class_and_status_is_empty() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert optimizer_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE portfolio_allocation_plans").fetchall()}
    assert {"cash_reserve", "allocation_json", "robustness", "research_only"} <= columns
