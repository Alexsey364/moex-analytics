"""Stage 62 uncertainty-aware grouping without directional probabilities."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

VERSION = "uncertainty-aware-rank-groups-v2"
HORIZONS = (60, 120, 250)
LABELS = ("BOTTOM GROUP", "LOWER-MIDDLE", "MIDDLE", "UPPER-MIDDLE", "TOP GROUP")

DDL = """
CREATE TABLE IF NOT EXISTS rank_group_runs(
 run_id VARCHAR PRIMARY KEY,source_run_id VARCHAR,cutoff DATE,created_at TIMESTAMP,
 status VARCHAR,rows BIGINT,details_json JSON,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS current_rank_groups(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,horizon INTEGER,rank_estimate DOUBLE,
 rank_low DOUBLE,rank_high DOUBLE,overlap_group INTEGER,group_label VARCHAR,
 bootstrap_top_frequency DOUBLE,bootstrap_bottom_frequency DOUBLE,
 relative_conviction VARCHAR,historical_rank_ic DOUBLE,historical_group_spread DOUBLE,
 evidence_status VARCHAR,method VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid,horizon));
CREATE TABLE IF NOT EXISTS composite_rank_groups(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,group_label VARCHAR,relative_conviction VARCHAR,
 decomposition_json JSON,reason VARCHAR,immutable BOOLEAN,PRIMARY KEY(run_id,secid));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _overlap_groups(frame: pd.DataFrame) -> dict[str, int]:
    ordered = frame.sort_values(["rank_low", "rank_high"], ascending=False)
    result: dict[str, int] = {}
    group, floor = 0, float("inf")
    for row in ordered.itertuples():
        if row.rank_high < floor:
            group += 1
            floor = float(row.rank_low)
        else:
            floor = min(floor, float(row.rank_low))
        result[row.secid] = group
    return result


def _label(center: float) -> str:
    return LABELS[min(4, max(0, int(center * 5)))]


def _bootstrap_frequencies(row: Any, horizon: int, draws: int = 2000) -> tuple[float, float]:
    # Parametric bootstrap of the persisted ensemble-rank dispersion. It is a
    # historical resampling frequency, never a probability of future return.
    sigma = max(.005, (float(row.rank_high) - float(row.rank_low)) / 3.92)
    rng = np.random.default_rng(6200 + horizon + sum(map(ord, row.secid)))
    values = np.clip(rng.normal(float(row.relative_rank), sigma, draws), 0, 1)
    return float(np.mean(values >= .8)), float(np.mean(values <= .2))


def _conviction(row: Any, rank_ic: float, spread: float) -> str:
    width = float(row.rank_high) - float(row.rank_low)
    if width <= .25 and row.model_agreement >= .8 and rank_ic > .08 and spread > 0:
        return "higher"
    if width <= .50 and rank_ic > 0 and spread > 0:
        return "moderate"
    return "low"


def run_rank_grouping(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id,cutoff FROM ranking_research_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    validation = con.execute(
        "SELECT run_id FROM long_horizon_ranking_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not source or not validation:
        raise ValueError("completed ranking and Stage 61 validation required")
    source_run, cutoff = source
    run_id = hashlib.sha256(f"{VERSION}|{source_run}|{validation[0]}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,rows FROM rank_group_runs WHERE run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "rows": cached[1], "cached": True}
    current = con.execute(
        "SELECT * FROM current_portfolio_ranking WHERE run_id=? AND horizon IN (60,120,250)",
        [source_run],
    ).df()
    metrics = con.execute(
        "SELECT horizon,rank_ic,top_bottom_spread_after_costs,status FROM "
        "long_horizon_ranking_validation WHERE run_id=? AND context_type='all'",
        [validation[0]],
    ).df().set_index("horizon")
    if current.empty:
        raise ValueError("current portfolio ranking required")
    con.execute("INSERT INTO rank_group_runs (run_id,source_run_id,cutoff,created_at,status,rows,"
                "details_json,immutable) VALUES (?,?,?,current_timestamp,'running',0,?,true)",
                [run_id, source_run, cutoff, json.dumps({"probability_published": False})])
    rows = []
    for horizon in HORIZONS:
        sample = current[current.horizon == horizon].copy()
        groups = _overlap_groups(sample)
        group_centers = sample.assign(
            overlap_group=sample.secid.map(groups)
        ).groupby("overlap_group").relative_rank.mean().to_dict()
        for row in sample.itertuples():
            metric = metrics.loc[horizon]
            top, bottom = _bootstrap_frequencies(row, horizon)
            rows.append([run_id, cutoff, row.secid, horizon, row.relative_rank, row.rank_low,
                row.rank_high, groups[row.secid], _label(group_centers[groups[row.secid]]), top,
                bottom,
                _conviction(row, metric.rank_ic, metric.top_bottom_spread_after_costs),
                metric.rank_ic, metric.top_bottom_spread_after_costs, metric.status,
                "parametric_bootstrap_of_ensemble_rank_dispersion", True])
    grouped = pd.DataFrame(rows, columns=("run_id", "cutoff", "secid", "horizon",
        "rank_estimate", "rank_low", "rank_high", "overlap_group", "group_label",
        "bootstrap_top_frequency", "bootstrap_bottom_frequency", "relative_conviction",
        "historical_rank_ic", "historical_group_spread", "evidence_status", "method", "immutable"))
    composite = []
    for secid, sample in grouped.groupby("secid"):
        decomposition = {str(int(row.horizon)): {"group": row.group_label,
            "conviction": row.relative_conviction, "rank": round(row.rank_estimate, 4)}
            for row in sample.itertuples()}
        weighted = float(np.average(sample.rank_estimate, weights=[.45, .35, .20]))
        convictions = "higher" if (sample.relative_conviction == "higher").sum() >= 2 else (
            "moderate" if (sample.relative_conviction != "low").sum() >= 2 else "low")
        composite.append([run_id, cutoff, secid, _label(weighted), convictions,
            json.dumps(decomposition), "horizons retained separately; weighted summary is secondary", True])
    composite_frame = pd.DataFrame(composite, columns=("run_id", "cutoff", "secid",
        "group_label", "relative_conviction", "decomposition_json", "reason", "immutable"))
    for table, frame in (("current_rank_groups", grouped), ("composite_rank_groups", composite_frame)):
        con.register("_rank_group_frame", frame)
        fields = ",".join(frame.columns)
        con.execute(f"INSERT INTO {table} ({fields}) SELECT {fields} FROM _rank_group_frame")
        con.unregister("_rank_group_frame")
    con.execute("UPDATE rank_group_runs SET status='completed',rows=?,details_json=? WHERE run_id=?",
                [len(grouped), json.dumps({"current_stocks": grouped.secid.nunique(),
                    "horizons": list(HORIZONS), "probability_published": False,
                    "production_changes": 0}), run_id])
    return {"run_id": run_id, "status": "completed", "rows": len(grouped), "cached": False}


def rank_group_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,rows,details_json FROM rank_group_runs "
                      "ORDER BY created_at DESC LIMIT 1").fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "cutoff", "rows", "details"), row, strict=True
    ))
