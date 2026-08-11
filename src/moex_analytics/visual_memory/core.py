"""Visual Historical Memory 5.0 using only pre-T0 matching and real historical futures."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS

from .schema import ensure_schema

VERSION = "visual-memory-v5.1"
MODES = {
    "price_path": ("path_cosine", 20),
    "stock_market": ("cosine", 60),
    "full_state": ("mahalanobis", 120),
}
MIN_ANALOGS = 5


def normalize_t0(values: list[float]) -> list[float]:
    if not values or not np.isfinite(values[-1]) or values[-1] == 0:
        return []
    return [float(value / values[-1] * 100) for value in values]


def similarity_label(score: float | None) -> str:
    if score is None:
        return "Сходство не рассчитано"
    if score >= 0.85:
        return "Очень похоже"
    if score >= 0.70:
        return "Похоже"
    if score >= 0.50:
        return "Среднее сходство"
    return "Слабое сходство"


def _price_map(con: duckdb.DuckDBPyConnection, secid: str) -> dict[Any, float]:
    return dict(
        con.execute(
            """SELECT trade_date,close FROM canonical_daily_prices
            WHERE canonical_secid=? AND close>0 ORDER BY trade_date""",
            [secid],
        ).fetchall()
    )


def _current_path(prices: dict[Any, float], cutoff: Any, window: int) -> list[dict[str, Any]]:
    dates = [date for date in prices if date <= cutoff][-window - 1 :]
    normalized = normalize_t0([prices[date] for date in dates])
    return [
        {
            "relative_session": index - len(dates) + 1,
            "date": str(date),
            "normalized": normalized[index],
            "real_price": prices[date],
            "observed": True,
        }
        for index, date in enumerate(dates)
    ]


def _scenario_name(value: str) -> str:
    return {
        "growth_without_deep_drawdown": "Рост / продолжение движения",
        "sideways": "Боковое движение",
        "dip_then_recover": "Просадка с восстановлением",  # noqa: RUF001
        "continued_decline": "Продолжение снижения",
        "volatile_mixed": "Смешанная волатильная траектория",
    }.get(value, value)


def build_visual_memory(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        """SELECT run_id,analog_run_id,trajectory_run_id,cutoff FROM scenario_research_runs
        WHERE status='completed' ORDER BY finished_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed scenario research is required")
    scenario_run, _analog_run, trajectory_run, cutoff = source
    run_id = hashlib.sha256(f"{VERSION}|{scenario_run}|{cutoff}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM visual_memory_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    snapshots = []
    for secid in SECIDS:
        prices = _price_map(con, secid)
        for horizon in HORIZONS:
            for mode, (method, window) in MODES.items():
                current = _current_path(prices, cutoff, window)
                matches = con.execute(
                    """SELECT analog_date,similarity_score,regime_agreement,event_agreement,
                    applicability,gaps_json FROM scenario_multiscale_matches
                    WHERE run_id=? AND secid=? AND method=? AND independent
                    ORDER BY combined_distance,analog_date LIMIT 10""",
                    [scenario_run, secid, method],
                ).fetchall()
                analog_paths, cards = [], []
                for rank, match in enumerate(matches, start=1):
                    analog_date, score, regime, event, applicability, gaps = match
                    pre = con.execute(
                        """SELECT relative_session,source_trade_date,normalized_value
                        FROM scenario_prehistory_points WHERE run_id=? AND secid=? AND method=?
                        AND analog_date=? AND series_type='issuer' AND path_window=?
                        ORDER BY relative_session""",
                        [scenario_run, secid, method, analog_date, window],
                    ).fetchall()
                    trajectory_window = window if method == "path_cosine" else 0
                    future = con.execute(
                        """SELECT forward_session,source_trade_date,normalized_price,forward_return
                        FROM analog_forward_trajectories WHERE run_id=? AND secid=? AND method=?
                        AND analog_date=? AND path_window=? AND forward_session<=?
                        ORDER BY forward_session""",
                        [trajectory_run, secid, method, analog_date, trajectory_window, horizon],
                    ).fetchall()
                    if not pre or len(future) < horizon:
                        continue
                    points = [
                        {
                            "relative_session": row[0],
                            "date": str(row[1]),
                            "normalized": row[2],
                            "real_price": prices.get(row[1]),
                            "phase": "До T0",
                        }
                        for row in pre
                    ] + [
                        {
                            "relative_session": row[0],
                            "date": str(row[1]),
                            "normalized": row[2],
                            "real_price": prices.get(row[1]),
                            "phase": "После T0",
                        }
                        for row in future
                    ]
                    analog_paths.append(
                        {"date": str(analog_date), "rank": rank, "similarity": score, "points": points}
                    )
                    outcomes = dict(
                        con.execute(
                            """SELECT horizon,terminal_return FROM scenario_episodes
                        WHERE run_id=? AND secid=? AND method=? AND analog_date=?""",
                            [scenario_run, secid, method, analog_date],
                        ).fetchall()
                    )
                    episode = con.execute(
                        """SELECT scenario,max_adverse,max_favorable FROM scenario_episodes
                        WHERE run_id=? AND secid=? AND method=? AND analog_date=? AND horizon=?""",
                        [scenario_run, secid, method, analog_date, horizon],
                    ).fetchone()
                    cards.append(
                        {
                            "date": str(analog_date),
                            "rank": rank,
                            "similarity": score,
                            "similarity_label": similarity_label(score),
                            "regime_similar": regime,
                            "event_similar": event,
                            "applicability": applicability,
                            "gaps": json.loads(gaps or "[]"),
                            "returns": outcomes,
                            "scenario": _scenario_name(episode[0]) if episode else None,
                            "max_drawdown": episode[1] if episode else None,
                            "max_favorable": episode[2] if episode else None,
                        }
                    )
                sample = len(analog_paths)
                by_session: dict[int, list[float]] = {}
                for path in analog_paths:
                    for point in path["points"]:
                        if point["relative_session"] > 0:
                            by_session.setdefault(point["relative_session"], []).append(point["normalized"])
                bands = []
                for session, values in sorted(by_session.items()):
                    if len(values) >= MIN_ANALOGS:
                        q10, q25, median, q75, q90 = np.quantile(values, [0.1, 0.25, 0.5, 0.75, 0.9])
                        bands.append(
                            {
                                "relative_session": session,
                                "q10": q10,
                                "q25": q25,
                                "median": median,
                                "q75": q75,
                                "q90": q90,
                            }
                        )
                terminal = [path["points"][-1]["normalized"] / 100 - 1 for path in analog_paths]
                summary = {
                    "analogs": sample,
                    "above": sum(value > 0 for value in terminal),
                    "below": sum(value < 0 for value in terminal),
                    "median": float(np.median(terminal)) if terminal else None,
                    "q25": float(np.quantile(terminal, 0.25)) if terminal else None,
                    "q75": float(np.quantile(terminal, 0.75)) if terminal else None,
                    "q10": float(np.quantile(terminal, 0.1)) if terminal else None,
                    "q90": float(np.quantile(terminal, 0.9)) if terminal else None,
                    "median_drawdown": float(
                        np.median(
                            [card["max_drawdown"] for card in cards if card["max_drawdown"] is not None]
                        )
                    )
                    if cards
                    else None,
                }
                scenario_rows = con.execute(
                    """SELECT scenario,episodes,median_return,median_adverse,medoid_analog_date,
                    applicability,status FROM scenario_tree_summaries WHERE run_id=? AND secid=?
                    AND method=? AND horizon=? AND subset='all' ORDER BY episodes DESC""",
                    [scenario_run, secid, method, horizon],
                ).fetchall()
                scenarios = [
                    {
                        "scenario": _scenario_name(row[0]),
                        "episodes": row[1],
                        "median_return": row[2],
                        "median_drawdown": row[3],
                        "representative_date": str(row[4]),
                        "applicability": row[5],
                        "status": row[6],
                    }
                    for row in scenario_rows
                ]
                why = {
                    "Акция": "похоже",
                    "IMOEX": "похоже" if mode != "price_path" else "не использовалось",
                    "Сектор": "частично" if mode != "price_path" else "не использовалось",
                    "Ширина рынка": "частично" if mode == "full_state" else "не использовалось",
                    "Волатильность": "похоже" if mode == "full_state" else "не использовалось",
                    "Ставки": "частично" if mode == "full_state" else "не использовалось",
                    "Рубль": "частично" if mode == "full_state" else "не использовалось",
                    "Нефть / сырьё": "частично" if mode == "full_state" else "не использовалось",
                    "Режим": "похоже" if mode == "full_state" else "не использовалось",
                    "Новости / события": "частично" if mode == "full_state" else "не использовалось",
                }
                status = "ready" if sample >= MIN_ANALOGS else "insufficient_history"
                reason = (
                    "real independent historical episodes"
                    if status == "ready"
                    else "too few independent analogs"
                )
                snapshots.append(
                    [
                        run_id,
                        secid,
                        horizon,
                        mode,
                        method,
                        window,
                        cutoff,
                        sample,
                        status,
                        reason,
                        json.dumps(current),
                        json.dumps(analog_paths),
                        json.dumps(bands),
                        json.dumps(cards, default=str),
                        json.dumps(summary),
                        json.dumps(why),
                        json.dumps(scenarios, default=str),
                        True,
                    ]
                )
    con.executemany(
        """INSERT INTO visual_memory_snapshots (
        run_id,instrument,horizon,comparison_mode,method,prehistory_window,cutoff,sample,
        status,reason,current_path_json,analog_paths_json,bands_json,cards_json,
        summary_json,why_json,scenarios_json,immutable
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        snapshots,
    )
    con.execute(
        """INSERT INTO visual_memory_runs (
        run_id,created_at,cutoff,scenario_run_id,snapshots,methodology_version,
        production_unchanged,probability_gate_unchanged,immutable,status,details_json
        ) VALUES (?,?,?,?,?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            scenario_run,
            len(snapshots),
            VERSION,
            json.dumps(
                {
                    "matching_uses_pre_t0_only": True,
                    "current_future_path": False,
                    "normalization": "T0=100",
                    "synthetic_paths": False,
                }
            ),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        """SELECT count(*),sum(status='ready'),sum(status='insufficient_history')
        FROM visual_memory_snapshots WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "snapshots": row[0],
        "ready": row[1],
        "insufficient": row[2],
        "status": "completed",
        "production_changes": 0,
        "probability_gate_changed": False,
    }
