"""Frozen temporal validation of deliberately simple regularized models."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import (
    ElasticNet,
    HuberRegressor,
    Lasso,
    QuantileRegressor,
    Ridge,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .schema import DDL

VERSION = "regularized-return-models-v5-named-scorecard"
MODELS = {
    "ridge": Ridge,
    "lasso": Lasso,
    "elastic_net": ElasticNet,
    "huber": HuberRegressor,
    "quantile_q50": QuantileRegressor,
}
FEATURES = (
    "return_5",
    "return_20",
    "return_60",
    "return_120",
    "ma_distance_20",
    "drawdown_250",
    "trend_slope_20",
    "trend_consistency_20",
    "realized_vol_20",
    "downside_vol_20",
    "volatility_ratio",
    "market_return_20",
    "market_vol_20",
    "momentum_rank",
    "volatility_rank",
)


def ensure_schema(con: Any) -> None:
    con.execute(DDL)
    columns = {row[0] for row in con.execute("DESCRIBE statistical_model_scorecards").fetchall()}
    for column in ("ci_low", "ci_high"):
        if column not in columns:
            con.execute(f"ALTER TABLE statistical_model_scorecards ADD COLUMN {column} DOUBLE")


def _estimator(name: str, alpha: float = 1.0):
    if name == "ridge":
        return Ridge(alpha=alpha)
    if name == "lasso":
        return Lasso(alpha=alpha, max_iter=5000)
    if name == "elastic_net":
        return ElasticNet(alpha=alpha, l1_ratio=0.5, max_iter=5000)
    if name == "huber":
        return HuberRegressor(alpha=alpha, max_iter=500)
    if name == "quantile_q50":
        return QuantileRegressor(alpha=alpha, quantile=0.5, solver="highs")
    raise ValueError(name)


def _fit_one(
    sample: pd.DataFrame, name: str, baseline_predictions: pd.DataFrame | None = None
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    dates = np.sort(sample.trade_date.unique())
    train_end, calibration_end = dates[int(len(dates) * 0.60)], dates[int(len(dates) * 0.80)]
    train, calibration, test = (
        sample[sample.trade_date <= train_end],
        sample[(sample.trade_date > train_end) & (sample.trade_date <= calibration_end)],
        sample[sample.trade_date > calibration_end],
    )
    best = None
    for alpha in (0.001, 0.01, 0.1, 1.0, 10.0):
        pipe = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), _estimator(name, alpha))
        pipe.fit(train[list(FEATURES)], train.forward_return)
        mae = float(np.mean(np.abs(calibration.forward_return - pipe.predict(calibration[list(FEATURES)]))))
        if best is None or mae < best[0]:
            best = (mae, alpha)
    development = sample[sample.trade_date <= calibration_end]
    pipe = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), _estimator(name, best[1]))
    pipe.fit(development[list(FEATURES)], development.forward_return)
    prediction = pipe.predict(test[list(FEATURES)])
    baseline = np.zeros(len(test))
    if baseline_predictions is not None:
        lookup = (
            baseline_predictions.assign(evaluation_date=pd.to_datetime(baseline_predictions.evaluation_date))
            .set_index("evaluation_date")
            .prediction
        )
        baseline = pd.to_datetime(test.trade_date).map(lookup).fillna(0).to_numpy(float)
    error = np.abs(test.forward_return.to_numpy() - prediction)
    baseline_error = np.abs(test.forward_return.to_numpy() - baseline)
    years = (
        pd.DataFrame({"year": pd.to_datetime(test.trade_date).dt.year, "gain": baseline_error - error})
        .groupby("year")
        .gain.mean()
    )
    model = pipe[-1]
    rng = np.random.default_rng(42)
    gains = baseline_error - error
    draws = np.array([rng.choice(gains, len(gains), replace=True).mean() for _ in range(400)])
    ci_low, ci_high = np.quantile(draws, [0.025, 0.975])
    coefficients = pd.DataFrame({"feature": FEATURES, "coefficient": model.coef_})
    fold_signs = []
    for fraction in (0.5, 0.65, 0.8):
        fold = sample.iloc[: max(100, int(len(sample) * fraction))]
        fold_pipe = make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(), _estimator(name, best[1])
        )
        fold_pipe.fit(fold[list(FEATURES)], fold.forward_return)
        fold_signs.append(np.sign(fold_pipe[-1].coef_))
    sign_matrix = np.asarray(fold_signs)
    nonzero = np.any(sign_matrix != 0, axis=0)
    sign_stability = float(
        np.mean(np.abs(np.mean(sign_matrix[:, nonzero], axis=0))) if nonzero.any() else 1.0
    )
    metrics = {
        "alpha": best[1],
        "oos_n": len(test),
        "mae": float(error.mean()),
        "baseline_mae": float(baseline_error.mean()),
        "improvement": float((baseline_error.mean() - error.mean()) / baseline_error.mean()),
        "direction_accuracy": float(np.mean(np.sign(prediction) == np.sign(test.forward_return))),
        "brier": None,
        "rank_ic": float(
            pd.Series(prediction).corr(pd.Series(test.forward_return.to_numpy()), method="spearman")
        ),
        "sign_stability": sign_stability,
        "subperiod_stability": float((years > 0).mean()),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
    }
    output = pd.DataFrame(
        {
            "trade_date": test.trade_date,
            "actual": test.forward_return,
            "prediction": prediction,
            "baseline_prediction": baseline,
            "split": "test",
            "train_end": calibration_end,
            "probability_up": np.nan,
        }
    )
    return metrics, output, coefficients


def run_statistical_models(
    con: Any,
    tickers: tuple[str, ...] = ("SBERP", "LKOH", "LSNGP", "MOEX", "MTSS", "PHOR", "TATNP", "TRNFP", "X5"),
) -> dict[str, Any]:
    ensure_schema(con)
    feature_run = con.execute(
        "SELECT run_id,version FROM predictive_feature_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    target_run = con.execute(
        "SELECT run_id FROM predictive_target_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not feature_run or not target_run:
        raise ValueError("completed feature and target runs required")
    signature = f"{VERSION}|{feature_run[0]}|{target_run[0]}|{tickers}"
    run_id = hashlib.sha256(signature.encode()).hexdigest()[:20]
    existing = con.execute(
        "select status,models,predictions from statistical_model_runs where run_id=?", [run_id]
    ).fetchone()
    if existing and existing[0] == "completed":
        return {
            "run_id": run_id,
            "status": existing[0],
            "models": existing[1],
            "predictions": existing[2],
            "cached": True,
        }
    panel = con.execute(
        "SELECT f.*,t.horizon,t.forward_return FROM predictive_feature_store f "
        "JOIN predictive_return_targets t ON f.trade_date=t.evaluation_date AND f.secid=t.secid "
        "WHERE f.run_id=? AND t.run_id=? AND f.secid IN (SELECT unnest(?)) "
        "AND t.horizon IN (5,20,60,120,250)",
        [feature_run[0], target_run[0], list(tickers)],
    ).df()
    baseline_run = con.execute(
        "SELECT run_id FROM predictive_baseline_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()[0]
    registry = []
    predictions = []
    scorecards = []
    coefficients = []
    for (secid, horizon), sample in panel.groupby(["secid", "horizon"]):
        sample = sample.dropna(subset=["forward_return"]).sort_values("trade_date")
        if len(sample) < 300:
            continue
        champion = con.execute(
            "SELECT model FROM predictive_baseline_scorecards WHERE run_id=? AND secid=? "
            "AND horizon=? AND rank=1",
            [baseline_run, secid, int(horizon)],
        ).fetchone()
        baseline = con.execute(
            "SELECT evaluation_date,prediction FROM predictive_baseline_predictions WHERE run_id=? "
            "AND secid=? AND horizon=? AND model=?",
            [baseline_run, secid, int(horizon), champion[0] if champion else "no_change"],
        ).df()
        for name in MODELS:
            metrics, pred, coef = _fit_one(sample, name, baseline)
            model_id = f"{secid}-{horizon}-{name}-{VERSION}"
            status = (
                "VALIDATED"
                if metrics["oos_n"] >= 100
                and metrics["improvement"] >= 0.02
                and metrics["subperiod_stability"] >= 0.6
                and metrics["ci_low"] > 0
                and metrics["sign_stability"] >= 0.7
                else ("WEAK" if metrics["improvement"] > 0 else "FAILED")
            )
            registry.append(
                [
                    run_id,
                    model_id,
                    secid,
                    horizon,
                    "return",
                    name,
                    feature_run[1],
                    pred.train_end.iloc[0],
                    pred.train_end.iloc[0],
                    pred.trade_date.min(),
                    pred.trade_date.max(),
                    json.dumps({"alpha": metrics["alpha"]}),
                    status,
                    None,
                    False,
                ]
            )
            pred.insert(0, "model_id", model_id)
            pred.insert(0, "run_id", run_id)
            pred.insert(2, "secid", secid)
            pred.insert(3, "horizon", horizon)
            pred.insert(5, "target", "return")
            pred["immutable"] = True
            predictions.append(pred)
            scorecards.append(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    "secid": secid,
                    "horizon": horizon,
                    "target": "return",
                    "model": name,
                    "oos_n": metrics["oos_n"],
                    "mae": metrics["mae"],
                    "baseline_mae": metrics["baseline_mae"],
                    "improvement": metrics["improvement"],
                    "direction_accuracy": metrics["direction_accuracy"],
                    "brier": None,
                    "rank_ic": metrics["rank_ic"],
                    "sign_stability": metrics["sign_stability"],
                    "subperiod_stability": metrics["subperiod_stability"],
                    "status": status,
                    "probability_allowed": False,
                    "ci_low": metrics["ci_low"],
                    "ci_high": metrics["ci_high"],
                    "details_json": json.dumps({"probability_gate": "closed", "production_changes": 0}),
                }
            )
            coef.insert(0, "model_id", model_id)
            coef.insert(0, "run_id", run_id)
            coef["standardized"] = True
            coefficients.append(coef)
    frames = [
        pd.DataFrame(
            registry,
            columns=(
                "run_id",
                "model_id",
                "secid",
                "horizon",
                "target",
                "model",
                "features_version",
                "training_cutoff",
                "calibration_cutoff",
                "test_start",
                "test_end",
                "hyperparameters_json",
                "status",
                "artifact_location",
                "automatic_promotion",
            ),
        ),
        pd.concat(predictions, ignore_index=True),
        pd.DataFrame(scorecards)[
            [
                "run_id",
                "model_id",
                "secid",
                "horizon",
                "target",
                "model",
                "oos_n",
                "mae",
                "baseline_mae",
                "improvement",
                "direction_accuracy",
                "brier",
                "rank_ic",
                "sign_stability",
                "subperiod_stability",
                "status",
                "probability_allowed",
                "ci_low",
                "ci_high",
                "details_json",
            ]
        ],
        pd.concat(coefficients, ignore_index=True),
    ]
    con.execute("BEGIN")
    try:
        con.execute(
            "INSERT OR REPLACE INTO statistical_model_runs "
            "VALUES (?,?,?,?,current_timestamp,NULL,'running',0,0,?,true)",
            [run_id, VERSION, feature_run[0], target_run[0], json.dumps({"production_changes": 0})],
        )
        for table, frame in zip(
            (
                "statistical_model_registry",
                "statistical_model_predictions",
                "statistical_model_scorecards",
                "statistical_model_coefficients",
            ),
            frames,
            strict=True,
        ):
            con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
            con.register("_x", frame)
            cols = ",".join(frame.columns)
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _x")
            con.unregister("_x")
        con.execute(
            "UPDATE statistical_model_runs SET finished_at=current_timestamp,status='completed',"
            "models=?,predictions=? WHERE run_id=?",
            [len(frames[0]), len(frames[1]), run_id],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "run_id": run_id,
        "status": "completed",
        "models": len(frames[0]),
        "predictions": len(frames[1]),
        "cached": False,
    }
