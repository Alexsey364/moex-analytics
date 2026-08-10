"""Stage 53 temporal quantile, conformal and downside distribution research."""

from __future__ import annotations

import hashlib
import json
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from moex_analytics.ranking_engine.core import FEATURES, PORTFOLIO, _feature_panel

from .schema import DDL

VERSION = "forward-return-distributions-v3-frozen"
QUANTILES = (.05, .10, .25, .50, .75, .90, .95)
Q_COLUMNS = tuple(f"q{int(q * 100):02d}" for q in QUANTILES)
METHODS = ("historical_unconditional", "rolling_volatility", "residual_bootstrap",
           "gradient_boosting_quantile", "analog_distribution", "regime_conditioned_empirical")


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _pinball(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> float:
    error = actual - predicted
    return float(np.mean(np.maximum(quantile * error, (quantile - 1) * error)))


def distribution_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    if frame.empty:
        return {"observations": 0}
    actual = frame.actual_return.to_numpy(float)
    losses = [_pinball(actual, frame[column].to_numpy(float), quantile)
              for column, quantile in zip(Q_COLUMNS, QUANTILES, strict=True)]
    return {"observations": len(frame), "median_mae": float(np.mean(np.abs(actual - frame.q50))),
        "pinball_loss": float(np.mean(losses)), "crps_approx": float(2 * np.mean(losses)),
        "coverage_50": float(((actual >= frame.q25) & (actual <= frame.q75)).mean()),
        "coverage_80": float(((actual >= frame.q10) & (actual <= frame.q90)).mean()),
        "coverage_90": float(((actual >= frame.q05) & (actual <= frame.q95)).mean()),
        "tail_10_coverage": float((actual <= frame.q10).mean()),
        "tail_5_coverage": float((actual <= frame.q05).mean())}


def _historical_quantiles(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    values = np.quantile(train.actual_return, QUANTILES)
    return np.tile(values, (len(target), 1))


def _rolling_volatility(target: pd.DataFrame, horizon: int) -> np.ndarray:
    sigma = np.maximum(target.volatility_20.to_numpy(float), 0.0) * np.sqrt(horizon / 252)
    normal = np.array([NormalDist().inv_cdf(q) for q in QUANTILES])
    return sigma[:, None] * normal[None, :]


def _residual_bootstrap(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    model.fit(train[list(FEATURES)], train.actual_return)
    residual = train.actual_return.to_numpy(float) - model.predict(train[list(FEATURES)])
    return model.predict(target[list(FEATURES)])[:, None] + np.quantile(residual, QUANTILES)[None, :]


def _gradient_quantiles(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    columns = []
    for quantile in QUANTILES:
        model = HistGradientBoostingRegressor(loss="quantile", quantile=quantile, max_iter=80,
            max_leaf_nodes=15, learning_rate=.05, min_samples_leaf=30, random_state=42)
        model.fit(train[list(FEATURES)], train.actual_return)
        columns.append(model.predict(target[list(FEATURES)]))
    return np.sort(np.column_stack(columns), axis=1)


def _analog_quantiles(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    scaler = StandardScaler().fit(train[list(FEATURES)])
    x_train = scaler.transform(train[list(FEATURES)])
    x_target = scaler.transform(target[list(FEATURES)])
    neighbors = NearestNeighbors(n_neighbors=min(50, len(train)), algorithm="auto").fit(x_train)
    indices = neighbors.kneighbors(x_target, return_distance=False)
    outcomes = train.actual_return.to_numpy(float)[indices]
    return np.quantile(outcomes, QUANTILES, axis=1).T


def _regime_quantiles(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    train = train.copy()
    target = target.copy()
    train["state"] = np.select([train.momentum_20 > .03, train.momentum_20 < -.03],
                                ["trend_up", "trend_down"], default="neutral")
    target["state"] = np.select([target.momentum_20 > .03, target.momentum_20 < -.03],
                                 ["trend_up", "trend_down"], default="neutral")
    fallback = np.quantile(train.actual_return, QUANTILES)
    mapping = {state: np.quantile(group.actual_return, QUANTILES)
               for state, group in train.groupby("state") if len(group) >= 50}
    return np.vstack([mapping.get(state, fallback) for state in target.state])


def _predict(method: str, train: pd.DataFrame, target: pd.DataFrame, horizon: int) -> np.ndarray:
    if method == "historical_unconditional":
        return _historical_quantiles(train, target)
    if method == "rolling_volatility":
        return _rolling_volatility(target, horizon)
    if method == "residual_bootstrap":
        return _residual_bootstrap(train, target)
    if method == "gradient_boosting_quantile":
        return _gradient_quantiles(train, target)
    if method == "analog_distribution":
        return _analog_quantiles(train, target)
    if method == "regime_conditioned_empirical":
        return _regime_quantiles(train, target)
    raise ValueError(f"unknown distribution method: {method}")


def _prediction_frame(target: pd.DataFrame, values: np.ndarray) -> pd.DataFrame:
    result = target[["trade_date", "secid", "actual_return"]].reset_index(drop=True).copy()
    for position, column in enumerate(Q_COLUMNS):
        result[column] = values[:, position]
    return result


def _conformal_radii(validation: pd.DataFrame) -> tuple[float, float, float]:
    residual = np.abs(validation.actual_return - validation.q50).to_numpy(float)
    return tuple(float(np.quantile(residual, q, method="higher")) for q in (.50, .80, .90))


def _selection_score(metrics: dict[str, float | int]) -> float:
    return float(metrics["pinball_loss"] + abs(metrics["coverage_50"] - .50) * .02 +
                 abs(metrics["coverage_80"] - .80) * .02 +
                 abs(metrics["coverage_90"] - .90) * .02)


def _bucket(center: float, threshold: float, direction: str) -> str:
    if direction == "up":
        return "elevated" if center > threshold else "limited" if center < 0 else "mixed"
    return "elevated" if center < -threshold else "limited" if center > 0 else "mixed"


def _insert(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    relation = f"_{table}"
    con.register(relation, frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {relation}")
    con.unregister(relation)


def run_distribution_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute("SELECT run_id,target_run_id,cutoff,train_end,validation_end,holdout_start "
                         "FROM ranking_research_runs WHERE status='completed' "
                         "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not source:
        raise ValueError("completed purged Stage 52 ranking run is required")
    ranking_run, target_run, cutoff, train_end, validation_end, holdout_start = source
    train_boundary = pd.Timestamp(train_end)
    validation_boundary = pd.Timestamp(validation_end)
    holdout_boundary = pd.Timestamp(holdout_start)
    run_id = hashlib.sha256(f"{VERSION}|{ranking_run}|{target_run}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,prediction_rows FROM distribution_research_runs WHERE run_id=?",
                         [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "predictions": cached[1], "cached": True}
    features = _feature_panel(con)
    labels = con.execute("SELECT trade_date,exit_date,secid,horizon,total_return AS actual_return "
                         "FROM predictive_target_observations WHERE run_id=? AND secid<>'IMOEX'",
                         [target_run]).df()
    panel = features.merge(labels, on=["trade_date", "secid"], how="inner")
    con.execute("INSERT OR REPLACE INTO distribution_research_runs "
        "(run_id,ranking_run_id,target_run_id,dataset_version,cutoff,train_end,validation_end,"
        "holdout_start,started_at,status,prediction_rows,details_json,immutable) "
        "VALUES (?,?,?,?,?,?,?,?,current_timestamp,'running',0,?,true)",
        [run_id, ranking_run, target_run, VERSION, cutoff, train_end, validation_end, holdout_start,
         json.dumps({"selection": "validation_only", "production_changes": 0})])
    try:
        policies, predictions, scorecards, current_rows = [], [], [], []
        latest_features = features[features.trade_date == features.trade_date.max()].copy()
        prices = con.execute("SELECT canonical_secid AS secid,close FROM canonical_daily_prices "
                             "QUALIFY row_number() OVER(PARTITION BY canonical_secid "
                             "ORDER BY trade_date DESC)=1").df().set_index("secid").close
        for horizon in sorted(panel.horizon.unique()):
            data = panel[panel.horizon == horizon].dropna(subset=[*FEATURES, "actual_return"])
            train = data[(data.trade_date <= train_boundary) &
                         (data.exit_date <= train_boundary)]
            validation = data[(data.trade_date > train_boundary) &
                              (data.exit_date <= validation_boundary)]
            holdout = data[data.trade_date >= holdout_boundary]
            candidates: dict[str, tuple[pd.DataFrame, dict[str, float | int]]] = {}
            for method in METHODS:
                frame = _prediction_frame(validation, _predict(method, train, validation, int(horizon)))
                metrics = distribution_metrics(frame)
                candidates[method] = (frame, metrics)
            selected = min(METHODS, key=lambda name: _selection_score(candidates[name][1]))
            selected_validation, _ = candidates[selected]
            radii = _conformal_radii(selected_validation)
            policy_hash = hashlib.sha256(
                f"{run_id}|{horizon}|{selected}|{train_end}|{validation_end}|{radii}".encode()
            ).hexdigest()
            for method in METHODS:
                metrics = candidates[method][1]
                policies.append([run_id, int(horizon), method, metrics["pinball_loss"],
                    metrics["coverage_50"], metrics["coverage_80"], metrics["coverage_90"],
                    _selection_score(metrics), method == selected,
                    policy_hash if method == selected else hashlib.sha256(
                        f"{run_id}|{horizon}|{method}|validation".encode()).hexdigest(),
                    *(radii if method == selected else (None, None, None)),
                    "validation_only", True])
            development = data[(data.trade_date <= validation_boundary) &
                               (data.exit_date <= validation_boundary)]
            holdout_frame = _prediction_frame(
                holdout, _predict(selected, development, holdout, int(horizon))
            )
            current_values = _predict(selected, development, latest_features, int(horizon))
            holdout_frame["conformal50_low"] = holdout_frame.q50 - radii[0]
            holdout_frame["conformal50_high"] = holdout_frame.q50 + radii[0]
            holdout_frame["conformal80_low"] = holdout_frame.q50 - radii[1]
            holdout_frame["conformal80_high"] = holdout_frame.q50 + radii[1]
            holdout_frame["conformal90_low"] = holdout_frame.q50 - radii[2]
            holdout_frame["conformal90_high"] = holdout_frame.q50 + radii[2]
            metrics = distribution_metrics(holdout_frame)
            actual = holdout_frame.actual_return
            metrics["coverage_50"] = float(
                ((actual >= holdout_frame.conformal50_low) &
                 (actual <= holdout_frame.conformal50_high)).mean()
            )
            metrics["coverage_80"] = float(
                ((actual >= holdout_frame.conformal80_low) &
                 (actual <= holdout_frame.conformal80_high)).mean()
            )
            metrics["coverage_90"] = float(
                ((actual >= holdout_frame.conformal90_low) &
                 (actual <= holdout_frame.conformal90_high)).mean()
            )
            baseline = distribution_metrics(_prediction_frame(
                holdout, _predict("historical_unconditional", development, holdout, int(horizon))))
            scorecards.append([run_id, int(horizon), selected, "untouched_holdout_frozen",
                metrics["observations"], metrics["median_mae"], metrics["pinball_loss"],
                metrics["crps_approx"], metrics["coverage_50"], metrics["coverage_80"],
                metrics["coverage_90"], metrics["tail_10_coverage"], metrics["tail_5_coverage"],
                baseline["pinball_loss"] - metrics["pinball_loss"],
                "SHADOW_CANDIDATE" if metrics["pinball_loss"] < baseline["pinball_loss"]
                else "NO_EVIDENCE"])
            holdout_frame.insert(0, "run_id", run_id)
            holdout_frame["horizon"] = int(horizon)
            holdout_frame["method"] = selected
            holdout_frame["current_price"] = holdout_frame.secid.map(prices)
            holdout_frame["expected_upside"] = (holdout_frame.q75 + holdout_frame.q90) / 2
            holdout_frame["expected_downside"] = (holdout_frame.q10 + holdout_frame.q25) / 2
            holdout_frame["upside_downside_ratio"] = holdout_frame.expected_upside / (
                holdout_frame.expected_downside.abs() + 1e-9)
            holdout_frame["expected_shortfall_10"] = holdout_frame.q10
            holdout_frame["expected_shortfall_5"] = holdout_frame.q05
            holdout_frame["material_up_5_bucket"] = holdout_frame.q50.map(lambda x: _bucket(x, .05, "up"))
            holdout_frame["material_down_5_bucket"] = holdout_frame.q50.map(
                lambda x: _bucket(x, .05, "down")
            )
            holdout_frame["probability_allowed"] = False
            holdout_frame["sample_type"] = "untouched_holdout_frozen"
            holdout_frame["policy_hash"] = policy_hash
            holdout_frame["history_end"] = validation_end
            holdout_frame["immutable"] = True
            predictions.append(holdout_frame[["run_id", "trade_date", "secid", "horizon", "method",
                *Q_COLUMNS, "conformal50_low", "conformal50_high", "conformal80_low",
                "conformal80_high", "conformal90_low", "conformal90_high", "actual_return",
                "current_price", "expected_upside", "expected_downside", "upside_downside_ratio",
                "expected_shortfall_10", "expected_shortfall_5", "material_up_5_bucket",
                "material_down_5_bucket", "probability_allowed", "sample_type", "policy_hash",
                "history_end", "immutable"]])
            for idx, row in latest_features.reset_index(drop=True).iterrows():
                if row.secid not in PORTFOLIO or row.secid not in prices.index:
                    continue
                values = current_values[idx]
                price = float(prices[row.secid])
                ratio = float((values[4] + values[5]) / 2 / (abs((values[1] + values[2]) / 2) + 1e-9))
                current_rows.append([run_id, pd.Timestamp(row.trade_date), row.secid, int(horizon),
                    selected, price, *[float(x) for x in values],
                    *[float(price * (1 + x)) for x in values], float(values[1]), ratio,
                    _bucket(float(values[3]), .05, "up"), _bucket(float(values[3]), .05, "down"),
                    False, "research_only", "historical/model-implied range; not a price target", True])
        policy_frame = pd.DataFrame(policies, columns=("run_id", "horizon", "method",
            "validation_pinball", "validation_coverage_50", "validation_coverage_80",
            "validation_coverage_90", "selection_score", "selected", "policy_hash",
            "calibration_q50", "calibration_q80", "calibration_q90", "selection_sample", "immutable"))
        prediction_frame = pd.concat(predictions, ignore_index=True)
        score_frame = pd.DataFrame(scorecards, columns=("run_id", "horizon", "method", "sample_type",
            "observations", "median_mae", "pinball_loss", "crps_approx", "coverage_50",
            "coverage_80", "coverage_90", "tail_10_coverage", "tail_5_coverage", "baseline_delta",
            "status"))
        current_frame = pd.DataFrame(current_rows, columns=("run_id", "cutoff", "secid", "horizon",
            "method", "current_price", "q05_return", "q10_return", "q25_return", "q50_return",
            "q75_return", "q90_return", "q95_return", "q05_price", "q10_price", "q25_price",
            "q50_price", "q75_price", "q90_price", "q95_price", "downside_10",
            "upside_downside_ratio", "material_up_5_bucket", "material_down_5_bucket",
            "probability_allowed", "status", "reason", "immutable"))
        for table, frame in (("distribution_method_policies", policy_frame),
                             ("distribution_oos_predictions", prediction_frame),
                             ("distribution_scorecards", score_frame),
                             ("current_return_distributions", current_frame)):
            _insert(con, table, frame)
        details = {"temporal_conformal": True, "selection_touched_holdout": False,
                   "probability_published": False, "price_target_published": False,
                   "production_changes": 0, "methods": METHODS}
        con.execute("UPDATE distribution_research_runs SET finished_at=current_timestamp,"
                    "status='completed',prediction_rows=?,details_json=? WHERE run_id=?",
                    [len(prediction_frame), json.dumps(details), run_id])
        return {"run_id": run_id, "status": "completed", "predictions": len(prediction_frame),
                "current_rows": len(current_frame), "cached": False}
    except Exception as exc:
        con.execute("UPDATE distribution_research_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def distribution_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,train_end,validation_end,holdout_start,"
                      "prediction_rows,details_json FROM distribution_research_runs "
                      "ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "train_end", "validation_end",
                    "holdout_start", "predictions", "details"), row, strict=True))
