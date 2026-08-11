"""Deterministic, point-in-time multidimensional regimes (Stage 97)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

from moex_analytics.conditional_similarity.core import build_state_panel
from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

VERSION = "multidimensional-regime-v1.1"
TREND_ORDER = ("crisis", "strong_bear", "weak_bear", "sideways", "weak_bull", "strong_bull")
VOL_ORDER = ("low", "normal", "elevated", "extreme")
RATES_ORDER = ("easing", "stable_normal", "stable_high", "tightening")
STOCK_ORDER = (
    "capitulation", "downtrend", "correction", "sideways", "accumulation", "recovery",
    "uptrend", "breakout",
)


def _config() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[3] / "config" / "conditional_forecasting.yaml"
    raw = path.read_bytes()
    return yaml.safe_load(raw)["regime_conditioning"], hashlib.sha256(raw).hexdigest()


def _ordered_score(left: str, right: str, order: tuple[str, ...]) -> float:
    if left == "insufficient" or right == "insufficient":
        return 0.65
    distance = abs(order.index(left) - order.index(right))
    return (1.0, 0.75, 0.35, 0.10)[min(distance, 3)]


def regime_compatibility(current: dict[str, str], historical: dict[str, str]) -> float:
    scores = [
        _ordered_score(current["market_trend"], historical["market_trend"], TREND_ORDER),
        _ordered_score(current["volatility_regime"], historical["volatility_regime"], VOL_ORDER),
        _ordered_score(current["rates_regime"], historical["rates_regime"], RATES_ORDER),
        _ordered_score(current["stock_state"], historical["stock_state"], STOCK_ORDER),
    ]
    crisis_mismatch = (current["market_trend"] == "crisis") != (
        historical["market_trend"] == "crisis"
    )
    return float(min(np.mean(scores), 0.25) if crisis_mismatch else np.mean(scores))


def classify_regimes(frame: pd.DataFrame, rate_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    """Classify each row using trailing/expanding values available on that row only."""
    result = pd.DataFrame(index=frame.index)
    market_return = pd.to_numeric(frame.get("market_return_60"), errors="coerce")
    market_drawdown = pd.to_numeric(frame.get("market_drawdown"), errors="coerce")
    conditions = [
        (market_drawdown <= -0.25) | (market_return <= -0.15),
        market_return <= -0.10,
        market_return <= -0.03,
        market_return < 0.03,
        market_return < 0.15,
    ]
    result["market_trend"] = np.select(
        conditions, ["crisis", "strong_bear", "weak_bear", "sideways", "weak_bull"],
        default="strong_bull",
    )
    result.loc[market_return.isna(), "market_trend"] = "insufficient"

    volatility = pd.to_numeric(frame.get("market_volatility_20"), errors="coerce")
    q25 = volatility.expanding(min_periods=60).quantile(0.25)
    q75 = volatility.expanding(min_periods=60).quantile(0.75)
    q90 = volatility.expanding(min_periods=60).quantile(0.90)
    result["volatility_regime"] = np.select(
        [volatility >= q90, volatility >= q75, volatility <= q25],
        ["extreme", "elevated", "low"],
        default="normal",
    )
    result.loc[volatility.isna() | q25.isna(), "volatility_regime"] = "insufficient"

    available_rates = [name for name in rate_columns if name in frame]
    if available_rates:
        level = frame[available_rates].astype(float).median(axis=1, skipna=True)
        rate_change = level - level.shift(20)
        scale = level.expanding(min_periods=60).std().replace(0, np.nan)
        meaningful = scale * 0.10
        high = level > level.expanding(min_periods=60).quantile(0.75)
        result["rates_regime"] = np.select(
            [rate_change < -meaningful, rate_change > meaningful, high],
            ["easing", "tightening", "stable_high"],
            default="stable_normal",
        )
        result.loc[level.isna() | meaningful.isna(), "rates_regime"] = "insufficient"
    else:
        result["rates_regime"] = "insufficient"

    ret20 = pd.to_numeric(frame.get("return_20"), errors="coerce")
    ret60 = pd.to_numeric(frame.get("return_60"), errors="coerce")
    drawdown = pd.to_numeric(frame.get("drawdown"), errors="coerce")
    sma20 = pd.to_numeric(frame.get("sma20_distance"), errors="coerce")
    result["stock_state"] = np.select(
        [
            (drawdown <= -0.35) & (ret20 <= -0.12),
            ret60 <= -0.10,
            ret20 <= -0.05,
            (ret20 >= 0.08) & (drawdown <= -0.08),
            (ret20 >= 0.10) & (sma20 > 0),
            ret20 >= 0.03,
            (ret20 > 0) & (sma20 <= 0),
        ],
        ["capitulation", "downtrend", "correction", "recovery", "breakout", "uptrend", "accumulation"],
        default="sideways",
    )
    result.loc[ret20.isna() | ret60.isna() | drawdown.isna(), "stock_state"] = "insufficient"
    return result


def _state(row: pd.Series) -> dict[str, str]:
    return {
        name: str(row[name])
        for name in ("market_trend", "volatility_regime", "rates_regime", "stock_state")
    }


def build_regime_conditioning(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    config, signature = _config()
    source = con.execute(
        """SELECT run_id,cutoff FROM conditional_similarity_runs
        WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed conditional similarity run is required")
    similarity_run_id, cutoff = source
    run_id = hashlib.sha256(f"{VERSION}|{similarity_run_id}|{signature}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT timeline_rows,analog_rows,status FROM conditional_regime_runs WHERE run_id=?", [run_id]
    ).fetchone()
    if existing:
        return {
            "run_id": run_id, "timeline_rows": existing[0], "analog_rows": existing[1],
            "status": existing[2], "idempotent": True,
        }
    timelines: dict[str, pd.DataFrame] = {}
    timeline_rows: list[list[Any]] = []
    analog_rows: list[list[Any]] = []
    transition_rows: list[list[Any]] = []
    for secid in SECIDS:
        frame, families = build_state_panel(con, secid, cutoff)
        if frame.empty:
            continue
        regimes = classify_regimes(frame, families["rates"])
        timelines[secid] = regimes
        for trade_date, regime in regimes.iterrows():
            evidence = {
                "market_return_60": frame.loc[trade_date].get("market_return_60"),
                "market_drawdown": frame.loc[trade_date].get("market_drawdown"),
                "market_volatility_20": frame.loc[trade_date].get("market_volatility_20"),
                "stock_return_20": frame.loc[trade_date].get("return_20"),
                "stock_drawdown": frame.loc[trade_date].get("drawdown"),
            }
            timeline_rows.append([
                run_id, secid, trade_date.date(), regime.market_trend,
                regime.volatility_regime, regime.rates_regime, regime.stock_state,
                json.dumps(evidence, default=str), trade_date.date(), True,
            ])
        current = _state(regimes.iloc[-1])
        analogs = con.execute(
            """SELECT analog_date,episode_id,eligibility FROM conditional_analog_diagnostics
            WHERE run_id=? AND secid=? AND eligibility<>'REJECTED'
            QUALIFY row_number() OVER
            (PARTITION BY episode_id ORDER BY total_similarity DESC,analog_date)=1
            ORDER BY analog_date""",
            [similarity_run_id, secid],
        ).fetchall()
        for analog_date, episode_id, eligibility in analogs:
            timestamp = pd.Timestamp(analog_date)
            if timestamp not in regimes.index:
                continue
            historical = _state(regimes.loc[timestamp])
            compatibility = regime_compatibility(current, historical)
            if compatibility >= float(config["expected_compatibility"]):
                role, eligible, reason = "EXPECTED_CONDITIONAL", True, "multidimensional regime compatible"
            elif compatibility >= float(config["alternative_compatibility"]):
                role, eligible, reason = "ALTERNATIVE_PLAUSIBLE", False, "adjacent regime retained separately"
            else:
                role, eligible, reason = "STRESS", False, "incompatible or crisis regime retained as tail"
            analog_rows.append([
                run_id, secid, analog_date, episode_id, eligibility, compatibility,
                role, eligible, reason, True,
            ])
        for horizon in config["transition_horizons"]:
            matched, changed, crisis = 0, 0, 0
            for position in range(0, len(regimes) - int(horizon)):
                historical = _state(regimes.iloc[position])
                if regime_compatibility(current, historical) < float(config["expected_compatibility"]):
                    continue
                future = _state(regimes.iloc[position + int(horizon)])
                matched += 1
                changed += future != historical
                crisis += future["market_trend"] == "crisis"
            status = "historical_frequency_uncalibrated" if matched >= 20 else "insufficient_history"
            transition_rows.append([
                run_id, secid, horizon, matched, changed / matched if matched else None,
                crisis / matched if matched else None, status, True,
            ])
    if timeline_rows:
        con.executemany(
            """INSERT INTO conditional_regime_timeline
            (run_id,secid,trade_date,market_trend,volatility_regime,rates_regime,stock_state,
            evidence_json,history_end,immutable) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            timeline_rows,
        )
    if analog_rows:
        con.executemany(
            """INSERT INTO regime_conditioned_analogs
            (run_id,secid,analog_date,episode_id,similarity_eligibility,regime_compatibility,
            scenario_role,eligible_for_center,reason,immutable) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            analog_rows,
        )
    if transition_rows:
        con.executemany(
            """INSERT INTO conditional_regime_transitions
            (run_id,secid,horizon,matched_states,transition_frequency,crisis_frequency,status,immutable)
            VALUES (?,?,?,?,?,?,?,?)""",
            transition_rows,
        )
    details = {"dimensions": 4, "center_excludes_stress": True, "future_confirmed_labels": False}
    con.execute(
        """INSERT INTO conditional_regime_runs
        (run_id,created_at,cutoff,similarity_run_id,regime_version,config_signature,
        timeline_rows,analog_rows,immutable,production_unchanged,probability_gate_unchanged,
        status,details_json) VALUES (?,?,?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [run_id, datetime.now(UTC), cutoff, similarity_run_id, VERSION, signature,
         len(timeline_rows), len(analog_rows), json.dumps(details, sort_keys=True)],
    )
    return {
        "run_id": run_id, "timeline_rows": len(timeline_rows), "analog_rows": len(analog_rows),
        "status": "completed", "idempotent": False,
    }
