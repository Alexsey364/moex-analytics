"""Stage 55 realistic entry timing without future-low selection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from moex_analytics.ranking_engine.core import PORTFOLIO

from .schema import DDL

VERSION = "entry-timing-v2-issuer-sessions"
HORIZONS = (5, 20, 60, 120, 250)
COMMISSION = .001
POLICIES = {
    "BUY_NOW": ("unconditional", 0),
    "WAIT_3": ("fixed_session", 3),
    "WAIT_5": ("fixed_session", 5),
    "WAIT_10": ("fixed_session", 10),
    "BUY_AFTER_DIP_2": ("issuer_close_first_touch_-2pct", 10),
    "BUY_AFTER_DIP_3": ("issuer_close_first_touch_-3pct", 10),
    "BUY_AFTER_MARKET_CONFIRMATION": ("imoex_close_above_t0_by_1pct", 10),
    "BUY_AFTER_RELATIVE_STRENGTH_CONFIRMATION": ("issuer_vs_imoex_above_t0_by_1pct", 10),
}


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _definitions(con: Any) -> None:
    rows = [[policy, VERSION, rule, "first executable session after close signal; fixed waits "
             "execute at named session", wait, COMMISSION * 10000, False, True]
            for policy, (rule, wait) in POLICIES.items()]
    frame = pd.DataFrame(rows, columns=("policy", "version", "signal_rule", "execution_rule",
        "max_wait", "commission_bps", "perfect_hindsight", "immutable"))
    con.register("_timing_definitions", frame)
    con.execute("INSERT OR IGNORE INTO timing_policy_definitions "
                "(policy,version,signal_rule,execution_rule,max_wait,commission_bps,"
                "perfect_hindsight,immutable) SELECT * FROM _timing_definitions")
    con.unregister("_timing_definitions")


def first_signal_entry(policy: str, position: int, exit_position: int, issuer: np.ndarray,
                       market: np.ndarray) -> tuple[int | None, int | None]:
    """Return signal and next-session execution positions under a frozen rule."""
    if policy == "BUY_NOW":
        return position, position + 1
    if policy.startswith("WAIT_"):
        delay = int(policy.split("_")[1])
        if position + delay > exit_position:
            return None, None
        return position + delay - 1, position + delay
    search_end = min(position + 10, exit_position - 1)
    if search_end <= position:
        return None, None
    if policy.startswith("BUY_AFTER_DIP"):
        dip = .02 if policy.endswith("_2") else .03
        condition = issuer[position + 1 : search_end + 1] <= issuer[position] * (1 - dip)
    elif policy == "BUY_AFTER_MARKET_CONFIRMATION":
        condition = market[position + 1 : search_end + 1] >= market[position] * 1.01
    elif policy == "BUY_AFTER_RELATIVE_STRENGTH_CONFIRMATION":
        relative = issuer / market
        condition = relative[position + 1 : search_end + 1] >= relative[position] * 1.01
    else:
        raise ValueError(f"unknown timing policy: {policy}")
    found = np.flatnonzero(condition)
    if not len(found):
        return None, None
    signal = position + 1 + int(found[0])
    return signal, signal + 1 if signal + 1 <= exit_position else None


def _bootstrap_delta(values: np.ndarray, iterations: int = 300) -> tuple[float, float]:
    values = values[np.isfinite(values)]
    if len(values) < 20:
        return np.nan, np.nan
    rng = np.random.default_rng(42)
    means = [float(rng.choice(values, len(values), replace=True).mean()) for _ in range(iterations)]
    return float(np.quantile(means, .025)), float(np.quantile(means, .975))


def _build_outcomes(con: Any, run_id: str, train_end: Any, validation_end: Any,
                    holdout_start: Any) -> pd.DataFrame:
    frame = con.execute("SELECT trade_date,canonical_secid AS secid,total_return_index "
                        "FROM daily_returns WHERE calculation_version='actual-dividends-v1' "
                        "AND canonical_secid IN (SELECT unnest(?)) ORDER BY trade_date,secid",
                        [list((*PORTFOLIO, "IMOEX"))]).df()
    market_series = frame[frame.secid == "IMOEX"].set_index("trade_date").total_return_index
    regime_rows = con.execute("SELECT trade_date,regime FROM regime_timeline_v2 WHERE selected "
                              "QUALIFY row_number() OVER(PARTITION BY trade_date "
                              "ORDER BY run_id DESC)=1").fetchall()
    regimes = dict(regime_rows)
    rows = []
    for secid in PORTFOLIO:
        security = frame[frame.secid == secid].dropna(subset=["total_return_index"])
        if security.empty:
            continue
        dates = pd.DatetimeIndex(pd.to_datetime(security.trade_date))
        issuer = security.total_return_index.to_numpy(float)
        market = market_series.reindex(security.trade_date, method="ffill").to_numpy(float)
        valid = np.isfinite(issuer) & np.isfinite(market)
        returns = pd.Series(issuer).pct_change(fill_method=None)
        volatility = returns.rolling(20).std().to_numpy()
        for position in np.flatnonzero(valid):
            for horizon in HORIZONS:
                exit_position = position + horizon
                if exit_position >= len(issuer) or not valid[exit_position] or position + 1 >= len(issuer):
                    continue
                trade_date, exit_date = dates[position], dates[exit_position]
                if exit_date <= pd.Timestamp(train_end):
                    sample = "train"
                    history_end = pd.Timestamp(train_end)
                elif trade_date > pd.Timestamp(train_end) and exit_date <= pd.Timestamp(validation_end):
                    sample = "validation"
                    history_end = pd.Timestamp(train_end)
                elif trade_date >= pd.Timestamp(holdout_start):
                    sample = "untouched_holdout_frozen"
                    history_end = pd.Timestamp(validation_end)
                else:
                    continue
                buy_entry = issuer[position + 1]
                buy_return = issuer[exit_position] / buy_entry - 1 - 2 * COMMISSION
                for policy in POLICIES:
                    signal, entry = first_signal_entry(policy, position, exit_position, issuer, market)
                    entered = entry is not None and valid[entry]
                    if entered:
                        net_return = float(issuer[exit_position] / issuer[entry] - 1 - 2 * COMMISSION)
                        path = issuer[entry : exit_position + 1] / issuer[entry]
                        drawdown = float((path / np.maximum.accumulate(path) - 1).min())
                        improvement = float(1 - issuer[entry] / buy_entry)
                        missed = float(buy_return - net_return)
                    else:
                        net_return, drawdown, improvement, missed = 0.0, 0.0, None, float(buy_return)
                    vol_state = (
                        "high"
                        if np.isfinite(volatility[position]) and volatility[position] > .02
                        else "normal"
                    )
                    rows.append([run_id, trade_date, secid, horizon, policy,
                        dates[signal] if signal is not None else None,
                        dates[entry] if entered else None, exit_date, entered,
                        entry - position if entered else None, float(issuer[entry]) if entered else None,
                        net_return, float(buy_return), improvement, missed, drawdown, vol_state,
                        regimes.get(trade_date.date()), sample, history_end, True])
    columns = ("run_id", "trade_date", "secid", "horizon", "policy", "signal_date",
        "entry_date", "exit_date", "entered", "wait_sessions", "entry_index", "net_return",
        "buy_now_return", "entry_improvement", "missed_upside", "max_drawdown",
        "volatility_state", "regime", "sample_type", "history_end", "immutable")
    return pd.DataFrame(rows, columns=columns)


def _scorecards(outcomes: pd.DataFrame, run_id: str) -> pd.DataFrame:
    rows = []
    contexts = [("all", "all", pd.Series(True, index=outcomes.index))]
    for state in sorted(outcomes.volatility_state.dropna().unique()):
        contexts.append(("volatility", str(state), outcomes.volatility_state == state))
    for regime in sorted(outcomes.regime.dropna().unique()):
        contexts.append(("regime", str(int(regime)), outcomes.regime == regime))
    for (horizon, policy, sample), base in outcomes.groupby(["horizon", "policy", "sample_type"]):
        for context, value, mask in contexts:
            group = base[mask.reindex(base.index, fill_value=False)]
            if group.empty:
                continue
            delta = group.net_return - group.buy_now_return
            ci_low, ci_high = _bootstrap_delta(delta.to_numpy(float))
            rows.append([run_id, int(horizon), policy, sample, context, value, len(group),
                int(group.entered.sum()), float((~group.entered).mean()),
                float(group.net_return.mean()), float(group.entry_improvement.dropna().median())
                if group.entry_improvement.notna().any() else None,
                float(group.missed_upside.mean()), float(group.max_drawdown.mean()),
                float(delta.mean()), ci_low, ci_high,
                "SUPPORTED" if np.isfinite(ci_low) and ci_low > 0 else "NO_EVIDENCE"])
    columns = ("run_id", "horizon", "policy", "sample_type", "context", "context_value",
        "cases", "entered_cases", "no_entry_rate", "mean_return", "median_entry_improvement",
        "mean_missed_upside", "mean_max_drawdown", "delta_vs_buy_now", "ci_low", "ci_high",
        "status")
    return pd.DataFrame(rows, columns=columns)


def _insert(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    relation = f"_{table}"
    con.register(relation, frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {relation}")
    con.unregister(relation)


def run_timing_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    _definitions(con)
    source = con.execute("SELECT run_id,target_run_id,cutoff,train_end,validation_end,holdout_start "
                         "FROM ranking_research_runs WHERE status='completed' "
                         "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not source:
        raise ValueError("completed Stage 52 run is required")
    ranking_run, target_run, cutoff, train_end, validation_end, holdout_start = source
    run_id = hashlib.sha256(f"{VERSION}|{target_run}|{ranking_run}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,outcome_rows FROM timing_research_runs WHERE run_id=?",
                         [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "outcomes": cached[1], "cached": True}
    con.execute("INSERT OR REPLACE INTO timing_research_runs "
        "(run_id,target_run_id,ranking_run_id,dataset_version,cutoff,train_end,validation_end,"
        "holdout_start,started_at,status,outcome_rows,details_json,immutable) "
        "VALUES (?,?,?,?,?,?,?,?,current_timestamp,'running',0,?,true)",
        [run_id, target_run, ranking_run, VERSION, cutoff, train_end, validation_end, holdout_start,
         json.dumps({"broker_orders": 0, "perfect_hindsight": False})])
    try:
        outcomes = _build_outcomes(con, run_id, train_end, validation_end, holdout_start)
        scores = _scorecards(outcomes, run_id)
        selections, current = [], []
        all_scores = scores[(scores.context == "all") & (scores.context_value == "all")]
        for horizon in HORIZONS:
            validation = all_scores[(all_scores.horizon == horizon) &
                                    (all_scores.sample_type == "validation")]
            selected = validation.sort_values("delta_vs_buy_now", ascending=False).iloc[0]
            policy_hash = hashlib.sha256(
                f"{run_id}|{horizon}|{selected.policy}|validation-only".encode()
            ).hexdigest()
            for row in validation.itertuples():
                selections.append([run_id, horizon, row.policy, row.delta_vs_buy_now, row.ci_low,
                    row.policy == selected.policy, policy_hash if row.policy == selected.policy else
                    hashlib.sha256(f"{run_id}|{horizon}|{row.policy}".encode()).hexdigest(),
                    "validation_only", True])
            holdout = all_scores[(all_scores.horizon == horizon) &
                                 (all_scores.sample_type == "untouched_holdout_frozen") &
                                 (all_scores.policy == selected.policy)].iloc[0]
            for secid in PORTFOLIO:
                current.append([run_id, cutoff, secid, horizon, selected.policy,
                    selected.delta_vs_buy_now, holdout.delta_vs_buy_now, holdout.ci_low,
                    holdout.ci_high, holdout.no_entry_rate, holdout.median_entry_improvement,
                    holdout.mean_missed_upside,
                    "supported" if holdout.status == "SUPPORTED" else "not_proven",
                    "wait" if holdout.status == "SUPPORTED" and selected.policy != "BUY_NOW"
                    else "buy_now_not_beaten",
                    "validation-selected policy; frozen holdout; research only", False, True])
        selection_frame = pd.DataFrame(selections, columns=("run_id", "horizon", "policy",
            "validation_delta", "validation_ci_low", "selected", "policy_hash",
            "selection_sample", "immutable"))
        current_frame = pd.DataFrame(current, columns=("run_id", "cutoff", "secid", "horizon",
            "selected_policy", "validation_delta", "holdout_delta", "holdout_ci_low",
            "holdout_ci_high", "no_entry_rate", "median_entry_improvement", "mean_missed_upside",
            "evidence", "timing_status", "reason", "broker_order", "immutable"))
        for table, frame in (("timing_policy_outcomes", outcomes),
                             ("timing_policy_scorecards", scores),
                             ("timing_policy_selections", selection_frame),
                             ("current_timing_intelligence", current_frame)):
            _insert(con, table, frame)
        details = {"perfect_hindsight": False, "next_session_after_signal": True,
                   "commissions": True, "broker_orders": 0, "selection_touched_holdout": False,
                   "analog_context": "unavailable_without_historical_oos_scenario_state",
                   "sector_context": "unavailable_without_pit_sector_membership",
                   "probability_published": False, "production_changes": 0}
        con.execute("UPDATE timing_research_runs SET finished_at=current_timestamp,status='completed',"
                    "outcome_rows=?,details_json=? WHERE run_id=?",
                    [len(outcomes), json.dumps(details), run_id])
        return {"run_id": run_id, "status": "completed", "outcomes": len(outcomes),
                "scorecards": len(scores), "current_rows": len(current_frame), "cached": False}
    except Exception as exc:
        con.execute("UPDATE timing_research_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def timing_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,train_end,validation_end,holdout_start,"
                      "outcome_rows,details_json FROM timing_research_runs "
                      "ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "train_end", "validation_end",
                    "holdout_start", "outcomes", "details"), row, strict=True))
