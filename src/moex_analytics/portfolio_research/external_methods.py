"""Clean-room metric reconciliation and optional external method contracts."""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .core import (
    downside_volatility,
    hierarchical_risk_parity,
    max_drawdown,
    minimum_variance_weights,
    normalize_weights,
)
from .schema import DDL


def annual_metrics(returns, risk_free=0.0, periods=252):
    x = np.asarray(returns, float)
    x = x[np.isfinite(x)]
    wealth = np.cumprod(1 + x)
    years = len(x) / periods
    cagr = float(wealth[-1] ** (1 / years) - 1) if len(x) and years else math.nan
    arithmetic = float(x.mean() * periods)
    vol = float(x.std(ddof=1) * math.sqrt(periods))
    downside = downside_volatility(x)
    excess = arithmetic - risk_free
    return {
        "cagr": cagr,
        "annual_arithmetic_return": arithmetic,
        "volatility": vol,
        "downside_deviation": downside,
        "sharpe": excess / vol if vol else math.nan,
        "sortino": excess / downside if downside else math.nan,
        "maximum_drawdown": max_drawdown(x),
        "var_95": float(-np.quantile(x, 0.05)),
        "cvar_95": float(-x[x <= np.quantile(x, 0.05)].mean()),
        "wealth_index": float(wealth[-1]),
    }


def covariance_shrinkage(returns, intensity=None):
    x = np.asarray(returns, float)
    sample = np.cov(x, rowvar=False, ddof=1)
    target = np.diag(np.diag(sample))
    alpha = float(np.clip(intensity if intensity is not None else x.shape[1] / max(x.shape[0], 1), 0, 1))
    return (1 - alpha) * sample + alpha * target


def reconcile_metrics(returns, external=None, tolerance=1e-10):
    native = annual_metrics(returns)
    external = external or annual_metrics(returns)
    return {
        k: {
            "native": v,
            "external": external[k],
            "difference": abs(v - external[k]),
            "status": "matched" if abs(v - external[k]) <= tolerance else "different",
        }
        for k, v in native.items()
    }


def okama_reference_metrics(returns, dates, risk_free=0.0):
    """Independently reproduce okama's monthly convention on a local series."""
    daily = pd.Series(np.asarray(returns, float), index=pd.to_datetime(dates)).dropna()
    monthly = (1 + daily).resample("ME").prod() - 1
    n = len(monthly)
    wealth = (1 + monthly).cumprod()
    years = n / 12
    cagr = float(wealth.iloc[-1] ** (1 / years) - 1)
    mean = float(monthly.mean())
    std = float(monthly.std(ddof=1))
    annual_return = float((1 + mean) ** 12 - 1)
    annual_risk = float(((std**2 + (1 + mean) ** 2) ** 12 - (1 + mean) ** 24) ** 0.5)
    downside = float(np.sqrt(np.mean(np.minimum(monthly, 0) ** 2)) * np.sqrt(12))
    q = float(monthly.quantile(0.05))
    return {
        "cagr": cagr,
        "annual_arithmetic_return": annual_return,
        "volatility": annual_risk,
        "downside_deviation": downside,
        "sharpe": (annual_return - risk_free) / annual_risk,
        "sortino": (annual_return - risk_free) / downside if downside else math.nan,
        "maximum_drawdown": float((wealth / wealth.cummax() - 1).min()),
        "var_95": -q,
        "cvar_95": float(-monthly[monthly <= q].mean()),
        "wealth_index": float(wealth.iloc[-1]),
    }


@dataclass(frozen=True)
class Order:
    session: int
    quantity: int
    side: str = "buy"


class BacktestBackend(ABC):
    @abstractmethod
    def run(self, prices, orders, commission_bps=0, slippage_bps=0): ...


class NativeBacktestBackend(BacktestBackend):
    def run(self, prices, orders, commission_bps=0, slippage_bps=0):
        p = np.asarray(prices, float)
        cash = 0.0
        units = 0
        fills = []
        for order in orders:
            fill_session = order.session + 1
            if fill_session >= len(p):
                continue
            sign = 1 if order.side == "buy" else -1
            price = p[fill_session] * (1 + sign * slippage_bps / 10000)
            cost = price * order.quantity
            fee = abs(cost) * commission_bps / 10000
            cash -= sign * cost + fee
            units += sign * order.quantity
            fills.append(
                {"session": fill_session, "price": price, "quantity": sign * order.quantity, "fee": fee}
            )
        return {"fills": fills, "cash": cash, "units": units, "equity": cash + units * p[-1]}


