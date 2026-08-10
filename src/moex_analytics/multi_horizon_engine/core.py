"""Stage 57 horizon experts, empirical feature gates and term-structure synthesis."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from moex_analytics.ranking_engine.core import FEATURES, _feature_panel, _mean_rank_ic

from .schema import DDL

VERSION = "multi-horizon-specialists-v2-validation-gate"
HORIZONS = (5, 20, 60, 120, 250)
FAMILIES = {
    "momentum": ("momentum_5", "momentum_20", "momentum_60", "momentum_120",
                 "momentum_20_pct"),
    "volatility_drawdown": ("volatility_20", "volatility_60", "drawdown_60",
                            "volatility_20_pct", "drawdown_60_pct"),
    "relative": ("relative_20", "relative_20_pct"),
    "liquidity": ("liquidity_proxy", "liquidity_pct"),
}
UNAVAILABLE = ("breadth", "sector", "macro_rates", "fundamental_valuation", "dividend")


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def expert_for_horizon(horizon: int) -> str:
    if horizon <= 20:
        return "short_horizon_expert"
    if horizon <= 60:
        return "medium_horizon_expert"
    return "long_horizon_expert"


def interpretation(short_return: float | None, long_return: float | None) -> str:
    if short_return is None or long_return is None:
        return "insufficient_data"
    if short_return < 0 < long_return:
        return "long_term_interesting_short_term_timing_weak"
    if short_return > 0 > long_return:
        return "short_term_strength_long_term_risk"
    if short_return > 0 and long_return > 0:
        return "positive_across_horizons"
    if short_return < 0 and long_return < 0:
        return "negative_across_horizons"
    return "mixed_or_neutral"


def _rank_ic(model: Any, frame: pd.DataFrame, features: list[str]) -> float:
    predicted = frame[["trade_date", "actual_rank"]].copy()
    predicted["predicted_score"] = model.predict(frame[features])
    return _mean_rank_ic(predicted)


def _fit(train: pd.DataFrame, features: list[str]) -> Any:
    model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    model.fit(train[features], train.actual_rank)
    return model


def _insert(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    relation = f"_{table}"
    con.register(relation, frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {relation}")
    con.unregister(relation)


def run_multi_horizon_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    ranking = con.execute("SELECT run_id,target_run_id,cutoff,train_end,validation_end,holdout_start "
                          "FROM ranking_research_runs WHERE status='completed' "
                          "ORDER BY finished_at DESC LIMIT 1").fetchone()
    opportunity = con.execute("SELECT run_id FROM opportunity_research_runs WHERE status='completed' "
                              "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not ranking or not opportunity:
        raise ValueError("completed Stages 52 and 56 are required")
    ranking_run, target_run, cutoff, train_end, validation_end, holdout_start = ranking
    opportunity_run = opportunity[0]
    run_id = hashlib.sha256(
        f"{VERSION}|{ranking_run}|{opportunity_run}|{target_run}".encode()
    ).hexdigest()[:20]
    cached = con.execute("SELECT status,ablation_rows,current_rows FROM multi_horizon_runs "
                         "WHERE run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "ablations": cached[1],
                "current_rows": cached[2], "cached": True}
    con.execute("INSERT OR REPLACE INTO multi_horizon_runs "
        "(run_id,ranking_run_id,opportunity_run_id,cutoff,started_at,status,ablation_rows,"
        "current_rows,details_json,immutable) VALUES (?,?,?,?,current_timestamp,'running',0,0,?,true)",
        [run_id, ranking_run, opportunity_run, cutoff,
         json.dumps({"horizon_gate_uses_future": False, "production_changes": 0})])
    try:
        features = _feature_panel(con)
        labels = con.execute("SELECT trade_date,exit_date,secid,horizon,percentile_rank AS actual_rank "
                             "FROM predictive_target_observations WHERE run_id=? AND secid<>'IMOEX'",
                             [target_run]).df()
        panel = features.merge(labels, on=["trade_date", "secid"], how="inner")
        ablations, policies = [], []
        for horizon in HORIZONS:
            data = panel[panel.horizon == horizon].dropna(subset=[*FEATURES, "actual_rank"])
            train = data[(data.trade_date <= pd.Timestamp(train_end)) &
                         (data.exit_date <= pd.Timestamp(train_end))]
            validation = data[(data.trade_date > pd.Timestamp(train_end)) &
                              (data.exit_date <= pd.Timestamp(validation_end))]
            development = data[(data.trade_date <= pd.Timestamp(validation_end)) &
                               (data.exit_date <= pd.Timestamp(validation_end))]
            holdout = data[data.trade_date >= pd.Timestamp(holdout_start)]
            full_validation_model = _fit(train, list(FEATURES))
            full_validation_ic = _rank_ic(full_validation_model, validation, list(FEATURES))
            full_holdout_model = _fit(development, list(FEATURES))
            full_holdout_ic = _rank_ic(full_holdout_model, holdout, list(FEATURES))
            selected_families = []
            for family, removed in FAMILIES.items():
                remaining = [feature for feature in FEATURES if feature not in removed]
                validation_model = _fit(train, remaining)
                validation_ablated = _rank_ic(validation_model, validation, remaining)
                holdout_model = _fit(development, remaining)
                holdout_ablated = _rank_ic(holdout_model, holdout, remaining)
                validation_contribution = full_validation_ic - validation_ablated
                holdout_contribution = full_holdout_ic - holdout_ablated
                validation_selected = validation_contribution > 0
                confirmed = validation_selected and holdout_contribution > 0
                if validation_selected:
                    selected_families.append(family)
                ablations.append([run_id, horizon, expert_for_horizon(horizon), family, True,
                    full_validation_ic, validation_ablated, validation_contribution,
                    full_holdout_ic, holdout_ablated, holdout_contribution,
                    "holdout_confirmed" if confirmed else (
                        "validation_selected_not_confirmed" if validation_selected else "rejected"
                    ),
                    "common sample validation gate with frozen-holdout diagnostic", True])
            for family in UNAVAILABLE:
                ablations.append([run_id, horizon, expert_for_horizon(horizon), family, False,
                    None, None, None, None, None, None, "insufficient_data",
                    "PIT feature family unavailable on common historical panel", True])
            policy_hash = hashlib.sha256(
                f"{run_id}|{horizon}|{expert_for_horizon(horizon)}|{selected_families}".encode()
            ).hexdigest()
            policies.append([run_id, horizon, expert_for_horizon(horizon),
                "deterministic horizon gate", "rejected_no_separate_oos_advantage",
                json.dumps(selected_families), policy_hash, "validation_only", True, True])
        ablation_frame = pd.DataFrame(ablations, columns=("run_id", "horizon", "expert",
            "feature_family", "available", "validation_full_rank_ic", "validation_ablated_rank_ic",
            "validation_contribution", "holdout_full_rank_ic", "holdout_ablated_rank_ic",
            "holdout_contribution", "gate_status", "reason", "immutable"))
        policy_frame = pd.DataFrame(policies, columns=("run_id", "horizon", "expert", "gate_rule",
            "regime_expert_status", "selected_families_json", "policy_hash", "selection_sample",
            "research_only", "immutable"))
        candidates = con.execute("SELECT secid,horizon,expected_median,downside_axis,relative_rank,"
            "evidence_quality,timing_status,abstain FROM opportunity_candidates "
            "WHERE run_id=? AND candidate_type='equity' AND horizon IN (5,20,60,120,250)",
            [opportunity_run]).df()
        current_rows = []
        for secid, group in candidates.groupby("secid"):
            returns = group.set_index("horizon").expected_median.to_dict()
            text = interpretation(returns.get(5), returns.get(120))
            for row in group.itertuples():
                label = "positive" if row.expected_median > 0 else (
                    "negative" if row.expected_median < 0 else "neutral"
                )
                current_rows.append([run_id, cutoff, secid, int(row.horizon),
                    expert_for_horizon(int(row.horizon)), row.expected_median, row.downside_axis,
                    row.relative_rank, row.evidence_quality, row.timing_status, label, text,
                    row.abstain, "research_only", "horizon-specific evidence; no forced consensus", True])
        current = pd.DataFrame(current_rows, columns=("run_id", "cutoff", "secid", "horizon",
            "expert", "expected_median", "downside", "relative_rank", "evidence_quality",
            "timing_status", "term_structure_label", "cross_horizon_interpretation", "abstain",
            "status", "reason", "immutable"))
        _insert(con, "horizon_feature_ablation", ablation_frame)
        _insert(con, "horizon_expert_policies", policy_frame)
        _insert(con, "current_horizon_term_structure", current)
        details = {"experts": ["short", "medium", "long"], "gate": "horizon_only",
                   "regime_experts_promoted": 0, "forced_consensus": False,
                   "probability_published": False, "production_changes": 0}
        con.execute("UPDATE multi_horizon_runs SET finished_at=current_timestamp,status='completed',"
                    "ablation_rows=?,current_rows=?,details_json=? WHERE run_id=?",
                    [len(ablation_frame), len(current), json.dumps(details), run_id])
        return {"run_id": run_id, "status": "completed", "ablations": len(ablation_frame),
                "current_rows": len(current), "cached": False}
    except Exception as exc:
        con.execute("UPDATE multi_horizon_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def multi_horizon_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,ablation_rows,current_rows,details_json "
                      "FROM multi_horizon_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "ablations", "current_rows", "details"),
                    row, strict=True))
