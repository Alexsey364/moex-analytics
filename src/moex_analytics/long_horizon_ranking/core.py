"""Stage 61 frozen-OOS economic validation for cross-sectional ranking."""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

import numpy as np
import pandas as pd

VERSION = "long-horizon-ranking-validation-v2"
HORIZONS = (5, 20, 60, 120, 250)
PRIMARY = (60, 120, 250)
PERIODS = (
    ("2003-2008", "2003-01-01", "2008-12-31"),
    ("2009-2013", "2009-01-01", "2013-12-31"),
    ("2014-2018", "2014-01-01", "2018-12-31"),
    ("2019-2021", "2019-01-01", "2021-12-31"),
    ("2022+", "2022-01-01", "2099-12-31"),
)
DDL = """
CREATE TABLE IF NOT EXISTS long_horizon_ranking_runs(
 run_id VARCHAR PRIMARY KEY,ranking_run_id VARCHAR,cutoff DATE,started_at TIMESTAMP,
 finished_at TIMESTAMP,status VARCHAR,rows BIGINT,details_json JSON,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS long_horizon_ranking_validation(
 run_id VARCHAR,horizon INTEGER,context_type VARCHAR,context_value VARCHAR,dates INTEGER,
 observations BIGINT,effective_n DOUBLE,rank_ic DOUBLE,ci_low DOUBLE,ci_high DOUBLE,
 top5_return DOUBLE,top10_return DOUBLE,top20_return DOUBLE,middle_return DOUBLE,
 bottom20_return DOUBLE,bottom10_return DOUBLE,bottom5_return DOUBLE,
 top_bottom_spread_after_costs DOUBLE,turnover DOUBLE,top20_persistence DOUBLE,
 permutation_pvalue DOUBLE,corrected_pvalue DOUBLE,status VARCHAR,reason VARCHAR,
 immutable BOOLEAN,PRIMARY KEY(run_id,horizon,context_type,context_value));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def date_rank_ic(frame: pd.DataFrame) -> pd.Series:
    return frame.groupby("trade_date").apply(
        lambda group: group.predicted_rank.corr(group.actual_return, method="spearman"),
        include_groups=False,
    ).dropna()


def clustered_ci(values: pd.Series, iterations: int = 500) -> tuple[float, float]:
    clean = values.dropna().to_numpy(float)
    if len(clean) < 10:
        return np.nan, np.nan
    rng = np.random.default_rng(61)
    estimates = [float(rng.choice(clean, len(clean), replace=True).mean()) for _ in range(iterations)]
    return float(np.quantile(estimates, .025)), float(np.quantile(estimates, .975))


def _group_returns(group: pd.DataFrame) -> dict[str, float]:
    ranked = group.assign(percentile=group.predicted_rank.rank(pct=True, method="average"))
    masks = {
        "top5": ranked.percentile > .95,
        "top10": ranked.percentile > .90,
        "top20": ranked.percentile > .80,
        "middle": ranked.percentile.between(.40, .60),
        "bottom20": ranked.percentile <= .20,
        "bottom10": ranked.percentile <= .10,
        "bottom5": ranked.percentile <= .05,
    }
    return {name: float(ranked.loc[mask, "actual_return"].mean()) if mask.any() else np.nan
            for name, mask in masks.items()}


def _metrics(
    frame: pd.DataFrame, horizon: int, cost: float = .0015, permutation_iterations: int = 50
) -> dict[str, Any]:
    correlations = date_rank_ic(frame)
    low, high = clustered_ci(correlations)
    grouped = pd.DataFrame([_group_returns(group) for _, group in frame.groupby("trade_date")])
    dates = sorted(frame.trade_date.unique())
    memberships = {
        date: set(group.loc[group.predicted_rank.rank(pct=True) > .8, "secid"])
        for date, group in frame.groupby("trade_date")
    }
    overlaps = []
    for left, right in itertools.pairwise(dates):
        union = memberships[left] | memberships[right]
        overlaps.append(len(memberships[left] & memberships[right]) / len(union) if union else 1.0)
    persistence = float(np.mean(overlaps)) if overlaps else np.nan
    turnover = 1 - persistence if np.isfinite(persistence) else np.nan
    spread = grouped.top20 - grouped.bottom20 - 2 * cost
    rng = np.random.default_rng(6100 + horizon)
    observed = float(correlations.mean()) if len(correlations) else np.nan
    # Permute within each date: this preserves the cross-section and never mixes time.
    # Pre-ranking makes the test fast enough to use a meaningful number of draws.
    ranked_pairs = []
    for _, group in frame.groupby("trade_date"):
        ranked_pairs.append((
            group.predicted_rank.rank().to_numpy(float),
            group.actual_return.rank().to_numpy(float),
        ))
    permutations = []
    for _ in range(permutation_iterations):
        daily = [np.corrcoef(left, rng.permutation(right))[0, 1]
                 for left, right in ranked_pairs if len(left) > 2]
        permutations.append(float(np.nanmean(daily)))
    pvalue = float(
        (1 + sum(value >= observed for value in permutations)) / (permutation_iterations + 1)
    )
    return {
        "dates": len(correlations), "observations": len(frame),
        "effective_n": len(correlations) / max(1, horizon), "rank_ic": observed,
        "ci_low": low, "ci_high": high, **{name: float(grouped[name].mean()) for name in grouped},
        "spread": float(spread.mean()), "turnover": turnover, "persistence": persistence,
        "pvalue": pvalue,
    }


def _status(metric: dict[str, Any], horizon: int) -> str:
    if not np.isfinite(metric["ci_low"]) or metric["dates"] < 50:
        return "NO_EDGE"
    if metric["ci_low"] > 0 and metric["spread"] > 0 and horizon in PRIMARY:
        return "ROBUST_RELATIVE_EDGE"
    if metric["rank_ic"] > 0 and metric["spread"] > 0:
        return "SHADOW_RANKER"
    return "WEAK_EDGE" if metric["rank_ic"] > 0 else "NO_EDGE"


def run_long_horizon_validation(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id,cutoff FROM ranking_research_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise ValueError("completed frozen ranking run required")
    ranking_run, cutoff = source
    run_id = hashlib.sha256(f"{VERSION}|{ranking_run}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,rows FROM long_horizon_ranking_runs WHERE run_id=?",
                         [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "rows": cached[1], "cached": True}
    predictions = con.execute(
        "SELECT trade_date,secid,horizon,predicted_rank,actual_return FROM "
        "ranking_oos_predictions WHERE run_id=? AND horizon IN (5,20,60,120,250)",
        [ranking_run],
    ).df()
    if predictions.empty:
        raise ValueError("frozen OOS ranking predictions required")
    con.execute("INSERT OR REPLACE INTO long_horizon_ranking_runs VALUES "
                "(?,?,?,current_timestamp,NULL,'running',0,?,true)",
                [run_id, ranking_run, cutoff, json.dumps({"production_changes": 0})])
    rows = []
    raw_metrics = []
    metric_row_indices = []
    for horizon in HORIZONS:
        base = predictions[predictions.horizon == horizon].copy()
        contexts = [("all", "all", base)]
        for label, start, end in PERIODS:
            sample = base[base.trade_date.between(pd.Timestamp(start), pd.Timestamp(end))]
            if not sample.empty:
                contexts.append(("period", label, sample))
            else:
                rows.append([
                    run_id, horizon, "period", label, 0, 0, 0.0,
                    np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan,
                    np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan,
                    np.nan, "NO_EDGE", "insufficient_history: unavailable in frozen OOS", True,
                ])
        for context_type, context_value, sample in contexts:
            metric = _metrics(
                sample, horizon, permutation_iterations=250 if context_type == "all" else 100
            )
            raw_metrics.append(metric)
            metric_row_indices.append(len(rows))
            rows.append([run_id, horizon, context_type, context_value, metric["dates"],
                metric["observations"], metric["effective_n"], metric["rank_ic"],
                metric["ci_low"], metric["ci_high"], metric["top5"], metric["top10"],
                metric["top20"], metric["middle"], metric["bottom20"], metric["bottom10"],
                metric["bottom5"], metric["spread"], metric["turnover"],
                metric["persistence"], metric["pvalue"], None, _status(metric, horizon),
                "frozen OOS; date-clustered bootstrap; costs included", True])
    # Five horizon hypotheses are primary; period rows are stability slices of the
    # same tests, not additional independently selected strategies.
    corrected = [min(1.0, metric["pvalue"] * len(HORIZONS)) for metric in raw_metrics]
    for row_index, corrected_pvalue in zip(metric_row_indices, corrected, strict=True):
        row = rows[row_index]
        row[21] = corrected_pvalue
        if row[22] == "ROBUST_RELATIVE_EDGE" and corrected_pvalue > .05:
            row[22] = "SHADOW_RANKER"
    frame = pd.DataFrame(rows, columns=("run_id", "horizon", "context_type", "context_value",
        "dates", "observations", "effective_n", "rank_ic", "ci_low", "ci_high",
        "top5_return", "top10_return", "top20_return", "middle_return", "bottom20_return",
        "bottom10_return", "bottom5_return", "top_bottom_spread_after_costs", "turnover",
        "top20_persistence", "permutation_pvalue", "corrected_pvalue", "status", "reason",
        "immutable"))
    con.register("_ranking_validation", frame)
    fields = ",".join(frame.columns)
    con.execute(f"INSERT INTO long_horizon_ranking_validation ({fields}) SELECT {fields} "
                "FROM _ranking_validation")
    con.unregister("_ranking_validation")
    con.execute("UPDATE long_horizon_ranking_runs SET finished_at=current_timestamp,status='completed',"
                "rows=?,details_json=? WHERE run_id=?",
                [len(frame), json.dumps({
                    "multiple_testing": "bonferroni_across_horizons",
                    "unavailable_periods": [label for label, start, end in PERIODS if not
                        predictions.trade_date.between(pd.Timestamp(start), pd.Timestamp(end)).any()],
                    "sector_and_listing_slices": "unavailable_without_point_in_time_classification",
                    "capacity": "unavailable_without_point_in_time_liquidity in frozen predictions",
                    "simple_baselines": "not stored on identical frozen rows; no superiority claim",
                    "production_changes": 0,
                }),
                 run_id])
    return {"run_id": run_id, "status": "completed", "rows": len(frame), "cached": False}


def ranking_validation_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,rows,details_json FROM long_horizon_ranking_runs "
                      "ORDER BY started_at DESC LIMIT 1").fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "cutoff", "rows", "details"), row, strict=True
    ))
