"""Stage 89 descriptive scenario branches from observed historical paths."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

VERSION = "market-portfolio-scenario-tree-v1"
HORIZON = 60


def _scenario(path: np.ndarray) -> tuple[str, str]:
    terminal, low = float(path[-1]), float(path.min())
    if terminal >= 0.12 and low > -0.08:
        return "strong_rebound", "Сильное восстановление"
    if low <= -0.10 and terminal > 0:
        return "stress_recovery", "Восстановление после стресса"
    if terminal <= -0.08:
        return "renewed_decline", "Новая просадка"
    return "sideways_stabilization", "Боковая стабилизация"


def _episodes_text(count: int, total: int) -> str:
    suffix = (
        "эпизод"
        if count % 10 == 1 and count % 100 != 11
        else ("эпизода" if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14} else "эпизодов")
    )
    return f"{count} исторических {suffix} из {total}"


def _series(con: Any, secid: str, cutoff: Any) -> pd.Series:
    frame = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? "
        "AND trade_date<=? AND close>0 ORDER BY trade_date",
        [secid, cutoff],
    ).df()
    return pd.Series(frame.close.to_numpy(float), index=pd.to_datetime(frame.trade_date))


def _independent_dates(con: Any, state_run: str) -> list[pd.Timestamp]:
    rows = con.execute(
        """SELECT analog_date,count(*) support,avg(distance) distance
        FROM state_similarity_matches WHERE run_id=? AND analog_type='state' AND independent
        GROUP BY analog_date ORDER BY support DESC,distance LIMIT 100""",
        [state_run],
    ).fetchall()
    selected: list[pd.Timestamp] = []
    for value, _support, _distance in rows:
        candidate = pd.Timestamp(value)
        if all(abs((candidate - prior).days) >= 60 for prior in selected):
            selected.append(candidate)
        if len(selected) == 30:
            break
    return sorted(selected)


def _root(con: Any, cutoff: Any) -> tuple[Any, ...]:
    row = con.execute(
        """SELECT market_state_label,breadth_json,rates_json,fx_json,commodities_json,
        volatility_json FROM whole_market_state_daily WHERE trade_date=?
        ORDER BY available_from DESC LIMIT 1""",
        [cutoff],
    ).fetchone()
    if not row:
        return ("unknown", "{}", "{}", "{}", "{}", "{}")
    return row


def _news_overlay(con: Any, cutoff: Any) -> list[dict[str, Any]]:
    try:
        rows = con.execute(
            """SELECT headline,event_type,reliability,last_update_at FROM news_stories
            WHERE status='active' AND first_report_at<=? ORDER BY last_update_at DESC LIMIT 5""",
            [str(cutoff) + " 23:59:59"],
        ).fetchall()
    except Exception:
        rows = []
    return [
        {"headline": row[0], "event_type": row[1], "reliability": row[2], "updated_at": str(row[3])}
        for row in rows
    ]


def build_portfolio_scenario_tree(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id,cutoff FROM state_similarity_runs WHERE status='completed' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise ValueError("completed state similarity run is required")
    state_run, cutoff = source
    dates = _independent_dates(con, state_run)
    run_id = hashlib.sha256(f"{VERSION}|{state_run}|{cutoff}|{dates}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM portfolio_scenario_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    market = _series(con, "IMOEX", cutoff)
    portfolio = {secid: _series(con, secid, cutoff) for secid in SECIDS}
    episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    path_rows = []
    for analog_date in dates:
        if analog_date not in market.index:
            continue
        location = market.index.get_loc(analog_date)
        future = market.iloc[location + 1 : location + HORIZON + 1]
        if len(future) < HORIZON:
            continue
        normalized = future.to_numpy(float) / float(market.loc[analog_date])
        path = normalized - 1
        branch_id, label = _scenario(path)
        episode = {
            "date": analog_date,
            "label": label,
            "return": float(path[-1]),
            "drawdown": float(path.min()),
            "normalized": normalized,
            "source_dates": future.index,
        }
        episodes[branch_id].append(episode)
        for session, (source_date, value) in enumerate(zip(future.index, normalized, strict=True), 1):
            path_rows.append(
                [run_id, branch_id, analog_date, session, source_date, float(value * 100), True, True]
            )
    total = sum(len(group) for group in episodes.values())
    branch_rows, sensitivity_rows = [], []
    for branch_id, group in episodes.items():
        representative = min(
            group, key=lambda row: abs(row["return"] - np.median([x["return"] for x in group]))
        )
        returns = [row["return"] for row in group]
        drawdowns = [row["drawdown"] for row in group]
        branch_rows.append(
            [
                run_id,
                branch_id,
                group[0]["label"],
                len(group),
                total,
                float(np.median(returns)),
                float(np.median(drawdowns)),
                None,
                None,
                None,
                json.dumps({}),
                representative["date"],
                _episodes_text(len(group), total),
                True,
            ]
        )
        for secid, prices in portfolio.items():
            stock_returns, relative, stock_drawdowns = [], [], []
            for episode in group:
                if episode["date"] not in prices.index:
                    continue
                location = prices.index.get_loc(episode["date"])
                future = prices.iloc[location + 1 : location + HORIZON + 1]
                if len(future) < HORIZON:
                    continue
                path = future.to_numpy(float) / float(prices.loc[episode["date"]]) - 1
                stock_returns.append(float(path[-1]))
                relative.append(float(path[-1] - episode["return"]))
                stock_drawdowns.append(float(path.min()))
            if stock_returns:
                relative_median = float(np.median(relative))
                resilience = (
                    "лучше рынка"
                    if relative_median > 0.02
                    else "хуже рынка"
                    if relative_median < -0.02
                    else "около рынка"
                )
                sensitivity_rows.append(
                    [
                        run_id,
                        branch_id,
                        secid,
                        len(stock_returns),
                        float(np.median(stock_returns)),
                        relative_median,
                        float(np.median(stock_drawdowns)),
                        resilience,
                        True,
                    ]
                )
    root = _root(con, cutoff)
    news = _news_overlay(con, cutoff)
    con.execute(
        "INSERT INTO portfolio_scenario_roots VALUES (?,?,?,?,?,?,?,?,?,FALSE,TRUE)",
        [run_id, cutoff, root[0], root[1], root[2], root[3], root[4], root[5], json.dumps(news)],
    )
    con.executemany(
        "INSERT INTO portfolio_scenario_branches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", branch_rows
    )
    con.executemany("INSERT INTO portfolio_scenario_paths VALUES (?,?,?,?,?,?,?,?)", path_rows)
    con.executemany(
        "INSERT INTO portfolio_scenario_sensitivities VALUES (?,?,?,?,?,?,?,?,?)", sensitivity_rows
    )
    con.execute(
        "INSERT INTO portfolio_scenario_runs VALUES (?,?,?,?,?,?,?,?,TRUE,TRUE,'completed',?)",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            state_run,
            total,
            len(branch_rows),
            VERSION,
            True,
            json.dumps({"historical_frequency_only": True, "news_weights_changed": False}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: Any, run_id: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT cutoff,episodes,branches,status FROM portfolio_scenario_runs WHERE run_id=?",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "cutoff": row[0],
        "episodes": row[1],
        "branches": row[2],
        "status": row[3],
        "production_changes": 0,
        "probability_gate_changed": False,
    }