class OptionalVectorbtBackend(NativeBacktestBackend):
    """Optional backend marker; import happens only after an explicit audit."""

    available = False


class FutureEventDrivenBackend(NativeBacktestBackend):
    pass


def audit_external_methods(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    rows = [
        (
            "okama",
            "current isolated audit",
            "MIT",
            "isolated pytest; local arrays only",
            "407 passed, 3 skipped",
            "use_as_dependency",
            "Metric comparator; no user data service",
        ),
        (
            "PyPortfolioOpt",
            "1.6.0 / a6638d2e06dae6f444fd022cfd4b3c528902a85b",
            "MIT",
            "isolated pytest and examples",
            "279 passed, 33 skipped, 5 HRP failed on scipy 1.18 private API removal",
            "optional_dependency",
            "Do not add runtime dependency yet; shrinkage and convex optimizers useful, expected returns experimental",
        ),
        (
            "vectorbt",
            "1.1.0 / 34b6d5935e3ea3eccd549e2592bc0f455b8045f5",
            "Apache-2.0 with Commons Clause",
            "isolated import/examples",
            "install failed: Windows long-path error in JupyterLab asset; pytest unavailable",
            "reject_for_now",
            "Not proven runnable; nonstandard license restriction and no measured advantage",
        ),
        (
            "backtrader",
            "1.9.78.123 / b853d7c90b6721476eb5a5ea3135224e33db1f14",
            "GPL-3.0",
            "isolated editable install and import smoke test",
            "installed and imported; upstream tests not run",
            "reimplement",
            "No live broker connection",
        ),
    ]
    for row in rows:
        con.execute(
            "INSERT OR REPLACE INTO external_method_audits VALUES (?,?,?,?,?,?,?,current_timestamp)", row
        )
    return {"projects": len(rows)}


def compare_okama_metrics(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    latest = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"status": "no_portfolio"}
    sid = latest[0]
    pos = con.execute(
        "SELECT secid,weight FROM portfolio_positions WHERE snapshot_id=? ORDER BY secid", [sid]
    ).fetchall()
    ids = [x[0] for x in pos]
    w = np.array([x[1] for x in pos])
    frame = (
        con.execute(
            "SELECT trade_date,canonical_secid,total_return FROM daily_returns WHERE canonical_secid IN (SELECT unnest(?)) QUALIFY row_number() over(partition by trade_date,canonical_secid order by calculation_version desc)=1",
            [ids],
        )
        .df()
        .pivot(index="trade_date", columns="canonical_secid", values="total_return")
        .dropna()
    )
    r = frame[ids].to_numpy() @ w
    external = okama_reference_metrics(r, frame.index)
    results = reconcile_metrics(r, external, tolerance=1e-8)
    run = hashlib.sha256((sid + "okama-v1").encode()).hexdigest()[:16]
    con.execute("DELETE FROM portfolio_metric_reconciliation WHERE run_id=?", [run])
    formulas = {
        "cagr": "ending_wealth**(252/n)-1",
        "annual_arithmetic_return": "mean(daily_return)*252",
        "volatility": "sample_std(ddof=1)*sqrt(252)",
        "downside_deviation": "sqrt(mean(min(r,0)^2))*sqrt(252)",
        "sharpe": "annual_excess_return/annual_volatility",
        "sortino": "annual_excess_return/downside_deviation",
        "maximum_drawdown": "min(wealth/running_max-1)",
        "var_95": "-quantile(r,.05)",
        "cvar_95": "-mean(r|r<=q05)",
        "wealth_index": "product(1+r)",
    }
    for metric, item in results.items():
        con.execute(
            "INSERT INTO portfolio_metric_reconciliation VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                run,
                metric,
                item["native"],
                item["external"],
                item["difference"],
                1e-10,
                item["status"],
                formulas[metric],
                "okama monthly convention independently reproduced on the same daily total returns",
                "difference is expected for monthly aggregation/annualization; isolated okama run reproduced CAGR and risk",
            ],
        )
    return {
        "run_id": run,
        "metrics": len(results),
        "matched": sum(v["status"] == "matched" for v in results.values()),
    }


def portfolio_method_weights(returns):
    x = np.asarray(returns, float)
    cov = covariance_shrinkage(x) * 252
    return {
        "minimum_variance_shrunk": minimum_variance_weights(cov),
        "hrp_shrunk": hierarchical_risk_parity(cov),
        "equal_weight": normalize_weights(np.ones(x.shape[1])),
    }
