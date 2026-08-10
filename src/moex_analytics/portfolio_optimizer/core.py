"""Stage 58 lot-aware, cash-aware research allocations without forced investment."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from moex_analytics.portfolio_research.core import load_config
from moex_analytics.portfolio_research.portfolio_v14 import risk_contributions

from .schema import DDL

VERSION = "cash-aware-optimizer-v1"
TRANCHES = (25_000, 50_000, 100_000, 250_000, 500_000)


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def lot_allocation(
    amount: float, price: float, lot_size: int, cost_rate: float
) -> tuple[int, int, float, float]:
    lot_cost = price * lot_size * (1 + cost_rate)
    lots = int(amount // lot_cost) if lot_cost > 0 else 0
    shares = lots * lot_size
    invested = shares * price * (1 + cost_rate)
    return lots, shares, invested, amount - invested


def portfolio_metrics(weights: np.ndarray, covariance: np.ndarray) -> tuple[float, np.ndarray]:
    variance = float(weights @ covariance @ weights)
    if variance <= 0:
        return 0.0, np.zeros_like(weights)
    return float(np.sqrt(variance)), risk_contributions(covariance, weights)


def is_dominated(left: tuple[float, float, float], right: tuple[float, float, float]) -> bool:
    """Whether left is no better in opportunity, downside and concentration."""
    return (right[0] >= left[0] and right[1] <= left[1] and right[2] <= left[2]
            and right != left)


def _insert(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    con.register("_batch", frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM _batch")
    con.unregister("_batch")


def _backtests(con: Any, run_id: str, ranking_run: str, distribution_run: str,
               history_end: Any) -> pd.DataFrame:
    ranks = con.execute("SELECT trade_date,secid,horizon,predicted_rank,actual_return,imoex_return "
                        "FROM ranking_oos_predictions WHERE run_id=? AND horizon=60",
                        [ranking_run]).df()
    distributions = con.execute("SELECT trade_date,secid,horizon,q10 FROM distribution_oos_predictions "
                                "WHERE run_id=? AND horizon=60", [distribution_run]).df()
    data = ranks.merge(distributions, on=["trade_date", "secid", "horizon"], how="left")
    # One decision per non-overlapping horizon.  This prevents overlapping labels from
    # masquerading as independent portfolio observations.
    decision_dates = sorted(data.trade_date.unique())[::60]
    data = data[data.trade_date.isin(decision_dates)]
    rows = []
    daily = []
    for date, group in data.groupby("trade_date"):
        ordered = group.sort_values("predicted_rank", ascending=False)
        equal = float(group.actual_return.mean())
        ranking = float(ordered.head(3).actual_return.mean())
        robust = ordered.assign(robust=ordered.predicted_rank + ordered.q10.fillna(-1)).sort_values(
            "robust", ascending=False).head(3)
        rank_downside = float(robust.actual_return.mean())
        daily.append([date, equal, ranking, rank_downside, 0.0])
    frame = pd.DataFrame(daily, columns=("date", "equal_addition", "ranking_only",
                                        "ranking_downside", "cash_aware_full"))
    cost = .0015
    for method in frame.columns[1:]:
        values = frame[method].to_numpy(float) - (cost if method != "cash_aware_full" else 0)
        wealth = np.cumprod(1 + values)
        drawdown = wealth / np.maximum.accumulate(wealth) - 1
        downside = values[values < 0]
        rows.append([run_id, method, 60, len(values), float(wealth[-1]) if len(wealth) else 1,
            float(wealth[-1] ** (252 / (len(values) * 60)) - 1) if len(values) else None,
            float(np.std(values) * np.sqrt(252 / 60)),
            float(np.std(downside) * np.sqrt(252 / 60)) if len(downside) else 0,
            float(drawdown.min()) if len(drawdown) else 0, 1.0 if method != "cash_aware_full" else 0,
            cost * len(values) if method != "cash_aware_full" else 0,
            float((values > 0).mean()), float(np.quantile(values, .05)),
            float((frame.iloc[:, 1:4].max(axis=1) - values).mean()), history_end, True, True, True])
    columns = ("run_id", "method", "horizon", "periods", "terminal_wealth", "cagr",
        "volatility", "downside_deviation", "max_drawdown", "turnover", "costs",
        "relative_hit_rate", "worst_5pct", "ex_post_regret", "history_end",
        "executable_next_session", "research_only", "immutable")
    return pd.DataFrame(rows, columns=columns)


def run_portfolio_optimizer(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    opportunity = con.execute("SELECT run_id,cutoff FROM opportunity_research_runs "
                              "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    snapshot = con.execute("SELECT snapshot_id,total_value FROM portfolio_snapshots "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
    ranking = con.execute("SELECT run_id,validation_end FROM ranking_research_runs "
                          "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    distribution = con.execute("SELECT run_id FROM distribution_research_runs "
                               "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not all((opportunity, snapshot, ranking, distribution)):
        raise ValueError("portfolio snapshot and completed Stages 52-56 are required")
    opportunity_run, cutoff = opportunity
    snapshot_id, _total_value = snapshot
    run_id = hashlib.sha256(f"{VERSION}|{opportunity_run}|{snapshot_id}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,candidate_rows,plan_rows,backtest_rows "
                         "FROM cash_aware_optimizer_runs WHERE run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "candidates": cached[1],
                "plans": cached[2], "backtests": cached[3], "cached": True}
    cfg = load_config()
    cost_bps = float(cfg["rules"].get("transaction_cost_bps", 10))
    slippage_bps = float(cfg["rules"].get("slippage_bps", 5))
    cost_rate = (cost_bps + slippage_bps) / 10000
    positions = con.execute("SELECT p.secid,p.current_price,p.market_value,p.weight,p.can_add,"
        "i.lot_size,i.sector FROM portfolio_positions p JOIN portfolio_instruments i USING(secid) "
        "WHERE p.snapshot_id=? ORDER BY p.secid", [snapshot_id]).df()
    ids = positions.secid.tolist()
    prices = con.execute("SELECT trade_date,canonical_secid AS secid,close FROM canonical_daily_prices "
                         "WHERE canonical_secid IN (SELECT unnest(?)) ORDER BY trade_date", [ids]).df()
    returns = (
        prices.pivot(index="trade_date", columns="secid", values="close")
        .pct_change(fill_method=None)
        .tail(252)
    )
    covariance = returns.reindex(columns=ids).cov(min_periods=80).fillna(0).to_numpy() * 252
    evidence = con.execute("SELECT secid,relative_rank,expected_median,tail_downside,timing_status,"
        "evidence_quality,abstain FROM opportunity_candidates WHERE run_id=? AND horizon=60 "
        "AND candidate_type='equity'", [opportunity_run]).df().set_index("secid")
    con.execute("INSERT OR REPLACE INTO cash_aware_optimizer_runs VALUES "
        "(?,?,?,?,current_timestamp,NULL,'running',0,0,0,?,true)",
        [run_id, opportunity_run, snapshot_id, cutoff, json.dumps({"broker_orders": 0})])
    candidate_rows, plan_rows = [], []
    weights = positions.weight.to_numpy(float)
    for tranche in TRANCHES:
        eligible = []
        for idx, row in enumerate(positions.itertuples()):
            lots, shares, invested, residual = lot_allocation(
                tranche, float(row.current_price), int(row.lot_size), cost_rate)
            item = evidence.loc[row.secid]
            new_values = positions.market_value.to_numpy(float).copy()
            new_values[idx] += invested
            new_weights = new_values / new_values.sum()
            volatility, contributions = portfolio_metrics(new_weights, covariance)
            concentration = float(np.sum(new_weights**2))
            sector_concentration = float(new_weights[positions.sector == row.sector].sum())
            sufficient = not bool(item.abstain) and item.evidence_quality == "research_oos"
            status = "CONDITIONAL" if sufficient and row.can_add and shares > 0 else "INSUFFICIENT_EVIDENCE"
            candidate_rows.append([run_id, row.secid, tranche, int(row.lot_size), lots, shares,
                invested, residual, float(new_weights[idx]), concentration, volatility,
                float(returns.min().mean()), float(contributions[idx]), sector_concentration,
                item.relative_rank, item.expected_median, item.tail_downside, item.timing_status,
                item.evidence_quality, float((item.tail_downside or 0) * -1), status,
                "research only; no trade recommendation", True])
            if status == "CONDITIONAL" and new_weights[idx] <= .30:
                eligible.append((row.secid, float(item.relative_rank), float(item.tail_downside)))
        # CASH wins unless every risky leg has robust evidence (none in current frozen scorecards).
        plan_rows.append([run_id, tranche, 1, json.dumps({"CASH": tranche}), 0.0, tranche,
            None, 0.0, float(np.sum(weights**2)), float(portfolio_metrics(weights, covariance)[0]),
            False, "ROBUST", "CASH_PREFERRED", True, True])
        # Persist transparent runner-ups for comparison; they are never promoted while the
        # frozen distribution evidence is insufficient.  Equal splitting is then rounded
        # independently to executable lots and residual cash is retained.
        ordered = [item[0] for item in sorted(eligible, key=lambda item: (-item[1], -item[2]))]
        for rank, size in enumerate(range(1, min(4, len(ordered)) + 1), start=2):
            allocation: dict[str, float] = {}
            invested_total = 0.0
            selected = ordered[:size]
            for secid in selected:
                position = positions.loc[positions.secid == secid].iloc[0]
                _, _, invested, _ = lot_allocation(
                    tranche / size,
                    float(position.current_price),
                    int(position.lot_size),
                    cost_rate,
                )
                allocation[secid] = invested
                invested_total += invested
            allocation["CASH"] = max(0.0, tranche - invested_total)
            medians = [float(evidence.loc[secid].expected_median) for secid in selected]
            tails = [float(evidence.loc[secid].tail_downside) for secid in selected]
            plan_rows.append([
                run_id, tranche, rank, json.dumps(allocation), invested_total,
                max(0.0, tranche - invested_total), float(np.mean(medians)),
                float(np.mean(tails)), None, None, True, "FRAGILE",
                "INSUFFICIENT_EVIDENCE", True, True,
            ])
    candidates = pd.DataFrame(candidate_rows, columns=("run_id", "secid", "tranche", "lot_size",
        "lots", "shares", "invested", "cash_residual", "new_weight", "new_concentration",
        "portfolio_volatility", "downside_volatility", "new_risk_contribution",
        "sector_concentration", "relative_opportunity", "expected_median", "tail_downside",
        "timing", "evidence", "uncertainty_penalty", "status", "reason", "immutable"))
    plans = pd.DataFrame(plan_rows, columns=("run_id", "tranche", "plan_rank", "allocation_json",
        "invested", "cash_reserve", "expected_median", "tail_downside", "concentration",
        "portfolio_volatility", "dominated", "robustness", "status", "research_only", "immutable"))
    backtests = _backtests(con, run_id, ranking[0], distribution[0], ranking[1])
    _insert(con, "portfolio_tranche_candidates", candidates)
    _insert(con, "portfolio_allocation_plans", plans)
    _insert(con, "portfolio_optimizer_backtests", backtests)
    details = {"tranches": TRANCHES, "commission_bps": cost_bps, "slippage_bps": slippage_bps,
               "execution_lag": 1, "cash_can_win": True, "forced_investment": False,
               "broker_orders": 0, "production_changes": 0}
    con.execute("UPDATE cash_aware_optimizer_runs SET finished_at=current_timestamp,status='completed',"
                "candidate_rows=?,plan_rows=?,backtest_rows=?,details_json=? WHERE run_id=?",
                [len(candidates), len(plans), len(backtests), json.dumps(details), run_id])
    return {"run_id": run_id, "status": "completed", "candidates": len(candidates),
            "plans": len(plans), "backtests": len(backtests), "cached": False}


def optimizer_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,candidate_rows,plan_rows,backtest_rows,details_json "
                      "FROM cash_aware_optimizer_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "cutoff", "candidates", "plans", "backtests", "details"), row, strict=True))
