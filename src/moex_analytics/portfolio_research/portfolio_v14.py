"""Real local portfolio, alternatives, dividends, scenarios and immutable tracking."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import numpy as np
import yaml

from moex_analytics.config import PROJECT_ROOT

from .core import (
    annualized_volatility,
    downside_volatility,
    hierarchical_risk_parity,
    inverse_volatility_weights,
    max_drawdown,
    maximum_diversification_weights,
    minimum_variance_weights,
    normalize_weights,
    risk_contributions,
    transaction_cost,
)
from .external_methods import covariance_shrinkage
from .schema import DDL

REQUIRED = (
    "secid",
    "quantity",
    "average_price",
    "target_weight",
    "maximum_weight",
    "allow_buy",
    "allow_sell",
    "frozen",
    "notes",
)


def parse_local_portfolio(path=None):
    path = path or PROJECT_ROOT / "config/portfolio_positions.local.yaml"
    if not path.exists():
        return {"mode": "demo", "cash": 0.0, "positions": [], "message": "local positions missing; demo mode"}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mode = cfg.get("mode", "demo")
    positions = cfg.get("positions", [])
    if mode == "real":
        for i, p in enumerate(positions):
            missing = [k for k in REQUIRED if k not in p]
            if missing:
                raise ValueError(f"position {i} missing fields: {','.join(missing)}")
    return {
        "mode": mode,
        "cash": float(cfg.get("cash", 0)),
        "positions": positions,
        "message": "private local input" if mode == "real" else "explicit demo mode; not user result",
    }


def _panel(con, ids):
    return (
        con.execute(
            "SELECT trade_date,canonical_secid,total_return FROM daily_returns WHERE canonical_secid IN (SELECT unnest(?)) QUALIFY row_number() over(partition by trade_date,canonical_secid order by calculation_version desc)=1",
            [ids],
        )
        .df()
        .pivot(index="trade_date", columns="canonical_secid", values="total_return")
        .dropna()[ids]
    )


def calculate_real_portfolio(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    cfg = parse_local_portfolio()
    positions = cfg["positions"]
    if not positions:
        return {"mode": "demo", "status": "local_positions_missing", "positions": 0}
    ids = [p["secid"] for p in positions]
    latest = dict(
        con.execute(
            "SELECT canonical_secid,arg_max(close,trade_date) FROM canonical_daily_prices WHERE canonical_secid IN (SELECT unnest(?)) GROUP BY 1",
            [ids],
        ).fetchall()
    )
    values = np.array([float(p["quantity"]) * latest.get(p["secid"], 0) for p in positions])
    total = float(values.sum() + cfg["cash"])
    weights = values / max(total, 1e-12)
    digest = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    sid = digest[:20]
    con.execute(
        "INSERT OR IGNORE INTO portfolio_snapshots VALUES (?,current_date,current_timestamp,?,?,?,?)",
        [sid, digest, total, cfg["cash"], cfg["mode"]],
    )
    con.execute("DELETE FROM portfolio_positions WHERE snapshot_id=?", [sid])
    for p, value, weight in zip(positions, values, weights, strict=True):
        con.execute(
            "INSERT INTO portfolio_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                sid,
                p["secid"],
                p["quantity"],
                p.get("average_price"),
                latest.get(p["secid"]),
                value,
                weight,
                p.get("target_weight"),
                p.get("maximum_weight"),
                p.get("allow_buy", False),
                p.get("horizon", 250),
            ],
        )
    panel = _panel(con, ids)
    cov = panel.cov().to_numpy() * 252
    portfolio = panel.to_numpy() @ weights
    rc = risk_contributions(cov, weights)
    metrics = {
        "volatility": annualized_volatility(portfolio),
        "downside_volatility": downside_volatility(portfolio),
        "max_drawdown": max_drawdown(portfolio),
        "issuer_concentration": float(weights @ weights),
    }
    for metric, value in metrics.items():
        con.execute(
            "INSERT OR REPLACE INTO portfolio_risk_metrics VALUES (?,?,?,?,?,?)",
            [
                sid,
                metric,
                value,
                "daily total returns; common dates",
                "research",
                json.dumps({"mode": cfg["mode"]}),
            ],
        )
    for secid, component in zip(ids, rc, strict=True):
        con.execute(
            "INSERT OR REPLACE INTO portfolio_factor_exposures VALUES (?,?,?,?,?)",
            [sid, "risk_contribution:" + secid, float(component), "Euler covariance", "research"],
        )
    return {
        "mode": cfg["mode"],
        "message": cfg["message"],
        "snapshot_id": sid,
        "positions": len(ids),
        "value": total,
        "metrics": metrics,
    }


def calculate_portfolio_alternatives(con):  # pragma: no cover - DuckDB integration
    latest = con.execute(
        "SELECT snapshot_id,total_value FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"status": "no_snapshot"}
    sid, value = latest
    pos = con.execute(
        "SELECT secid,weight,target_weight FROM portfolio_positions WHERE snapshot_id=? ORDER BY secid", [sid]
    ).fetchall()
    ids = [p[0] for p in pos]
    current = np.array([p[1] for p in pos])
    panel = _panel(con, ids)
    cov = covariance_shrinkage(panel.to_numpy()) * 252
    methods = {
        "current": current,
        "less_concentrated": normalize_weights(np.sqrt(current)),
        "equal_weight": normalize_weights(np.ones(len(ids))),
        "inverse_volatility": inverse_volatility_weights(cov),
        "hrp": hierarchical_risk_parity(cov),
        "minimum_variance": minimum_variance_weights(cov),
        "maximum_diversification": maximum_diversification_weights(cov),
    }
    targets = np.array([p[2] if p[2] is not None else 0 for p in pos])
    if targets.sum() > 0:
        methods["user_target_weights"] = normalize_weights(targets)
    con.execute("DELETE FROM portfolio_rebalancing_experiments WHERE snapshot_id=?", [sid])
    for name, w in methods.items():
        r = panel.to_numpy() @ w
        vol = annualized_volatility(r)
        ratio = float((w @ np.sqrt(np.diag(cov))) / max(math.sqrt(w @ cov @ w), 1e-12))
        details = {
            "weights": dict(zip(ids, map(float, w), strict=True)),
            "historical_cagr": float(np.prod(1 + r) ** (252 / len(r)) - 1),
            "downside": downside_volatility(r),
            "max_drawdown": max_drawdown(r),
            "risk_contribution": dict(zip(ids, map(float, risk_contributions(cov, w)), strict=True)),
        }
        con.execute(
            "INSERT INTO portfolio_rebalancing_experiments VALUES (?,?,?,?,?,?,?,?,?)",
            [
                sid,
                name,
                json.dumps(details),
                vol,
                ratio,
                float(np.abs(w - current).sum()),
                transaction_cost(current, w, value),
                "no_short; experimental historical comparison",
                True,
            ],
        )
    return {
        "snapshot_id": sid,
        "alternatives": len(methods),
        "warning": "historically best is not a future optimum",
    }


def build_portfolio_dividend_outlook(con):  # pragma: no cover - DuckDB integration
    latest = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"status": "no_snapshot"}
    sid = latest[0]
    con.execute("DELETE FROM portfolio_dividend_outlook WHERE snapshot_id=?", [sid])
    rows = 0
    positions = con.execute(
        "SELECT secid,quantity,average_price,current_price FROM portfolio_positions WHERE snapshot_id=?",
        [sid],
    ).fetchall()
    for secid, qty, cost, price in positions:
        history = con.execute(
            "SELECT registry_close_date,dividend_per_share FROM dividends WHERE canonical_secid=? ORDER BY registry_close_date",
            [secid],
        ).fetchall()
        if not history:
            continue
        dps = float(np.median([x[1] for x in history[-3:]]))
        month = date(date.today().year + 1, 7, 1)
        for scenario, mult, confidence in (
            ("conservative", 0.7, "low"),
            ("base", 1.0, "medium"),
            ("optimistic", 1.2, "low"),
        ):
            gross = qty * dps * mult
            tax = gross * 0.13
            con.execute(
                "INSERT INTO portfolio_dividend_outlook VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    sid,
                    secid,
                    scenario,
                    month,
                    gross,
                    tax,
                    gross - tax,
                    dps * mult,
                    dps * mult / price if price else None,
                    dps * mult / cost if cost else None,
                    "estimated_not_announced",
                    confidence,
                ],
            )
            rows += 1
    return {"snapshot_id": sid, "rows": rows, "warning": "scenario DPS is not announced DPS"}


def calculate_portfolio_scenarios_v2(con):  # pragma: no cover - DuckDB integration
    latest = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"status": "no_snapshot"}
    sid = latest[0]
    ids = [
        x[0]
        for x in con.execute("SELECT secid FROM portfolio_positions WHERE snapshot_id=?", [sid]).fetchall()
    ]
    con.execute("DELETE FROM portfolio_scenarios_v2 WHERE snapshot_id=?", [sid])
    rows = 0
    bench = con.execute(
        "SELECT trade_date,total_return FROM daily_returns WHERE canonical_secid='IMOEX' QUALIFY row_number() over(partition by trade_date order by calculation_version desc)=1"
    ).df()
    for secid in ids:
        asset = con.execute(
            "SELECT trade_date,total_return FROM daily_returns WHERE canonical_secid=? QUALIFY row_number() over(partition by trade_date order by calculation_version desc)=1",
            [secid],
        ).df()
        merged = asset.merge(bench, on="trade_date", suffixes=("_a", "_b")).dropna()
        x = merged.total_return_b.to_numpy()
        y = merged.total_return_a.to_numpy()
        beta = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1)) if len(x) > 60 else math.nan
        rng = np.random.default_rng(14)
        boots = []
        block = 20
        for _ in range(300):
            idx = []
            while len(idx) < len(x):
                start = int(rng.integers(0, max(len(x) - block + 1, 1)))
                idx.extend(range(start, min(start + block, len(x))))
            idx = np.asarray(idx[: len(x)])
            boots.append(float(np.cov(x[idx], y[idx], ddof=1)[0, 1] / np.var(x[idx], ddof=1)))
        beta_low, beta_high = np.quantile(boots, [0.05, 0.95])
        impact = beta * -0.15
        range_low, range_high = sorted((float(beta_low * -0.15), float(beta_high * -0.15)))
        midpoint = len(x) // 2
        first_beta = float(np.cov(x[:midpoint], y[:midpoint], ddof=1)[0, 1] / np.var(x[:midpoint], ddof=1))
        last_beta = float(np.cov(x[midpoint:], y[midpoint:], ddof=1)[0, 1] / np.var(x[midpoint:], ddof=1))
        warning = abs(first_beta - last_beta) > 0.25
        con.execute(
            "INSERT INTO portfolio_scenarios_v2 VALUES (?,?,?,?,?,?,?,?,?)",
            [
                sid,
                secid,
                "IMOEX_minus_15",
                impact,
                range_low,
                range_high,
                "medium" if not warning else "low",
                "historical beta plus 20-session block bootstrap; half-sample break diagnostic",
                bool(warning),
            ],
        )
        rows += 1
    return {
        "snapshot_id": sid,
        "rows": rows,
        "method_limit": "market beta implemented; rate/FX/oil require validated PIT proxies",
    }


def save_real_portfolio_snapshot(con):  # pragma: no cover - DuckDB integration
    latest = con.execute(
        "SELECT snapshot_id,total_value,status FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"status": "no_snapshot"}
    sid, value, mode = latest
    weights = dict(
        con.execute("SELECT secid,weight FROM portfolio_positions WHERE snapshot_id=?", [sid]).fetchall()
    )
    risk = dict(
        con.execute("SELECT metric,value FROM portfolio_risk_metrics WHERE snapshot_id=?", [sid]).fetchall()
    )
    variants = dict(
        con.execute(
            "SELECT method,weights_json FROM portfolio_rebalancing_experiments WHERE snapshot_id=?", [sid]
        ).fetchall()
    )
    payload = {
        "sid": sid,
        "value": value,
        "mode": mode,
        "weights": weights,
        "risk": risk,
        "variants": variants,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    shadow = digest[:24]
    before = con.execute(
        "SELECT count(*) FROM real_portfolio_live_shadow WHERE snapshot_id=?", [shadow]
    ).fetchone()[0]
    con.execute(
        "INSERT OR IGNORE INTO real_portfolio_live_shadow VALUES (?,current_timestamp,current_date,?,?,?,?,?,?,?,?,?,?,?)",
        [
            shadow,
            mode,
            value,
            json.dumps(weights),
            json.dumps(risk),
            json.dumps({}),
            json.dumps({}),
            "unassigned",
            json.dumps(variants),
            digest,
            json.dumps({"portfolio": "v14"}),
            True,
        ],
    )
    return {"inserted": 0 if before else 1, "snapshot_id": shadow, "mode": mode}


def portfolio_validation_status(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    return {
        "alpha": dict(
            con.execute("SELECT status,count(*) FROM portfolio_alpha_validations GROUP BY 1").fetchall()
        ),
        "cross": con.execute("SELECT count(*) FROM portfolio_cross_validations").fetchone()[0],
        "reconciliation": con.execute("SELECT count(*) FROM portfolio_metric_reconciliation").fetchone()[0],
        "issuer_metrics": con.execute("SELECT count(*) FROM issuer_source_maps").fetchone()[0],
        "dividend_outlook": con.execute("SELECT count(*) FROM portfolio_dividend_outlook").fetchone()[0],
        "scenarios_v2": con.execute("SELECT count(*) FROM portfolio_scenarios_v2").fetchone()[0],
        "live": con.execute("SELECT count(*) FROM real_portfolio_live_shadow").fetchone()[0],
    }


def run_portfolio_validation(con):  # pragma: no cover - orchestration
    from .external_methods import audit_external_methods, compare_okama_metrics
    from .issuers import discover_issuer_fundamentals
    from .validation import validate_cross_instrument_factors, validate_portfolio_alpha

    result = {
        "alpha": validate_portfolio_alpha(con),
        "cross": validate_cross_instrument_factors(con),
        "external": audit_external_methods(con),
        "portfolio": calculate_real_portfolio(con),
    }
    result["alternatives"] = calculate_portfolio_alternatives(con)
    result["okama"] = compare_okama_metrics(con)
    result["issuers"] = discover_issuer_fundamentals(con)
    result["dividends"] = build_portfolio_dividend_outlook(con)
    result["scenarios"] = calculate_portfolio_scenarios_v2(con)
    result["shadow"] = save_real_portfolio_snapshot(con)
    result["status"] = portfolio_validation_status(con)
    return result
