"""Decomposed portfolio-aware research with CASH as a first-class candidate."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime

import numpy as np
import pandas as pd

from .schema import DDL

TRANCHE = 50_000.0
COMMISSION = 0.0005


def ensure_schema(con) -> None:
    con.execute(DDL)


def _portfolio_metrics(weights: np.ndarray, covariance: np.ndarray) -> tuple[float, np.ndarray]:
    variance = float(weights @ covariance @ weights)
    volatility = float(np.sqrt(max(variance, 0)))
    if volatility == 0:
        return 0.0, np.zeros_like(weights)
    contribution = weights * (covariance @ weights) / variance
    return volatility, contribution


def _marginal_impact(weights: np.ndarray, covariance: np.ndarray, index: int, tranche_share: float) -> dict:
    before_volatility, before_contribution = _portfolio_metrics(weights, covariance)
    new = weights * (1 - tranche_share)
    new[index] += tranche_share
    after_volatility, after_contribution = _portfolio_metrics(new, covariance)
    return {
        "delta_weight": float(new[index] - weights[index]),
        "delta_volatility": after_volatility - before_volatility,
        "delta_concentration": float(np.sum(new**2) - np.sum(weights**2)),
        "delta_risk_contribution": float(after_contribution[index] - before_contribution[index]),
        "correlation_effect": float(np.mean(covariance[index, np.arange(len(weights)) != index])),
    }


def _backtest(returns: pd.DataFrame, weights: np.ndarray, commission: float = COMMISSION) -> dict:
    clean = returns.dropna(how="all").fillna(0)
    strategy = clean.to_numpy() @ weights
    if len(strategy):
        strategy = strategy.copy()
        strategy[0] -= commission
    index = np.cumprod(1 + strategy)
    drawdown = index / np.maximum.accumulate(index) - 1 if len(index) else np.array([])
    downside = strategy[strategy < 0]
    return {
        "observations": len(strategy),
        "total_return": float(index[-1] - 1) if len(index) else None,
        "volatility": float(np.std(strategy, ddof=1) * np.sqrt(252)) if len(strategy) > 1 else None,
        "downside": float(np.std(downside, ddof=1) * np.sqrt(252)) if len(downside) > 1 else None,
        "drawdown": float(drawdown.min()) if len(drawdown) else None,
        "turnover": 1.0,
        "commissions": commission,
    }


def _score(value: str | None, positive: set[str]) -> float:
    if value is None:
        return 0.0
    return 1.0 if str(value).lower() in positive else -0.5


def run_portfolio_learning(con) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    snapshot = con.execute(
        "SELECT snapshot_id,total_value FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not snapshot:
        raise RuntimeError("portfolio snapshot is required")
    snapshot_id, total_value = snapshot
    positions = con.execute(
        """SELECT p.secid,p.weight,p.current_price,coalesce(i.lot_size,1) lot_size,
        coalesce(i.sector,'unknown') sector FROM portfolio_positions p
        LEFT JOIN portfolio_instruments i USING(secid) WHERE p.snapshot_id=? ORDER BY p.secid""",
        [snapshot_id],
    ).df()
    ids = positions.secid.tolist()
    prices = (
        con.execute(
        """SELECT trade_date,canonical_secid secid,close FROM canonical_daily_prices
        WHERE canonical_secid IN (SELECT unnest(?)) ORDER BY trade_date""",
            [ids],
        )
        .df()
        .pivot(index="trade_date", columns="secid", values="close")
    )
    returns = prices.pct_change(fill_method=None).tail(756)
    returns = returns.reindex(columns=ids)
    covariance = returns.tail(252).cov(min_periods=80).fillna(0).to_numpy() * 252
    weights = positions.weight.to_numpy(float)
    weights = weights / weights.sum()
    run_id = hashlib.sha256(f"{snapshot_id}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO portfolio_learning_runs VALUES (?,?,current_timestamp,'running',NULL,0,?)",
        [run_id, snapshot_id, "historical research only; no orders"],
    )
    candidates = 0
    for index, row in enumerate(positions.itertuples()):
        lot = max(1, int(row.lot_size))
        lot_cost = lot * float(row.current_price)
        lots = int(TRANCHE // lot_cost) if lot_cost > 0 else 0
        invested = lots * lot_cost
        share = invested / (float(total_value) + invested) if total_value and invested else 0.0
        impact = _marginal_impact(weights, covariance, index, share)
        action = con.execute(
            """SELECT valuation_status,dividend_status,fundamental_confidence,allowed_action
            FROM portfolio_action_map WHERE snapshot_id=? AND secid=?""",
            [snapshot_id, row.secid],
        ).fetchone()
        action = action or (None, None, None, "insufficient_data")
        tournament = con.execute(
            """SELECT r.advantage,l.status FROM tournament_leaderboard l
            LEFT JOIN tournament_results r ON r.run_id=l.run_id AND r.secid=l.secid
            AND r.horizon=l.horizon AND r.model=l.winner AND r.split='untouched_holdout'
            WHERE l.secid=? ORDER BY l.horizon LIMIT 1""",
            [row.secid],
        ).fetchone()
        predictive = float(tournament[0] or 0) if tournament else 0.0
        fundamental = _score(action[2], {"high", "validated", "sufficient"})
        valuation = _score(action[0], {"attractive", "undervalued"})
        dividend = _score(action[1], {"confirmed", "attractive"})
        standalone_risk = float(np.sqrt(max(covariance[index, index], 0)))
        diversification = -impact["delta_volatility"]
        concentration_cost = max(impact["delta_concentration"], 0)
        rank = (
            predictive
            + 0.15 * fundamental
            + 0.15 * valuation
            + 0.1 * dividend
            + diversification
            - concentration_cost
        )
        eligible = bool(
            action[3] not in {"do_not_increase", "insufficient_data"} and row.weight < 0.30 and lots > 0
        )
        status = "eligible_research" if eligible and rank > 0 else "cash_preferred"
        con.execute(
            "INSERT INTO portfolio_marginal_candidates VALUES ("
            + ",".join("?" for _ in range(25))
            + ",TRUE)",
            [
                run_id,
                row.secid,
                TRANCHE,
                lots * lot,
                lots,
                invested,
                impact["delta_weight"],
                impact["delta_volatility"],
                None,
                impact["delta_concentration"],
                impact["delta_risk_contribution"],
                None,
                impact["correlation_effect"],
                predictive,
                fundamental,
                valuation,
                dividend,
                0.0,
                standalone_risk,
                diversification,
                concentration_cost,
                rank,
                eligible,
                status,
                f"allowed_action={action[3]}",
            ],
        )
        candidates += 1
    methods = {
        "current_rule": weights,
        "equal_allocation": np.repeat(1 / len(ids), len(ids)),
        "random_eligible": np.random.default_rng(28).dirichlet(np.ones(len(ids))),
    }
    for method, method_weights in methods.items():
        result = _backtest(returns, method_weights)
        con.execute(
            "INSERT INTO portfolio_learning_backtests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,TRUE,TRUE)",
            [
                run_id,
                method,
                returns.index.min(),
                returns.index.max(),
                result["observations"],
                result["total_return"],
                result["volatility"],
                result["downside"],
                result["drawdown"],
                result["turnover"],
                result["commissions"],
                1,
            ],
        )
    con.execute(
        "INSERT INTO portfolio_learning_backtests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,TRUE,TRUE)",
        [
            run_id,
            "cash",
            returns.index.min(),
            returns.index.max(),
            len(returns),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1,
        ],
    )
    runtime = time.perf_counter() - started
    con.execute(
        "UPDATE portfolio_learning_runs SET status='completed',runtime_seconds=?,candidates=? WHERE run_id=?",
        [runtime, candidates, run_id],
    )
    return {
        "run_id": run_id,
        "candidates": candidates,
        "runtime_seconds": runtime,
        "orders_created": 0,
        "production_change": False,
    }


def portfolio_learning_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,runtime_seconds,candidates FROM portfolio_learning_runs
        ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    return {"latest": latest}
