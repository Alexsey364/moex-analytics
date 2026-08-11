"""Strict chronological Stage 72 market forecast tournament."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LogisticRegression
from sklearn.metrics import balanced_accuracy_score, matthews_corrcoef
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .schema import ensure_schema

VERSION = "stage72-v1"
HORIZONS = (1, 5, 20, 60, 120)
FEATURES = (
    "return_1",
    "return_5",
    "return_20",
    "return_60",
    "return_120",
    "drawdown",
    "distance_sma20",
    "distance_sma50",
    "distance_sma100",
    "distance_sma200",
    "realized_vol20",
    "realized_vol60",
    "range_expansion",
    "rtsi_return_20",
)


def _targets(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    result = frame.copy()
    close = result.imoex_close.astype(float)
    result["target_return"] = close.shift(-horizon) / close - 1
    realized_daily = close.pct_change().std(skipna=True)
    threshold = max(0.005, float(realized_daily or 0) * np.sqrt(horizon) * 0.35)
    result["target_class"] = np.select(
        [result.target_return < -threshold, result.target_return > threshold], [-1, 1], default=0
    )
    future = pd.concat([close.shift(-step) / close - 1 for step in range(1, horizon + 1)], axis=1)
    result["target_drawdown"] = future.min(axis=1)
    result["target_volatility"] = future.std(axis=1) * np.sqrt(252 / max(horizon, 2))
    return result.dropna(subset=["target_return"])


def _models() -> dict[str, Any]:
    return {
        "logistic": make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(max_iter=500)),
        "hist_gradient_boosting": make_pipeline(SimpleImputer(), HistGradientBoostingClassifier(max_iter=80)),
        "random_forest": make_pipeline(
            SimpleImputer(),
            RandomForestClassifier(n_estimators=100, min_samples_leaf=12, random_state=71, n_jobs=-1),
        ),
        "extra_trees": make_pipeline(
            SimpleImputer(),
            ExtraTreesClassifier(n_estimators=100, min_samples_leaf=12, random_state=71, n_jobs=-1),
        ),
    }


def _safe_metric(function: Any, actual: np.ndarray, predicted: np.ndarray) -> float | None:
    try:
        return float(function(actual, predicted))
    except ValueError:
        return None


def run_market_forecast_research(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Train on the past and score only untouched chronological samples."""
    ensure_schema(con)
    state_run = con.execute(
        "SELECT run_id FROM whole_market_state_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not state_run:
        raise ValueError("Stage 71 whole-market state is required")
    state_run_id = state_run[0]
    frame = con.execute(
        f"""SELECT trade_date,imoex_close,{",".join(FEATURES)} FROM whole_market_state_daily
        WHERE run_id=? ORDER BY trade_date""",
        [state_run_id],
    ).df()
    frame = frame.dropna(subset=["imoex_close"]).reset_index(drop=True)
    signature = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{state_run_id}|{signature}".encode()).hexdigest()[:20]
    old = con.execute("SELECT observations FROM market_forecast_runs WHERE run_id=?", [run_id]).fetchone()
    if old:
        return _status(con, run_id) | {"idempotent": True}
    train_end = int(len(frame) * 0.6)
    validation_end = int(len(frame) * 0.8)
    prediction_rows: list[list[Any]] = []
    score_rows: list[list[Any]] = []
    for horizon in HORIZONS:
        data = _targets(frame, horizon)
        train = data.iloc[:train_end]
        validation = data.iloc[train_end:validation_end]
        holdout = data.iloc[validation_end:]
        x_train = train[list(FEATURES)]
        y_train = train.target_class.astype(int)
        baseline_class = int(y_train.mode().iloc[0])
        for sample_name, sample in (("validation", validation), ("frozen_holdout", holdout)):
            actual = sample.target_class.astype(int).to_numpy()
            baseline = np.full(len(sample), baseline_class)
            baseline_ba = _safe_metric(balanced_accuracy_score, actual, baseline)
            for model_name, model in _models().items():
                has_class_variation = y_train.nunique() >= 2
                if has_class_variation:
                    model.fit(x_train, y_train)
                    predicted = model.predict(sample[list(FEATURES)]).astype(int)
                else:
                    predicted = np.full(len(sample), baseline_class)
                return_model = make_pipeline(
                    SimpleImputer(), StandardScaler(), ElasticNet(alpha=0.001, l1_ratio=0.2)
                )
                return_model.fit(x_train, train.target_return)
                predicted_return = return_model.predict(sample[list(FEATURES)])
                drawdown_prediction = np.full(len(sample), float(train.target_drawdown.median()))
                volatility_prediction = np.full(len(sample), float(train.target_volatility.median()))
                ba = _safe_metric(balanced_accuracy_score, actual, predicted)
                mcc = _safe_metric(matthews_corrcoef, actual, predicted)
                corr = _safe_metric(
                    lambda a, b: np.corrcoef(a, b)[0, 1], sample.target_return, predicted_return
                )
                improvement = ba - baseline_ba if ba is not None and baseline_ba is not None else None
                status = (
                    "experimental"
                    if sample_name == "frozen_holdout" and improvement and improvement > 0.02
                    else "rejected_or_weak"
                )
                if not has_class_variation:
                    status = "insufficient_class_variation"
                score_rows.append(
                    [
                        run_id,
                        horizon,
                        model_name,
                        sample_name,
                        len(sample),
                        ba,
                        mcc,
                        None,
                        float(np.mean(np.abs(sample.target_return - predicted_return))),
                        corr,
                        float(np.mean(np.abs(sample.target_drawdown - drawdown_prediction))),
                        float(np.mean(np.abs(sample.target_volatility - volatility_prediction))),
                        baseline_ba,
                        improvement,
                        status,
                        json.dumps({"three_class_threshold_frozen": True, "probability_gated": True}),
                    ]
                )
                for position, item in enumerate(sample.itertuples()):
                    prediction_rows.append(
                        [
                            run_id,
                            horizon,
                            model_name,
                            item.trade_date,
                            sample_name,
                            int(item.target_class),
                            int(predicted[position]),
                            float(item.target_return),
                            float(predicted_return[position]),
                            float(item.target_drawdown),
                            float(drawdown_prediction[position]),
                            float(item.target_volatility),
                            float(volatility_prediction[position]),
                            False,
                        ]
                    )
    con.executemany(
        """INSERT INTO market_forecast_predictions
        (run_id,horizon,model,trade_date,sample,actual_class,predicted_class,actual_return,predicted_return,
        actual_drawdown,predicted_drawdown,actual_volatility,predicted_volatility,probability_published)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        prediction_rows,
    )
    con.executemany(
        """INSERT INTO market_forecast_scorecards
        (run_id,horizon,model,sample,observations,balanced_accuracy,mcc,brier,return_mae,return_correlation,
        drawdown_mae,volatility_mae,baseline_balanced_accuracy,improvement_vs_baseline,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        score_rows,
    )
    con.execute(
        """INSERT INTO market_forecast_runs
        (run_id,created_at,state_run_id,date_from,date_to,frozen_train_to,frozen_validation_to,holdout_from,
        observations,methodology_version,production_unchanged,probability_gate_unchanged,immutable,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            state_run_id,
            frame.trade_date.min(),
            frame.trade_date.max(),
            frame.iloc[train_end - 1].trade_date,
            frame.iloc[validation_end - 1].trade_date,
            frame.iloc[validation_end].trade_date,
            len(frame),
            VERSION,
            True,
            True,
            True,
            "completed",
            json.dumps({"split": "60/20/20 chronological", "nested_feature_selection": "fixed_predeclared"}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    summary = con.execute(
        """SELECT count(*), count(DISTINCT horizon), count(DISTINCT model),
        max(improvement_vs_baseline), avg(balanced_accuracy)
        FROM market_forecast_scorecards WHERE run_id=? AND sample='frozen_holdout'""",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "scorecards": summary[0],
        "horizons": summary[1],
        "models": summary[2],
        "best_holdout_improvement": summary[3],
        "mean_holdout_balanced_accuracy": summary[4],
        "status": "completed",
        "probability_published": False,
    }
