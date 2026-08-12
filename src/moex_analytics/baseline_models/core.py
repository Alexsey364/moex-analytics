"""PIT-safe transparent return baselines and their frozen OOS scorecards."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "strong-baselines-v1"
MODELS = ("no_change", "drift_1y", "drift_3y", "drift_expanding", "momentum",
          "mean_reversion", "market_beta", "simple_valuation")
MIN_TRAIN = 60


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _split(dates: pd.Series) -> pd.Series:
    unique = np.sort(pd.to_datetime(dates.unique()))
    calibration = unique[int(len(unique) * 0.60)]
    test = unique[int(len(unique) * 0.80)]
    values = pd.to_datetime(dates)
    return pd.Series(np.where(values < calibration, "train",
        np.where(values < test, "calibration", "test")), index=dates.index)


def _history_prediction(train: pd.DataFrame, row: pd.Series, model: str) -> float | None:
    returns = train.forward_return.dropna()
    if model == "no_change":
        return 0.0
    if len(returns) < MIN_TRAIN:
        return None
    if model == "drift_1y":
        return float(returns.tail(252).median())
    if model == "drift_3y":
        return float(returns.tail(756).median())
    if model == "drift_expanding":
        return float(returns.median())
    price = train.drop_duplicates("evaluation_date").sort_values("evaluation_date")
    observed = price.forward_return.shift(row.horizon).dropna()
    if model == "momentum":
        return float(observed.tail(max(20, int(row.horizon))).median()) if len(observed) else None
    if model == "mean_reversion":
        recent = observed.tail(60)
        return float(-0.5 * recent.mean()) if len(recent) >= 20 else None
    if model == "market_beta":
        sample = train[["forward_return", "market_return"]].dropna().tail(756)
        if len(sample) < MIN_TRAIN or sample.market_return.var() <= 0:
            return None
        beta = float(sample.forward_return.cov(sample.market_return) / sample.market_return.var())
        return beta * float(sample.market_return.median())
    # Strictly unavailable until a PIT valuation forecast is joined in a later stage.
    return None


def _predictions(targets: pd.DataFrame, run_id: str) -> pd.DataFrame:
    frames = []
    for (secid, horizon), group in targets.groupby(["secid", "horizon"], sort=True):
        group = group.sort_values("evaluation_date").reset_index(drop=True)
        group["split"] = _split(group.evaluation_date)
        # At row i only the label ending no later than i can be known. With a session horizon h,
        # shifting the target by h is exactly the target-availability embargo.
        known = group.forward_return.shift(int(horizon))
        known_market = group.market_return.shift(int(horizon))
        observations = known.notna().cumsum()
        probability = (known > 0).expanding().mean().where(observations >= MIN_TRAIN)
        predictions = {
            "no_change": pd.Series(0.0, index=group.index),
            "drift_1y": known.rolling(252, min_periods=MIN_TRAIN).median(),
            "drift_3y": known.rolling(756, min_periods=MIN_TRAIN).median(),
            "drift_expanding": known.expanding(MIN_TRAIN).median(),
            "momentum": known,
            "mean_reversion": -0.5 * known.rolling(60, min_periods=20).mean(),
        }
        beta_window = 756
        covariance = known.rolling(beta_window, min_periods=MIN_TRAIN).cov(known_market)
        variance = known_market.rolling(beta_window, min_periods=MIN_TRAIN).var()
        predictions["market_beta"] = covariance / variance * known_market.expanding(MIN_TRAIN).median()
        training_end = group.target_available_date.shift(int(horizon))
        for model, prediction in predictions.items():
            output = pd.DataFrame({"run_id": run_id, "evaluation_date": group.evaluation_date,
                "secid": secid, "horizon": int(horizon), "model": model,
                "prediction": prediction, "actual": group.forward_return,
                "actual_excess_market": group.excess_imoex, "probability_up": probability,
                "training_observations": observations, "training_end": training_end,
                "feature_timestamp": group.feature_timestamp,
                "target_available_date": group.target_available_date, "split": group.split,
                "immutable": True})
            if model == "no_change":
                output["probability_up"] = 0.5
            frames.append(output.dropna(subset=["prediction"]))
    return pd.concat(frames, ignore_index=True)


def _scorecards(predictions: pd.DataFrame, run_id: str) -> pd.DataFrame:
    rows = []
    test = predictions[predictions.split == "test"]
    for (secid, horizon), ticker in test.groupby(["secid", "horizon"], sort=True):
        metrics = []
        for model, sample in ticker.groupby("model", sort=True):
            error = sample.actual - sample.prediction
            mae = float(error.abs().mean())
            years = sample.groupby(pd.to_datetime(sample.evaluation_date).dt.year).apply(
                lambda x: float(np.mean(np.sign(x.prediction) == np.sign(x.actual))),
                include_groups=False,
            )
            correlation = (
                sample.actual.corr(sample.prediction, method="spearman")
                if sample.prediction.nunique() > 1 and sample.actual.nunique() > 1
                else None
            )
            excess_mae = float(np.mean(np.abs(sample.actual_excess_market - sample.prediction)))
            metrics.append({"model": model, "sample_size": len(sample), "mae": mae,
                "median_ae": float(error.abs().median()), "rmse": float(np.sqrt(np.mean(error**2))),
                "direction_accuracy": float(np.mean(np.sign(sample.prediction) == np.sign(sample.actual))),
                "brier": float(np.mean(((sample.probability_up.fillna(.5)) - (sample.actual > 0))**2)),
                "spearman_rank_correlation": None if pd.isna(correlation) else float(correlation),
                "excess_return_mae": excess_mae,
                "subperiod_stability": float((years >= .5).mean()) if len(years) else 0.0})
        metrics.sort(key=lambda x: (x["mae"], x["model"]))
        best = metrics[0]["mae"] if metrics else np.nan
        for rank, metric in enumerate(metrics, 1):
            rows.append([run_id, secid, int(horizon), metric["model"], metric["sample_size"],
                metric["mae"], metric["median_ae"], metric["rmse"], metric["direction_accuracy"],
                metric["brier"], metric["spearman_rank_correlation"], metric["excess_return_mae"],
                metric["subperiod_stability"], "BASELINE", rank, metric["mae"] - best,
                json.dumps({"split": "frozen_test", "probability_publication": False})])
    return pd.DataFrame(rows, columns=("run_id", "secid", "horizon", "model", "sample_size",
        "mae", "median_ae", "rmse", "direction_accuracy", "brier",
        "spearman_rank_correlation", "excess_return_mae", "subperiod_stability", "status",
        "rank", "mae_difference_to_best", "details_json"))


def build_baseline_suite(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    target = con.execute("SELECT run_id FROM predictive_target_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not target:
        raise ValueError("completed predictive target dataset is required")
    target_run_id = target[0]
    targets = con.execute("SELECT * FROM predictive_return_targets WHERE run_id=?", [target_run_id]).df()
    cutoff = pd.Timestamp(targets.evaluation_date.max()).date()
    signature = hashlib.sha256(pd.util.hash_pandas_object(targets, index=False).values.tobytes()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{target_run_id}|{signature}".encode()).hexdigest()[:20]
    existing = con.execute("SELECT status,prediction_rows,scorecard_rows FROM predictive_baseline_runs "
        "WHERE run_id=?", [run_id]).fetchone()
    if existing and existing[0] == "completed":
        return {"run_id": run_id, "status": existing[0], "predictions": existing[1],
                "scorecards": existing[2], "cached": True}
    predictions = _predictions(targets, run_id)
    scorecards = _scorecards(predictions, run_id)
    con.execute("BEGIN")
    try:
        con.execute("INSERT OR REPLACE INTO predictive_baseline_runs "
            "(run_id,target_run_id,version,cutoff,input_hash,started_at,status,prediction_rows,"
            "scorecard_rows,details_json,immutable) "
            "VALUES (?,?,?,?,?,current_timestamp,'running',0,0,?,true)",
            [run_id, target_run_id, VERSION, cutoff, signature,
             json.dumps({"split": "time_60_20_20", "production_changes": 0})])
        for table, frame in (("predictive_baseline_predictions", predictions),
                             ("predictive_baseline_scorecards", scorecards)):
            con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
            con.register("_incoming", frame)
            columns = ",".join(frame.columns)
            con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM _incoming")
            con.unregister("_incoming")
        con.execute("UPDATE predictive_baseline_runs SET finished_at=current_timestamp,status='completed',"
            "prediction_rows=?,scorecard_rows=? WHERE run_id=?", [len(predictions), len(scorecards), run_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"run_id": run_id, "status": "completed", "predictions": len(predictions),
            "scorecards": len(scorecards), "cached": False}
