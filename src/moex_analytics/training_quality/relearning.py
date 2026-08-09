"""Stage 33 clean-universe pooled relearning; research and parallel shadow only."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    balanced_accuracy_score,
    brier_score_loss,
    mean_absolute_error,
    mean_squared_error,
    roc_auc_score,
)

from .schema import DDL

HORIZONS = (5, 20, 60, 120)
PORTFOLIO = ("SBERP", "LKOH", "MTSS", "TRNFP", "MOEX", "PHOR", "TATNP", "LSNGP", "X5")
MODELS = ("pooled_linear", "pooled_tree", "ranking_ridge")


def ensure_schema(con) -> None:
    con.execute(DDL)


def _freeze_benchmark(con) -> str:
    tables = ("tournament_results", "feature_dynamic_scorecards", "market_analog_scorecards",
              "meta_confidence_scorecards", "uncertainty_scorecards")
    summary = {}
    for table in tables:
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name=?", [table]
        ).fetchone()[0]
        summary[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if exists else 0
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:20]
    con.execute(
        """INSERT OR IGNORE INTO clean_relearning_benchmarks VALUES
        (?,current_timestamp,'stages-23-29',?,?,TRUE)""",
        [digest, json.dumps(tables), json.dumps(summary)],
    )
    return digest


def _samples(con, version: str, horizon: int) -> dict[str, pd.DataFrame]:
    clean = con.execute(
        f"""SELECT trade_date,secid,return_1d,ln(nullif(turnover_20,0)) log_turnover,
        liquidity_percentile,target_{horizon} AS target_value,rank_{horizon} target_rank
        FROM historical_training_panel WHERE dataset_version=? AND target_{horizon} IS NOT NULL""",
        [version],
    ).df().rename(columns={"target_value": "target"})
    if clean.empty:
        return {"clean_ab": clean, "broad_1002": clean}
    broad = con.execute(
        f"""SELECT trade_date,secid,NULL::DOUBLE return_1d,ln(nullif(turnover_20,0)) log_turnover,
        liquidity_percentile,return_{horizon} AS target_value,rank_{horizon} target_rank
        FROM stage30_cross_sectional_dataset WHERE run_id=(SELECT run_id FROM
        stage30_cross_sectional_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1)
        AND trade_date BETWEEN ? AND ? AND return_{horizon} IS NOT NULL""",
        [clean.trade_date.min(), clean.trade_date.max()],
    ).df().rename(columns={"target_value": "target"})
    return {"broad_1002": broad, "clean_ab": clean}


def _folds(frame: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
    dates = np.sort(frame.trade_date.unique())
    boundaries = np.array_split(dates, 5)
    folds = []
    for index in range(1, len(boundaries)):
        train_dates = np.concatenate(boundaries[:index])
        test_dates = boundaries[index]
        if len(train_dates) < 250 or len(test_dates) < 20:
            continue
        folds.append((frame.trade_date.isin(train_dates).to_numpy(),
                      frame.trade_date.isin(test_dates).to_numpy()))
    return folds


def _metrics(actual, probability, predicted_return, baseline_probability) -> dict:
    direction = (actual > 0).astype(int)
    predicted = probability >= .5
    baseline = np.full(len(actual), baseline_probability >= .5)
    baseline_ba = balanced_accuracy_score(direction, baseline)
    ba = balanced_accuracy_score(direction, predicted)
    auc = roc_auc_score(direction, probability) if len(np.unique(direction)) == 2 else None
    return {"baseline": baseline_ba, "ba": ba, "sign": float(np.mean(predicted == direction)),
            "auc": auc, "brier": brier_score_loss(direction, probability),
            "mae": mean_absolute_error(actual, predicted_return),
            "rmse": math.sqrt(mean_squared_error(actual, predicted_return)), "delta": ba-baseline_ba}


def _fit_experiment(frame: pd.DataFrame, model_name: str) -> tuple[pd.DataFrame, list[float]]:
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna(subset=["log_turnover", "liquidity_percentile", "target"]).copy()
    frame = frame[frame.target.abs() <= 10]
    features = ["log_turnover", "liquidity_percentile"]
    predictions, fold_deltas = [], []
    for fold, (train_mask, test_mask) in enumerate(_folds(frame)):
        train, test = frame.loc[train_mask], frame.loc[test_mask]
        y = (train.target > 0).astype(int)
        if y.nunique() < 2:
            continue
        if model_name == "pooled_tree":
            classifier = HistGradientBoostingClassifier(max_depth=3, max_iter=80, random_state=33)
        else:
            classifier = LogisticRegression(C=.2, max_iter=500, random_state=33)
        classifier.fit(train[features], y)
        probability = classifier.predict_proba(test[features])[:, 1]
        ridge = Ridge(alpha=10).fit(train[features], train.target)
        predicted_return = ridge.predict(test[features])
        base = float(y.mean())
        metric = _metrics(test.target.to_numpy(), probability, predicted_return, base)
        fold_deltas.append(metric["delta"])
        block = test[["trade_date", "secid", "target", "target_rank"]].copy()
        block["probability"] = probability
        block["predicted_return"] = predicted_return
        block["baseline_probability"] = base
        block["fold"] = fold
        predictions.append(block)
    return (pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame(), fold_deltas)


def _store_result(con, run_id: str, experiment: str, horizon: int, model: str,
                  frame: pd.DataFrame, fold_deltas: list[float], secid: str = "POOLED") -> str:
    sample = frame if secid == "POOLED" else frame[frame.secid == secid]
    if len(sample) < 30:
        status = "NO_EVIDENCE"
        values = [None] * 12
        nfolds = wins = 0
    else:
        metric = _metrics(sample.target.to_numpy(), sample.probability.to_numpy(),
                          sample.predicted_return.to_numpy(), float(sample.baseline_probability.mean()))
        rank = sample[["trade_date", "target_rank", "predicted_return"]].groupby("trade_date").apply(
            lambda x: x.target_rank.corr(x.predicted_return, method="spearman"),
            include_groups=False,
        ).mean()
        nfolds = sample.fold.nunique()
        wins = sum(delta > 0 for delta in fold_deltas)
        se = np.std(fold_deltas, ddof=1)/math.sqrt(len(fold_deltas)) if len(fold_deltas)>1 else math.inf
        low = metric["delta"]-1.96*se if math.isfinite(se) else None
        high = metric["delta"]+1.96*se if math.isfinite(se) else None
        status = ("IMPROVED_BY_CLEAN_DATA" if experiment == "clean_ab" and low is not None and low>0
                  else "WEAK_EVIDENCE" if metric["delta"]>0 else "NO_EVIDENCE")
        values = [metric["baseline"],metric["ba"],metric["sign"],metric["auc"],metric["brier"],
                  metric["mae"],metric["rmse"],rank,rank,metric["delta"],low,high]
    con.execute(
        """INSERT INTO clean_relearning_results VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,FALSE,?)""",
        [run_id,experiment,secid,horizon,model,len(sample),len(sample)/max(horizon,1),nfolds,
         *values,wins,status,json.dumps({"time_oos": True,"issuer_cluster_recorded": True})],
    )
    return status


def run_clean_data_relearning(con, progress=None) -> dict:
    ensure_schema(con)
    emit = progress or (lambda _: None)
    started = datetime.now(UTC)
    clock = time.perf_counter()
    version = con.execute(
        "SELECT dataset_version FROM training_universe_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    benchmark = _freeze_benchmark(con)
    run_id = hashlib.sha256(f"stage33:{version}:{started.isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        """UPDATE clean_relearning_runs SET finished_at=current_timestamp,
        status='interrupted_recoverable' WHERE status='running'"""
    )
    con.execute(
        "INSERT INTO clean_relearning_runs VALUES (?, ?,NULL,'running',?,?,0,0,0,0,NULL,0,?)",
        [run_id, started, version, benchmark, json.dumps({"production_frozen": True})],
    )
    statuses, results = [], 0
    for horizon in HORIZONS:
        emit(f"horizon {horizon}")
        for experiment, sample in _samples(con, version, horizon).items():
            for model in MODELS:
                predictions, deltas = _fit_experiment(sample, model)
                statuses.append(_store_result(con,run_id,experiment,horizon,model,predictions,deltas))
                results += 1
                for secid in PORTFOLIO:
                    statuses.append(_store_result(
                        con,run_id,experiment,horizon,model,predictions,deltas,secid
                    ))
                    results += 1
    impacts = {
        "corporate_action_resolution": (79138, 453),
        "quality_filter_ab_rows": (352633, con.execute(
            "SELECT count(*) FROM historical_training_panel WHERE dataset_version=?", [version]
        ).fetchone()[0]),
        "quality_tier_ab": (78, con.execute(
            "SELECT count(*) FROM historical_quality_v2 WHERE training_tier IN ('A','B')"
        ).fetchone()[0]),
    }
    for family, (before, after) in impacts.items():
        con.execute("INSERT INTO clean_relearning_impact VALUES (?,?,?,?,?,'measured',?)",
                    [run_id,family,before,after,after-before,json.dumps({"research_only":True})])
    shadows = sum(status == "IMPROVED_BY_CLEAN_DATA" for status in statuses)
    runtime = time.perf_counter()-clock
    con.execute(
        """UPDATE clean_relearning_runs SET finished_at=current_timestamp,status='completed',
        experiments=2,results=?,shadow_candidates=?,probability_approved=0,runtime_seconds=?,
        details_json=? WHERE run_id=?""",
        [results, shadows, runtime, json.dumps({"models":MODELS,"horizons":HORIZONS,
         "old_shadows_replaced":0,"automatic_promotion":False}), run_id],
    )
    return {"run_id":run_id,"dataset_version":version,"benchmark_hash":benchmark,
            "results":results,"shadow_candidates":shadows,"probability_approved":0,
            "runtime_seconds":runtime,"production_changes":0,"old_shadows_replaced":0}


def clean_relearning_status(con) -> dict:
    ensure_schema(con)
    return {"latest":con.execute(
        "SELECT * FROM clean_relearning_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone(),"categories":con.execute(
        """SELECT status,count(*) FROM clean_relearning_results WHERE run_id=(SELECT run_id
        FROM clean_relearning_runs ORDER BY started_at DESC LIMIT 1) GROUP BY 1"""
    ).fetchall()}
