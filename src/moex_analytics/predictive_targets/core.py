"""Stage 51: deterministic PIT-safe predictive target redesign."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "predictive-targets-v4-return-foundation"
HORIZONS = (1, 5, 20, 60, 120, 250)
ENTRY_POLICIES = {"BUY_NOW": 1, "WAIT_3": 3, "WAIT_5": 5, "WAIT_10": 10}
LIMIT_POLICIES = {"BUY_AFTER_DIP_2": 0.02, "BUY_AFTER_DIP_3": 0.03}
PATH_SHAPES = {"steady_up", "steady_down", "dip_then_recover", "rise_then_fall",
               "sideways", "high_volatility"}


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _path_shape(path_returns: np.ndarray) -> str:
    """Classify a realised path using fixed economic thresholds, never fitted labels."""
    terminal = float(path_returns[-1])
    low, high = float(path_returns.min()), float(path_returns.max())
    volatility = float(np.std(np.diff(np.r_[0.0, path_returns])))
    if volatility > 0.04 or high - low > 0.20:
        return "high_volatility"
    if low <= -0.05 and terminal >= 0.02:
        return "dip_then_recover"
    if high >= 0.05 and terminal <= 0.0:
        return "rise_then_fall"
    positive_steps = float(np.mean(np.diff(np.r_[0.0, path_returns]) >= 0))
    if terminal >= 0.03 and positive_steps >= 0.60:
        return "steady_up"
    if terminal <= -0.03 and positive_steps <= 0.40:
        return "steady_down"
    return "sideways"


def _definitions(con: Any) -> None:
    definitions = {
        "total_return": ("Forward dividend-adjusted total return", "TRI[t+h]/TRI[t]-1"),
        "excess_imoex": ("Forward return less IMOEX", "R[i,t,h]-R[IMOEX,t,h]"),
        "excess_sector": ("Forward return less PIT sector index", "R[i,t,h]-R[sector(i,t),t,h]"),
        "percentile_rank": ("Date-wise eligible-universe return percentile", "rank_pct(R[:,t,h])"),
        "material_move": ("Frozen absolute material-move flags", "R compared with +/-3/5/10/15%"),
        "path_excursion": ("Observed path MFE, MAE and drawdown", "path extrema through t+h"),
        "path_shape": ("Deterministic realised path class", "fixed v1 path rules"),
        "risk_adjusted": ("Forward return scaled by realised risk", "R/vol, R/abs(MAE), Calmar"),
        "forward_return": ("Forward dividend-adjusted arithmetic return", "TRI[t+h]/TRI[t]-1"),
        "forward_log_return": ("Forward dividend-adjusted log return", "log(TRI[t+h]/TRI[t])"),
        "up": ("Positive forward return label", "1 if forward_return>0 else 0"),
        "outperform_market": ("Market outperformance label", "1 if excess_imoex>0 else 0"),
    }
    rows = []
    for horizon in HORIZONS:
        for name, (description, formula) in definitions.items():
            rows.append([name, VERSION, description, formula, horizon,
                         "features and eligibility known at close T; outcome ends at T+h",
                         "target only; first executable session used for entry policies",
                         None, True, True])
    frame = pd.DataFrame(rows, columns=("target_name", "target_version", "description", "formula",
        "horizon", "cutoff_semantics", "execution_semantics", "threshold",
        "point_in_time_safe", "immutable"))
    con.register("_target_definitions", frame)
    con.execute("INSERT OR IGNORE INTO predictive_target_definitions "
                "(target_name,target_version,description,formula,horizon,cutoff_semantics,"
                "execution_semantics,threshold,point_in_time_safe,created_at,immutable) "
                "SELECT target_name,target_version,description,formula,horizon,cutoff_semantics,"
                "execution_semantics,threshold,point_in_time_safe,current_timestamp,immutable "
                "FROM _target_definitions")
    con.unregister("_target_definitions")


def _source(con: Any) -> tuple[pd.DataFrame, str, str]:
    versions = con.execute("SELECT DISTINCT calculation_version FROM daily_returns").fetchall()
    if len(versions) != 1:
        raise ValueError("Stage 51 requires exactly one frozen daily_returns version")
    source_version = str(versions[0][0])
    frame = con.execute(
        "SELECT trade_date,canonical_secid AS secid,total_return_index FROM daily_returns "
        "WHERE calculation_version=? AND total_return_index>0 ORDER BY secid,trade_date",
        [source_version],
    ).df()
    if frame.empty or "IMOEX" not in set(frame.secid):
        raise ValueError("dividend-adjusted history including IMOEX is required")
    digest_rows = con.execute(
        "SELECT count(*),min(trade_date),max(trade_date),sum(hash(trade_date,canonical_secid,"
        "total_return_index)) FROM daily_returns WHERE calculation_version=?",
        [source_version],
    ).fetchone()
    input_hash = hashlib.sha256(repr(digest_rows).encode()).hexdigest()
    return frame, source_version, input_hash


def _records(frame: pd.DataFrame, run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    observations: list[list[Any]] = []
    entries: list[list[Any]] = []
    by_security = {s: g.reset_index(drop=True) for s, g in frame.groupby("secid", sort=True)}
    imoex = by_security["IMOEX"].set_index("trade_date").total_return_index
    for secid, group in by_security.items():
        dates = pd.to_datetime(group.trade_date)
        index = group.total_return_index.to_numpy(float)
        one_day = np.diff(np.log(index), prepend=np.nan)
        for position in range(len(group)):
            for horizon in HORIZONS:
                if position + horizon >= len(group):
                    continue
                start = index[position]
                future_index = index[position + 1 : position + horizon + 1]
                path = future_index / start - 1.0
                total_return = float(path[-1])
                trade_date = dates.iloc[position]
                exit_date = dates.iloc[position + horizon]
                benchmark = None
                if trade_date in imoex.index and exit_date in imoex.index:
                    benchmark = float(imoex.loc[exit_date] / imoex.loc[trade_date] - 1)
                running_peak = np.maximum.accumulate(np.r_[start, future_index])
                drawdown = np.r_[start, future_index] / running_peak - 1
                realised_vol = float(np.nanstd(one_day[position + 1 : position + horizon + 1]) * np.sqrt(252))
                mae, mfe = float(path.min()), float(path.max())
                downside = abs(min(mae, 0.0))
                observations.append([run_id, trade_date, secid, horizon, exit_date, total_return,
                    total_return - benchmark if benchmark is not None else None, None, None,
                    None, None, None, None, None,
                    *(total_return > x for x in (.03, .05, .10, .15)),
                    *(total_return < -x for x in (.03, .05, .10, .15)), mfe, mae,
                    float(drawdown.min()), int(np.argmax(path) + 1), int(np.argmin(path) + 1),
                    _path_shape(path), realised_vol,
                    total_return / realised_vol if realised_vol > 0 else None,
                    total_return / downside if downside > 0 else None,
                    total_return / abs(float(drawdown.min())) if drawdown.min() < 0 else None,
                    None, "actual-dividend-total-return-index", "unavailable_no_pit_sector_mapping",
                    trade_date, True])
                buy_entry_pos = position + 1
                buy_entry = index[buy_entry_pos]
                buy_return = (
                    float(index[position + horizon] / buy_entry - 1)
                    if buy_entry_pos <= position + horizon
                    else 0.0
                )
                for policy, delay in ENTRY_POLICIES.items():
                    entry_pos = min(position + delay, position + horizon)
                    entry_price = index[entry_pos]
                    policy_return = float(index[position + horizon] / entry_price - 1)
                    entries.append([run_id, trade_date, secid, horizon, policy, None,
                        dates.iloc[entry_pos], float(entry_price), exit_date, policy_return, buy_return,
                        float(1 - entry_price / buy_entry), float(buy_return - policy_return), True,
                        trade_date, f"deterministic session delay={delay}; no future-low selection", True])
                for policy, dip in LIMIT_POLICIES.items():
                    threshold = start * (1 - dip)
                    search_end = min(position + 10, position + horizon)
                    candidates = np.flatnonzero(index[position + 1 : search_end + 1] <= threshold)
                    entered = len(candidates) > 0
                    entry_pos = position + 1 + int(candidates[0]) if entered else None
                    entry_price = float(index[entry_pos]) if entry_pos is not None else None
                    policy_return = (
                        float(index[position + horizon] / entry_price - 1)
                        if entry_price is not None
                        else None
                    )
                    entries.append([run_id, trade_date, secid, horizon, policy, float(threshold),
                        dates.iloc[entry_pos] if entry_pos is not None else None, entry_price,
                        exit_date, policy_return, buy_return,
                        float(1 - entry_price / buy_entry) if entry_price is not None else None,
                        float(buy_return - policy_return) if policy_return is not None else buy_return,
                        entered, trade_date,
                        f"first touch of precommitted -{dip:.0%} threshold within 10 sessions", True])
    obs = pd.DataFrame(observations, columns=("run_id", "trade_date", "secid", "horizon",
        "exit_date", "total_return", "excess_imoex", "excess_sector",
        "excess_cross_section_median", "percentile_rank", "top_10", "top_20", "bottom_10",
        "bottom_20", "move_up_3", "move_up_5", "move_up_10", "move_up_15", "move_down_3",
        "move_down_5", "move_down_10", "move_down_15", "mfe", "mae", "path_max_drawdown",
        "time_to_high", "time_to_low", "path_shape", "realized_volatility",
        "return_over_volatility", "return_over_downside", "calmar_utility", "eligible_count",
        "return_basis", "sector_status", "history_end", "immutable"))
    equity = obs.secid != "IMOEX"
    grouped = obs.loc[equity].groupby(["trade_date", "horizon"])["total_return"]
    obs.loc[equity, "excess_cross_section_median"] = (
        obs.loc[equity, "total_return"] - grouped.transform("median")
    )
    obs.loc[equity, "percentile_rank"] = grouped.rank(method="average", pct=True)
    obs.loc[equity, "eligible_count"] = grouped.transform("size").astype(int)
    obs["top_10"] = obs.percentile_rank >= .90
    obs["top_20"] = obs.percentile_rank >= .80
    obs["bottom_10"] = obs.percentile_rank <= .10
    obs["bottom_20"] = obs.percentile_rank <= .20
    entry = pd.DataFrame(entries, columns=("run_id", "trade_date", "secid", "horizon", "policy",
        "signal_threshold", "entry_date", "entry_price", "exit_date", "policy_return",
        "buy_now_return", "entry_improvement", "missed_return", "entered", "history_end",
        "execution_semantics", "immutable"))
    return obs, entry


def build_predictive_targets(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    _definitions(con)
    frame, source_version, input_hash = _source(con)
    cutoff = pd.Timestamp(frame.trade_date.max()).date()
    run_id = hashlib.sha256(f"{VERSION}|{source_version}|{cutoff}|{input_hash}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT status,observation_rows,entry_rows FROM predictive_target_runs WHERE run_id=?",
        [run_id],
    ).fetchone()
    if existing and existing[0] == "completed":
        return {"run_id": run_id, "status": existing[0], "observations": existing[1],
                "entry_targets": existing[2], "cutoff": cutoff, "cached": True}
    con.execute("INSERT OR REPLACE INTO predictive_target_runs "
        "(run_id,dataset_version,source_version,cutoff,input_hash,started_at,status,observation_rows,"
        "entry_rows,details_json,immutable) VALUES (?,?,?,?,?,current_timestamp,'running',0,0,?,true)",
        [run_id, VERSION, source_version, cutoff, input_hash, json.dumps({"production_changes": 0})])
    try:
        observations, entries = _records(frame, run_id)
        supervised = observations[["run_id", "trade_date", "secid", "horizon", "exit_date",
            "total_return", "excess_imoex", "excess_sector", "mfe", "mae",
            "path_max_drawdown", "realized_volatility", "history_end", "immutable"]].copy()
        supervised = supervised.rename(columns={"trade_date": "evaluation_date",
            "exit_date": "target_available_date", "total_return": "forward_return",
            "mfe": "max_favorable_excursion", "mae": "max_adverse_excursion",
            "path_max_drawdown": "max_drawdown", "realized_volatility": "realized_vol"})
        supervised["forward_log_return"] = np.log1p(supervised.forward_return)
        supervised["market_return"] = supervised.forward_return - supervised.excess_imoex
        supervised["sector_return"] = supervised.forward_return - supervised.excess_sector
        supervised["up"] = supervised.forward_return > 0
        supervised["outperform_market"] = supervised.excess_imoex > 0
        supervised["feature_timestamp"] = (
            pd.to_datetime(supervised.evaluation_date.astype(str))
            + pd.Timedelta(hours=18, minutes=50)
        )
        supervised["evaluation_timestamp"] = supervised.feature_timestamp
        supervised["target_version"] = VERSION
        supervised = supervised[["run_id", "evaluation_date", "secid", "horizon",
            "feature_timestamp", "evaluation_timestamp", "target_available_date", "forward_return",
            "forward_log_return", "market_return", "excess_imoex", "sector_return", "excess_sector",
            "up", "outperform_market", "max_drawdown", "max_favorable_excursion",
            "max_adverse_excursion", "realized_vol", "target_version", "history_end", "immutable"]]
        details = {"horizons": HORIZONS, "path_shapes": sorted(PATH_SHAPES),
                   "sector_excess": "unavailable_no_pit_sector_mapping", "future_leakage": False,
                   "probability_published": False, "production_changes": 0}
        # A previous killed attempt may have left rows for the deterministic run
        # id. Replace both child layers in one transaction, never append to them.
        con.execute("BEGIN TRANSACTION")
        try:
            for name, data in (("predictive_target_observations", observations),
                               ("predictive_entry_targets", entries),
                               ("predictive_return_targets", supervised)):
                con.execute(f"DELETE FROM {name} WHERE run_id=?", [run_id])
                con.register(f"_{name}", data)
                columns = ",".join(data.columns)
                con.execute(f"INSERT INTO {name} ({columns}) SELECT {columns} FROM _{name}")
                con.unregister(f"_{name}")
            con.execute("UPDATE predictive_target_runs SET finished_at=current_timestamp,"
                "status='completed',observation_rows=?,entry_rows=?,details_json=? WHERE run_id=?",
                [len(observations), len(entries), json.dumps(details), run_id])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {"run_id": run_id, "status": "completed", "observations": len(observations),
                "entry_targets": len(entries), "cutoff": cutoff, "cached": False}
    except Exception as exc:
        con.execute("UPDATE predictive_target_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def target_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,observation_rows,entry_rows,details_json "
                      "FROM predictive_target_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "observations", "entry_targets", "details"),
                    row, strict=True))
