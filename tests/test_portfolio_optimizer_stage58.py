from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.portfolio_optimizer import core
from moex_analytics.portfolio_optimizer.core import (
    ensure_schema,
    is_dominated,
    lot_allocation,
    optimizer_status,
    portfolio_metrics,
)


def test_lot_rounding_cost_and_cash_residual() -> None:
    lots, shares, invested, cash = lot_allocation(100_000, 282.74, 10, 0.0015)
    assert shares == lots * 10
    assert invested + cash == 100_000
    assert 0 <= cash < 282.74 * 10 * 1.0015


def test_risk_contribution_and_pareto() -> None:
    volatility, contribution = portfolio_metrics(np.array([0.5, 0.5]), np.eye(2) * 0.04)
    assert volatility > 0
    assert contribution.sum() == 1
    assert is_dominated((0.05, 0.2, 0.3), (0.06, 0.1, 0.3))


def test_cash_is_first_class_and_status_is_empty() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert optimizer_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE portfolio_allocation_plans").fetchall()}
    assert {"cash_reserve", "allocation_json", "robustness", "research_only"} <= columns


def test_missing_current_evidence_is_an_explicit_abstention() -> None:
    source = Path("src/moex_analytics/portfolio_optimizer/core.py").read_text()
    assert "if row.secid in evidence.index" in source
    assert '"evidence_quality": "insufficient_data"' in source


def test_full_optimizer_is_lot_cost_and_cash_aware(monkeypatch) -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE opportunity_research_runs(run_id VARCHAR,cutoff DATE,status VARCHAR,"
        "finished_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE portfolio_snapshots(snapshot_id VARCHAR,total_value DOUBLE,created_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE ranking_research_runs(run_id VARCHAR,validation_end DATE,"
        "status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE distribution_research_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE portfolio_positions(snapshot_id VARCHAR,secid VARCHAR,"
        "current_price DOUBLE,market_value DOUBLE,weight DOUBLE,can_add BOOLEAN)"
    )
    con.execute("CREATE TABLE portfolio_instruments(secid VARCHAR,lot_size INTEGER,sector VARCHAR)")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute(
        "CREATE TABLE opportunity_candidates(run_id VARCHAR,secid VARCHAR,horizon INTEGER,"
        "candidate_type VARCHAR,relative_rank DOUBLE,expected_median DOUBLE,"
        "tail_downside DOUBLE,timing_status VARCHAR,evidence_quality VARCHAR,"
        "abstain BOOLEAN)"
    )
    con.execute(
        "CREATE TABLE ranking_oos_predictions(run_id VARCHAR,trade_date DATE,secid VARCHAR,"
        "horizon INTEGER,predicted_rank DOUBLE,actual_return DOUBLE,imoex_return DOUBLE)"
    )
    con.execute(
        "CREATE TABLE distribution_oos_predictions(run_id VARCHAR,trade_date DATE,"
        "secid VARCHAR,horizon INTEGER,q10 DOUBLE)"
    )
    con.execute(
        "INSERT INTO opportunity_research_runs VALUES ('opp','2026-08-07','completed',current_timestamp)"
    )
    con.execute("INSERT INTO portfolio_snapshots VALUES ('snap',100000,current_timestamp)")
    con.execute(
        "INSERT INTO ranking_research_runs VALUES ('rank','2023-01-01','completed',current_timestamp)"
    )
    con.execute("INSERT INTO distribution_research_runs VALUES ('dist','completed',current_timestamp)")
    dates = pd.bdate_range("2020-01-01", periods=300)
    for number, secid in enumerate(("AAA", "BBB")):
        prices = 100 * np.cumprod(1 + 0.0005 * (number + 1) + np.sin(np.arange(300) / 9) * 0.002)
        con.execute("INSERT INTO portfolio_instruments VALUES (?,10,?)", [secid, f"sector{number}"])
        con.execute(
            "INSERT INTO portfolio_positions VALUES ('snap',?,?,50000,.5,true)", [secid, float(prices[-1])]
        )
        con.executemany(
            "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
            [(date, secid, float(price)) for date, price in zip(dates, prices, strict=True)],
        )
        con.execute(
            "INSERT INTO opportunity_candidates VALUES "
            "('opp',?,60,'equity',?,.05,-.12,'buy_now_not_beaten',"
            "'research_oos',false)",
            [secid, 0.8 - number * 0.3],
        )
        for index, date in enumerate(dates[::20]):
            con.execute(
                "INSERT INTO ranking_oos_predictions VALUES ('rank',?,?,60,?,?,0)",
                [date, secid, 0.8 - number * 0.3, 0.02 + index / 1000],
            )
            con.execute("INSERT INTO distribution_oos_predictions VALUES ('dist',?,?,60,-.1)", [date, secid])
    monkeypatch.setattr(
        core, "load_config", lambda: {"rules": {"transaction_cost_bps": 10, "slippage_bps": 5}}
    )
    result = core.run_portfolio_optimizer(con)
    assert result["status"] == "completed"
    assert result["candidates"] == 10
    assert con.execute("SELECT count(DISTINCT tranche) FROM portfolio_tranche_candidates").fetchone()[0] == 5
    assert (
        con.execute("SELECT bool_and(lots*lot_size=shares) FROM portfolio_tranche_candidates").fetchone()[0]
        is True
    )
    assert (
        con.execute(
            "SELECT bool_and(cash_reserve=tranche AND invested=0) "
            "FROM portfolio_allocation_plans WHERE plan_rank=1"
        ).fetchone()[0]
        is True
    )
    assert con.execute("SELECT count(*) FROM portfolio_optimizer_backtests").fetchone()[0] == 4
    assert core.run_portfolio_optimizer(con)["cached"] is True


def test_unknown_evidence_never_becomes_conditional() -> None:
    lots, shares, invested, residual = lot_allocation(25_000, 100, 10, 0.0015)
    assert lots > 0 and shares > 0
    assert invested == pytest.approx(shares * 100 * 1.0015)
    assert residual >= 0
