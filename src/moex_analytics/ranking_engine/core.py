"""Stage 52 cross-sectional ranking with validation-only model selection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import ndcg_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .schema import DDL

VERSION = "cross-sectional-ranking-v4-real-turnover"
PORTFOLIO = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
FEATURES = ("momentum_5", "momentum_20", "momentum_60", "momentum_120",
            "volatility_20", "volatility_60", "drawdown_60", "relative_20",
            "liquidity_proxy", "momentum_20_pct", "volatility_20_pct", "drawdown_60_pct",
            "relative_20_pct", "liquidity_pct")


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _models() -> dict[str, Any]:
    return {
        "linear_ranking": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "elasticnet_proxy": make_pipeline(StandardScaler(), ElasticNet(alpha=.001, l1_ratio=.25,
                                                                          max_iter=3000)),
    }


def _feature_panel(con: Any) -> pd.DataFrame:
    frame = con.execute(
        "SELECT trade_date,canonical_secid AS secid,total_return_index FROM daily_returns "
        "WHERE calculation_version='actual-dividends-v1' AND canonical_secid<>'IMOEX' "
        "ORDER BY secid,trade_date"
    ).df()
    market = con.execute(
        "SELECT trade_date,total_return_index FROM daily_returns WHERE canonical_secid='IMOEX' "
        "AND calculation_version='actual-dividends-v1' ORDER BY trade_date"
    ).df().set_index("trade_date").total_return_index
    chunks = []
    for _, group in frame.groupby("secid", sort=False):
        group = group.copy()
        index = group.total_return_index
        daily = index.pct_change(fill_method=None)
        for window in (5, 20, 60, 120):
            group[f"momentum_{window}"] = index.pct_change(window, fill_method=None)
        group["volatility_20"] = daily.rolling(20).std() * np.sqrt(252)
        group["volatility_60"] = daily.rolling(60).std() * np.sqrt(252)
        group["drawdown_60"] = index / index.rolling(60).max() - 1
        market_aligned = market.reindex(group.trade_date).to_numpy(float)
        group["relative_20"] = group.momentum_20 - pd.Series(market_aligned).pct_change(
            20, fill_method=None
        ).to_numpy()
        group["liquidity_proxy"] = daily.abs().rolling(20).mean()
        chunks.append(group)
    panel = pd.concat(chunks, ignore_index=True)
    for name in ("momentum_20", "volatility_20", "drawdown_60", "relative_20",
                 "liquidity_proxy"):
        panel[f"{name.removesuffix('_proxy')}_pct" if name == "liquidity_proxy" else f"{name}_pct"] = (
            panel.groupby("trade_date")[name].rank(pct=True, method="average")
        )
    return panel.dropna(subset=list(FEATURES))


def _split_dates(dates: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    unique = np.array(sorted(pd.to_datetime(dates.unique())))
    if len(unique) < 100:
        raise ValueError("at least 100 dates are required for temporal validation")
    train_end = pd.Timestamp(unique[int(len(unique) * .70) - 1])
    validation_end = pd.Timestamp(unique[int(len(unique) * .85) - 1])
    holdout_start = pd.Timestamp(unique[int(len(unique) * .85)])
    return train_end, validation_end, holdout_start


def _mean_rank_ic(frame: pd.DataFrame, prediction: str = "predicted_score") -> float:
    values = [group[prediction].corr(group.actual_rank, method="spearman")
              for _, group in frame.groupby("trade_date") if len(group) >= 3]
    clean = [value for value in values if pd.notna(value)]
    return float(np.mean(clean)) if clean else np.nan


def _metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    if frame.empty:
        return {"observations": 0, "dates": 0}
    daily = []
    for _, group in frame.groupby("trade_date"):
        group = group.sort_values("predicted_score", ascending=False)
        n = len(group)
        if n < 3:
            continue
        top10 = max(1, int(np.ceil(n * .10)))
        top20 = max(1, int(np.ceil(n * .20)))
        bottom10 = max(1, int(np.ceil(n * .10)))
        actual = group.actual_return.to_numpy(float)
        relevance = group.actual_rank.to_numpy(float)
        score = group.predicted_score.to_numpy(float)
        daily.append({"rank_ic": group.predicted_score.corr(group.actual_rank, method="spearman"),
            "ndcg": float(ndcg_score([relevance], [score])),
            "top10": float(actual[:top10].mean() - actual.mean()),
            "top20": float(actual[:top20].mean() - actual.mean()),
            "bottom10": float(actual[-bottom10:].mean() - actual.mean()),
            "top3": float(actual[:min(3, n)].mean() - group.imoex_return.mean()),
            "top5": float(actual[:min(5, n)].mean() - group.imoex_return.mean()),
            "top10x": float(actual[:min(10, n)].mean() - group.imoex_return.mean()),
            "hit": float(actual[:min(3, n)].mean() > group.imoex_return.mean())})
    values = pd.DataFrame(daily)
    return {"observations": len(frame), "dates": len(values), "rank_ic": float(values.rank_ic.mean()),
        "spearman": float(values.rank_ic.mean()), "ndcg": float(values.ndcg.mean()),
        "top_decile_spread": float(values.top10.mean()),
        "top_quintile_spread": float(values.top20.mean()),
        "bottom_decile_spread": float(values.bottom10.mean()), "top3_excess": float(values.top3.mean()),
        "top5_excess": float(values.top5.mean()), "top10_excess": float(values.top10x.mean()),
        "top_k_hit_rate": float(values.hit.mean())}


def _date_bootstrap(frame: pd.DataFrame, iterations: int = 300) -> tuple[float, float]:
    per_date = frame.groupby("trade_date").apply(
        lambda x: x.predicted_score.corr(x.actual_rank, method="spearman"), include_groups=False
    ).dropna().to_numpy(float)
    if len(per_date) < 20:
        return np.nan, np.nan
    rng = np.random.default_rng(42)
    draws = [float(rng.choice(per_date, len(per_date), replace=True).mean()) for _ in range(iterations)]
    return float(np.quantile(draws, .025)), float(np.quantile(draws, .975))


def _policy_hash(run_id: str, horizon: int, model: str, train_end: Any,
                 validation_end: Any) -> str:
    return hashlib.sha256(
        f"{run_id}|{horizon}|{model}|{train_end}|{validation_end}|validation-only".encode()
    ).hexdigest()


def _insert_frame(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    name = f"_{table}"
    con.register(name, frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {name}")
    con.unregister(name)


def run_ranking_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute("SELECT run_id,cutoff,input_hash FROM predictive_target_runs "
                         "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not source:
        raise ValueError("completed Stage 51 target dataset is required")
    target_run, cutoff, input_hash = source
    run_id = hashlib.sha256(f"{VERSION}|{target_run}|{input_hash}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,panel_rows,prediction_rows FROM ranking_research_runs "
                         "WHERE run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "panel_rows": cached[1],
                "predictions": cached[2], "cached": True}
    feature_panel = _feature_panel(con)
    labels = con.execute("SELECT trade_date,exit_date,secid,horizon,total_return AS actual_return,"
                         "percentile_rank AS actual_rank,excess_imoex FROM "
                         "predictive_target_observations WHERE run_id=? AND secid<>'IMOEX'",
                         [target_run]).df()
    panel = feature_panel.merge(labels, on=["trade_date", "secid"], how="inner")
    panel["imoex_return"] = panel.actual_return - panel.excess_imoex
    train_end, validation_end, holdout_start = _split_dates(panel.trade_date)
    con.execute("INSERT OR REPLACE INTO ranking_research_runs "
        "(run_id,target_run_id,dataset_version,cutoff,train_end,validation_end,holdout_start,"
        "started_at,status,panel_rows,prediction_rows,details_json,immutable) "
        "VALUES (?,?,?,?,?,?,?,current_timestamp,'running',?,0,?,true)",
        [run_id, target_run, VERSION, cutoff, train_end, validation_end, holdout_start, len(panel),
         json.dumps({"selection": "validation_only", "production_changes": 0})])
    try:
        prediction_frames, policy_rows, score_rows, backtests, current_rows = [], [], [], [], []
        for horizon in sorted(panel.horizon.unique()):
            data = panel[panel.horizon == horizon].dropna(subset=[*FEATURES, "actual_rank"])
            train = data[(data.trade_date <= train_end) & (data.exit_date <= train_end)]
            validation = data[(data.trade_date > train_end) &
                              (data.trade_date <= validation_end) &
                              (data.exit_date <= validation_end)]
            holdout = data[data.trade_date >= holdout_start]
            fitted: dict[str, Any] = {}
            validation_scores: dict[str, float] = {}
            for name, model in _models().items():
                model.fit(train[list(FEATURES)], train.actual_rank)
                candidate = validation[["trade_date", "secid", "actual_rank", "actual_return",
                                        "imoex_return"]].copy()
                candidate["predicted_score"] = model.predict(validation[list(FEATURES)])
                validation_scores[name] = _mean_rank_ic(candidate)
                fitted[name] = model
                metric = _metrics(candidate)
                policy_rows.append([run_id, int(horizon), name, metric["rank_ic"], metric["ndcg"],
                    metric["top_quintile_spread"], 3, "", False, "validation_only", True])
            selected = max(validation_scores, key=lambda key: np.nan_to_num(validation_scores[key], nan=-9))
            policy_hash = _policy_hash(run_id, int(horizon), selected, train_end, validation_end)
            for row in policy_rows:
                if row[1] == horizon:
                    row[7] = policy_hash if row[2] == selected else _policy_hash(
                        run_id, int(horizon), row[2], train_end, validation_end
                    )
                    row[8] = row[2] == selected
            selected_model = _models()[selected]
            development = data[(data.trade_date <= validation_end) &
                               (data.exit_date <= validation_end)]
            selected_model.fit(development[list(FEATURES)], development.actual_rank)
            predicted = holdout[["trade_date", "secid", "actual_rank", "actual_return",
                                 "imoex_return"]].copy()
            predicted["predicted_score"] = selected_model.predict(holdout[list(FEATURES)])
            predicted["predicted_rank"] = predicted.groupby("trade_date").predicted_score.rank(pct=True)
            predicted.insert(0, "run_id", run_id)
            predicted["horizon"] = int(horizon)
            predicted["model"] = selected
            predicted["sample_type"] = "untouched_holdout_frozen"
            predicted["policy_hash"] = policy_hash
            predicted["history_end"] = validation_end
            predicted["immutable"] = True
            prediction_frames.append(predicted[["run_id", "trade_date", "secid", "horizon", "model",
                "predicted_score", "predicted_rank", "actual_rank", "actual_return", "imoex_return",
                "sample_type", "policy_hash", "history_end", "immutable"]])
            metric = _metrics(predicted)
            ci_low, ci_high = _date_bootstrap(predicted)
            score_rows.append([run_id, int(horizon), selected, "untouched_holdout_frozen",
                metric["observations"], metric["dates"], metric["rank_ic"], metric["spearman"],
                metric["ndcg"], metric["top_decile_spread"], metric["top_quintile_spread"],
                metric["bottom_decile_spread"], metric["top3_excess"], metric["top5_excess"],
                metric["top10_excess"], metric["top_k_hit_rate"], ci_low, ci_high,
                "SHADOW_CANDIDATE" if ci_low > 0 else "NO_EVIDENCE"])
            for k in (3, 5, 10):
                picks = predicted.sort_values(["trade_date", "predicted_score"], ascending=[True, False])
                picks = picks.groupby("trade_date").head(k)
                daily = picks.groupby("trade_date").agg(stock=("actual_return", "mean"),
                    benchmark=("imoex_return", "mean"))
                holdings = picks.groupby("trade_date").secid.apply(set).sort_index()
                turnover_values = [1.0]
                for prior, current in zip(holdings.iloc[:-1], holdings.iloc[1:], strict=True):
                    turnover_values.append(1 - len(prior & current) / max(1, k))
                turnover = float(np.mean(turnover_values))
                commission = .001
                backtests.append([run_id, int(horizon), selected, k, "untouched_holdout_frozen",
                    len(daily), float(daily.stock.mean() - commission),
                    float((daily.stock - daily.benchmark).mean() - commission),
                    float(predicted.groupby("trade_date").actual_return.mean().mean()), turnover,
                    10.0, 1, "research_only"])
            latest = feature_panel[feature_panel.trade_date == feature_panel.trade_date.max()].copy()
            if not latest.empty:
                scores = []
                for model in fitted.values():
                    scores.append(model.predict(latest[list(FEATURES)]))
                matrix = np.vstack(scores)
                chosen_score = selected_model.predict(latest[list(FEATURES)])
                rank = pd.Series(chosen_score).rank(pct=True).to_numpy()
                spread = np.std(np.vstack([pd.Series(x).rank(pct=True) for x in matrix]), axis=0)
                order = np.argsort(-rank)
                tie = np.empty(len(rank), dtype=int)
                group_number, prior = 1, None
                for idx in order:
                    if prior is not None and abs(rank[idx] - prior) > max(.08, spread[idx]):
                        group_number += 1
                    tie[idx], prior = group_number, rank[idx]
                for idx, row in latest.reset_index(drop=True).iterrows():
                    if row.secid not in PORTFOLIO:
                        continue
                    current_rows.append([run_id, pd.Timestamp(row.trade_date), row.secid, int(horizon),
                        float(rank[idx]), float(max(0, rank[idx] - 1.96 * spread[idx])),
                        float(min(1, rank[idx] + 1.96 * spread[idx])), int(tie[idx]),
                        float(max(0, 1 - spread[idx])), metric["rank_ic"], "pending_live_evidence",
                        "research_only", "rank uncertainty overlaps are grouped", True])
        policies = pd.DataFrame(policy_rows, columns=("run_id", "horizon", "model",
            "validation_rank_ic", "validation_ndcg", "validation_top_quintile_spread", "folds",
            "policy_hash", "selected", "selection_sample", "immutable"))
        predictions = pd.concat(prediction_frames, ignore_index=True)
        scores = pd.DataFrame(score_rows, columns=("run_id", "horizon", "model", "sample_type",
            "observations", "dates", "rank_ic", "spearman", "ndcg", "top_decile_spread",
            "top_quintile_spread", "bottom_decile_spread", "top3_excess", "top5_excess",
            "top10_excess", "top_k_hit_rate", "ci_low", "ci_high", "status"))
        backtest_frame = pd.DataFrame(backtests, columns=("run_id", "horizon", "model", "k",
            "sample_type", "periods", "mean_return", "mean_excess_imoex", "equal_eligible_return",
            "turnover", "commission_bps", "execution_lag", "status"))
        current = pd.DataFrame(current_rows, columns=("run_id", "cutoff", "secid", "horizon",
            "relative_rank", "rank_low", "rank_high", "tie_group", "model_agreement",
            "historical_oos", "live_evidence", "status", "reason", "immutable"))
        for table, data in (("ranking_model_policies", policies),
                            ("ranking_oos_predictions", predictions),
                            ("ranking_scorecards", scores), ("ranking_topk_backtests", backtest_frame),
                            ("current_portfolio_ranking", current)):
            _insert_frame(con, table, data)
        details = {"selection_touched_holdout": False, "holdout_frozen": True,
                   "survivors_only": False, "pit_universe_from_observed_history": True,
                   "models": sorted(_models()), "complex_models": False, "production_changes": 0,
                   "probability_published": False}
        con.execute("UPDATE ranking_research_runs SET finished_at=current_timestamp,status='completed',"
                    "prediction_rows=?,details_json=? WHERE run_id=?",
                    [len(predictions), json.dumps(details), run_id])
        return {"run_id": run_id, "status": "completed", "panel_rows": len(panel),
                "predictions": len(predictions), "current_rows": len(current), "cached": False}
    except Exception as exc:
        con.execute("UPDATE ranking_research_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def ranking_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,train_end,validation_end,holdout_start,panel_rows,"
                      "prediction_rows,details_json FROM ranking_research_runs "
                      "ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "train_end", "validation_end",
                    "holdout_start", "panel_rows", "predictions", "details"), row, strict=True))
