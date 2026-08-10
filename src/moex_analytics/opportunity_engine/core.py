"""Stage 56 transparent reward/downside map with Pareto comparisons and reserve."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "opportunity-downside-v2-frozen"


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def quadrant(opportunity: float, downside: float, opportunity_median: float,
             downside_median: float) -> str:
    high_opportunity = opportunity >= opportunity_median
    low_downside = downside <= downside_median
    if high_opportunity and low_downside:
        return "high_opportunity_low_downside"
    if high_opportunity:
        return "high_opportunity_high_downside"
    if low_downside:
        return "low_opportunity_low_downside"
    return "low_opportunity_high_downside"


def pareto_pairs(frame: pd.DataFrame) -> list[tuple[str, str, float, float]]:
    rows = []
    for left in frame.itertuples():
        for right in frame.itertuples():
            if left.secid == right.secid:
                continue
            return_better = left.expected_median >= right.expected_median
            risk_better = left.downside_axis <= right.downside_axis
            strict = (left.expected_median > right.expected_median or
                      left.downside_axis < right.downside_axis)
            if return_better and risk_better and strict:
                rows.append((left.secid, right.secid,
                             float(left.expected_median - right.expected_median),
                             float(right.downside_axis - left.downside_axis)))
    return rows


def _latest_action_map(con: Any) -> pd.DataFrame:
    exists = con.execute("SELECT count(*) FROM information_schema.tables "
                         "WHERE table_name='portfolio_action_map'").fetchone()[0]
    if not exists:
        return pd.DataFrame()
    snapshot = con.execute("SELECT snapshot_id FROM portfolio_action_map "
                           "ORDER BY snapshot_id DESC LIMIT 1").fetchone()
    if not snapshot:
        return pd.DataFrame()
    return con.execute("SELECT secid,equity_weight,risk_contribution,valuation_status,"
                       "fundamental_confidence,portfolio_fit FROM portfolio_action_map "
                       "WHERE snapshot_id=?", [snapshot[0]]).df()


def _reserve_carry(con: Any) -> tuple[float | None, str]:
    tables = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "macro_observations" not in tables:
        return None, "RUSFAR-like validated carry unavailable"
    columns = {row[0] for row in con.execute("DESCRIBE macro_observations").fetchall()}
    if not {"series_id", "value", "observation_date"} <= columns:
        return None, "RUSFAR-like validated carry unavailable"
    row = con.execute("SELECT value FROM macro_observations WHERE upper(series_id) LIKE '%RUSFAR%' "
                      "ORDER BY observation_date DESC LIMIT 1").fetchone()
    return (float(row[0]) / 100 if row else None,
            "latest validated RUSFAR-like annualized observation" if row else
            "RUSFAR-like validated carry unavailable")


def _insert(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    relation = f"_{table}"
    con.register(relation, frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {relation}")
    con.unregister(relation)


def run_opportunity_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    distribution = con.execute("SELECT run_id,cutoff FROM distribution_research_runs "
                               "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    ranking = con.execute("SELECT run_id FROM ranking_research_runs WHERE status='completed' "
                          "ORDER BY finished_at DESC LIMIT 1").fetchone()
    scenario = con.execute("SELECT run_id FROM scenario_research_runs WHERE status='completed' "
                           "ORDER BY finished_at DESC LIMIT 1").fetchone()
    timing = con.execute("SELECT run_id FROM timing_research_runs WHERE status='completed' "
                         "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not all((distribution, ranking, scenario, timing)):
        raise ValueError("completed Stages 52-55 are required")
    distribution_run, cutoff = distribution
    ranking_run, scenario_run, timing_run = ranking[0], scenario[0], timing[0]
    run_id = hashlib.sha256(
        f"{VERSION}|{distribution_run}|{ranking_run}|{scenario_run}|{timing_run}".encode()
    ).hexdigest()[:20]
    cached = con.execute("SELECT status,candidate_rows FROM opportunity_research_runs WHERE run_id=?",
                         [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "candidates": cached[1], "cached": True}
    con.execute("INSERT OR REPLACE INTO opportunity_research_runs "
        "(run_id,distribution_run_id,ranking_run_id,scenario_run_id,timing_run_id,cutoff,"
        "started_at,status,candidate_rows,details_json,immutable) "
        "VALUES (?,?,?,?,?,?,current_timestamp,'running',0,?,true)",
        [run_id, distribution_run, ranking_run, scenario_run, timing_run, cutoff,
         json.dumps({"magic_score": False, "production_changes": 0})])
    try:
        distributions = con.execute("SELECT secid,horizon,q50_return AS expected_median,"
            "q75_return AS upper_quartile,q25_return AS lower_quartile,q10_return AS tail_downside,"
            "status AS distribution_status FROM current_return_distributions WHERE run_id=?",
            [distribution_run]).df()
        ranks = con.execute("SELECT secid,horizon,relative_rank,rank_low,rank_high,historical_oos "
                            "FROM current_portfolio_ranking WHERE run_id=?", [ranking_run]).df()
        scenarios = con.execute("SELECT secid,horizon,applicability AS scenario_applicability,"
                                "status AS scenario_status FROM current_scenario_intelligence "
                                "WHERE run_id=?", [scenario_run]).df()
        timings = con.execute("SELECT secid,horizon,timing_status,timing_status AS timing_evidence "
                              "FROM current_timing_intelligence WHERE run_id=?", [timing_run]).df()
        data = distributions.merge(ranks, on=["secid", "horizon"], how="left")
        data = data.merge(scenarios, on=["secid", "horizon"], how="left")
        data = data.merge(timings, on=["secid", "horizon"], how="left")
        actions = _latest_action_map(con)
        if not actions.empty:
            data = data.merge(actions, on="secid", how="left")
        for column, default in (("fundamental_confidence", "insufficient_data"),
                                ("valuation_status", "insufficient_data"),
                                ("portfolio_fit", "unknown")):
            if column not in data:
                data[column] = default
            data[column] = data[column].fillna(default)
        for column in ("equity_weight", "risk_contribution"):
            if column not in data:
                data[column] = np.nan
        data["candidate_type"] = "equity"
        data["portfolio_weight"] = data.equity_weight
        data["opportunity_axis"] = data.relative_rank
        data["downside_axis"] = data.tail_downside.abs()
        data["evidence_quality"] = np.where(
            (data.distribution_status == "research_only") & data.historical_oos.notna(),
            "research_oos", "insufficient_data")
        data["evidence_opacity"] = np.where(data.evidence_quality == "research_oos", .75, .25)
        data["abstain"] = (data.evidence_quality != "research_oos") | data.relative_rank.isna()
        data["abstention_reason"] = np.where(data.abstain, "insufficient common frozen evidence", None)
        data["diversification_status"] = data.portfolio_fit
        data["cutoff"] = cutoff
        for _horizon, group in data.groupby("horizon"):
            opportunity_median = float(group.opportunity_axis.median())
            downside_median = float(group.downside_axis.median())
            data.loc[group.index, "quadrant"] = [
                quadrant(row.opportunity_axis, row.downside_axis, opportunity_median, downside_median)
                for row in group.itertuples()
            ]
        carry, carry_reason = _reserve_carry(con)
        reserve_rows = []
        for horizon in sorted(data.horizon.unique()):
            horizon_return = carry * horizon / 250 if carry is not None else None
            reserve_rows.append({"secid": "CASH", "horizon": int(horizon),
                "expected_median": horizon_return, "upper_quartile": horizon_return,
                "lower_quartile": horizon_return, "tail_downside": 0.0, "relative_rank": None,
                "rank_low": None, "rank_high": None, "timing_status": "available_now",
                "timing_evidence": "carry_only", "scenario_applicability": "not_applicable",
                "fundamental_confidence": "not_applicable", "valuation_status": "not_applicable",
                "portfolio_weight": None, "risk_contribution": 0.0,
                "diversification_status": "reserve", "candidate_type": "cash_reserve",
                "opportunity_axis": horizon_return, "downside_axis": 0.0,
                "quadrant": "reserve", "evidence_quality": "validated_carry" if carry else
                "insufficient_data", "evidence_opacity": .75 if carry else .25,
                "abstain": carry is None, "abstention_reason": None if carry else carry_reason,
                "cutoff": cutoff})
        data = pd.concat([data.drop(columns=["equity_weight"]), pd.DataFrame(reserve_rows)],
                         ignore_index=True)
        data.insert(0, "run_id", run_id)
        data["immutable"] = True
        candidate_columns = ("run_id", "cutoff", "secid", "horizon", "candidate_type",
            "expected_median", "upper_quartile", "lower_quartile", "tail_downside",
            "relative_rank", "rank_low", "rank_high", "timing_status", "timing_evidence",
            "scenario_applicability", "fundamental_confidence", "valuation_status",
            "portfolio_weight", "risk_contribution", "diversification_status", "opportunity_axis",
            "downside_axis", "quadrant", "evidence_quality", "evidence_opacity", "abstain",
            "abstention_reason", "immutable")
        candidates = data[list(candidate_columns)]
        dominance_rows = []
        for horizon, group in candidates.dropna(subset=["expected_median"]).groupby("horizon"):
            for dominant, dominated, return_advantage, downside_advantage in pareto_pairs(group):
                dominance_rows.append([run_id, cutoff, int(horizon), dominant, dominated,
                    return_advantage, downside_advantage, "historical/model dominance",
                    True, True])
        dominance = pd.DataFrame(dominance_rows, columns=("run_id", "cutoff", "horizon",
            "dominant_secid", "dominated_secid", "expected_return_advantage",
            "downside_advantage", "label", "research_only", "immutable"))
        _insert(con, "opportunity_candidates", candidates)
        _insert(con, "opportunity_pareto_dominance", dominance)
        details = {"magic_score": False, "axes": ["opportunity", "downside"],
                   "cash_carry": carry, "cash_reason": carry_reason, "automatic_promotion": False,
                   "probability_published": False, "production_changes": 0}
        con.execute("UPDATE opportunity_research_runs SET finished_at=current_timestamp,"
                    "status='completed',candidate_rows=?,details_json=? WHERE run_id=?",
                    [len(candidates), json.dumps(details), run_id])
        return {"run_id": run_id, "status": "completed", "candidates": len(candidates),
                "dominance_pairs": len(dominance), "cached": False}
    except Exception as exc:
        con.execute("UPDATE opportunity_research_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def opportunity_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,candidate_rows,details_json "
                      "FROM opportunity_research_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "candidates", "details"), row, strict=True))
