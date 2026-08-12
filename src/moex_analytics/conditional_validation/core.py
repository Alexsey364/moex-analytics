"""Walk-forward replay, frozen calibration and reliability gates (Stage 100)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression

from moex_analytics.analog_projection.core import HORIZONS
from moex_analytics.conditional_forecast.core import effective_sample_size, weighted_quantile
from moex_analytics.conditional_similarity.core import (
    _total_distance,
    build_state_panel,
    family_distances,
    similarity_score,
)
from moex_analytics.conditioned_stock_forecasting.core import SECIDS
from moex_analytics.regime_conditioning.core import (
    classify_regimes,
    regime_compatibility,
)

from .schema import ensure_schema

VERSION = "frozen-oos-calibration-v1.1"
VARIANTS = {
    "price_only": ("price",),
    "price_volatility": ("price", "volatility"),
    "plus_market": ("price", "volatility", "market"),
    "plus_rates_macro": ("price", "volatility", "market", "rates"),
    "plus_fx_commodities": ("price", "volatility", "market", "rates", "fx_commodities"),
    "plus_sector": ("price", "volatility", "market", "rates", "fx_commodities", "sector"),
    "plus_fundamental": (
        "price", "volatility", "market", "rates", "fx_commodities", "sector", "fundamental",
    ),
    "plus_regime": (
        "price", "volatility", "market", "rates", "fx_commodities", "sector", "fundamental",
    ),
    "plus_weighting": (
        "price", "volatility", "market", "rates", "fx_commodities", "sector", "fundamental",
    ),
}


def _config() -> tuple[dict[str, Any], dict[str, float], str]:
    path = Path(__file__).resolve().parents[3] / "config" / "conditional_forecasting.yaml"
    raw = path.read_bytes()
    config = yaml.safe_load(raw)
    return (
        config["historical_validation"],
        config["conditional_similarity"]["family_weights"],
        hashlib.sha256(raw).hexdigest(),
    )


def _phase(position: int, length: int, train_fraction: float, calibration_fraction: float) -> str:
    train_end = int(length * train_fraction)
    calibration_end = int(length * (train_fraction + calibration_fraction))
    if position < train_end:
        return "train"
    if position < calibration_end:
        return "calibration"
    return "frozen_test"


def _native_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [
        [value.item() if isinstance(value, np.generic) else value for value in row]
        for row in rows
    ]


def _episode_dates(
    scores: pd.Series, positions: dict[pd.Timestamp, int], separation: int = 20
) -> list[pd.Timestamp]:
    selected: list[pd.Timestamp] = []
    ranked = scores.rename("score").to_frame()
    ranked["date_tiebreak"] = ranked.index
    ranked = ranked.sort_values(["score", "date_tiebreak"], ascending=[False, True], kind="mergesort")
    for candidate in ranked.index:
        if all(abs(positions[candidate] - positions[prior]) >= separation for prior in selected):
            selected.append(candidate)
    return selected


def _regime_bucket(row: pd.Series) -> str:
    if row.market_trend == "crisis":
        return "crisis"
    if row.volatility_regime in {"elevated", "extreme"}:
        return "high_vol"
    return "normal"


def _forecast_one(
    frame: pd.DataFrame,
    distances: pd.DataFrame,
    regimes: pd.DataFrame,
    position: int,
    horizon: int,
    variant: str,
    family_weights: dict[str, float],
    threshold: float,
    precomputed_scores: pd.Series | None = None,
    precomputed_compatibility: pd.Series | None = None,
) -> dict[str, Any]:
    candidate_end = position - horizon
    if candidate_end < 1:
        return {"status": "insufficient_history"}
    if precomputed_scores is None:
        selected_families = VARIANTS[variant]
        selected_weights = {name: family_weights[name] for name in selected_families}
        total = _total_distance(distances, selected_weights)
        scores = pd.Series(similarity_score(total), index=total.index)
    else:
        scores = precomputed_scores
    scores = scores.iloc[: candidate_end + 1].dropna()
    scores = scores[scores >= threshold]
    if scores.empty:
        return {"status": "insufficient_conditional_history"}
    positions = {date: index for index, date in enumerate(frame.index[: candidate_end + 1])}
    dates = _episode_dates(scores, positions)
    if variant in {"plus_regime", "plus_weighting"}:
        if precomputed_compatibility is None:
            current_state = regimes.iloc[position].to_dict()
            dates = [
                date
                for date in dates
                if regime_compatibility(current_state, regimes.loc[date].to_dict()) >= 0.80
            ]
        else:
            dates = [date for date in dates if precomputed_compatibility.loc[date] >= 0.80]
    forward = frame.close.shift(-horizon) / frame.close - 1
    dates = [date for date in dates if pd.notna(forward.loc[date])]
    if not dates:
        return {"status": "insufficient_conditional_history"}
    outcomes = forward.loc[dates].to_numpy(float)
    if variant == "plus_weighting":
        weights = (scores.loc[dates].to_numpy(float) / 100.0) ** 2
    else:
        weights = np.ones(len(dates), dtype=float)
    weights /= weights.sum()
    historical = forward.iloc[: candidate_end + 1].dropna().to_numpy(float)
    current_momentum = (
        float(frame.return_20.iloc[position])
        if pd.notna(frame.return_20.iloc[position])
        else 0.0
    )
    scaled_momentum = float(np.clip(current_momentum * np.sqrt(horizon / 20), -0.50, 0.50))
    return {
        "status": "ready",
        "raw_n": len(dates),
        "ess": effective_sample_size(weights),
        "prediction": weighted_quantile(outcomes, weights, 0.5),
        "low60": weighted_quantile(outcomes, weights, 0.2),
        "high60": weighted_quantile(outcomes, weights, 0.8),
        "low80": weighted_quantile(outcomes, weights, 0.1),
        "high80": weighted_quantile(outcomes, weights, 0.9),
        "up": float(weights[outcomes > 0].sum()),
        "unconditional": float(np.median(historical)) if len(historical) else 0.0,
        "momentum": scaled_momentum,
        "mean_reversion": -scaled_momentum,
        "history_end": frame.index[candidate_end],
    }


def _fit_probability(scores: pd.Series, actual: pd.Series, seed: int) -> tuple[str, float, float]:
    valid = scores.notna() & actual.notna()
    values = scores[valid].clip(1e-6, 1 - 1e-6)
    labels = (actual[valid] > 0).astype(int)
    if len(values) < 20 or labels.nunique() < 2:
        return "insufficient_identity", 1.0, 0.0
    logits = np.log(values.to_numpy() / (1 - values.to_numpy())).reshape(-1, 1)
    model = LogisticRegression(random_state=seed, solver="lbfgs").fit(logits, labels.to_numpy())
    return "logistic_calibration", float(model.coef_[0, 0]), float(model.intercept_[0])


def _apply_probability(values: pd.Series, method: str, coefficient: float, intercept: float) -> np.ndarray:
    clipped = values.clip(1e-6, 1 - 1e-6).to_numpy(float)
    if method != "logistic_calibration":
        return clipped
    logits = np.log(clipped / (1 - clipped))
    return 1 / (1 + np.exp(-(coefficient * logits + intercept)))


def _reliability(
    group: pd.DataFrame,
    metrics: dict[str, float | None],
    config: dict[str, Any],
) -> tuple[str, str]:
    failures = []
    if len(group) < int(config["minimum_oos_forecasts"]):
        failures.append("insufficient frozen OOS N")
    if float(group.effective_sample_size.median()) < float(config["minimum_effective_sample"]):
        failures.append("median ESS below threshold")
    baseline = min(
        value
        for value in (
            metrics["no_change_mae"], metrics["unconditional_mae"], metrics["momentum_mae"],
            metrics["mean_reversion_mae"],
        )
        if value is not None
    )
    if metrics["mae"] is None or metrics["mae"] >= baseline:
        failures.append("does not beat best baseline")
    if abs(float(metrics["coverage60"]) - float(config["target_60_coverage"])) > float(
        config["coverage_60_tolerance"]
    ):
        failures.append("60% interval coverage outside frozen tolerance")
    if abs(float(metrics["coverage80"]) - float(config["target_80_coverage"])) > float(
        config["coverage_80_tolerance"]
    ):
        failures.append("80% interval coverage outside frozen tolerance")
    midpoint = len(group) // 2
    if midpoint:
        halves = (group.iloc[:midpoint], group.iloc[midpoint:])
        if any(
            np.mean(np.abs(part.predicted_return - part.actual_return))
            >= np.mean(np.abs(part.no_change_return - part.actual_return))
            for part in halves
        ):
            failures.append("subperiod stability gate failed")
    if not failures:
        return "VALIDATED", "all frozen OOS gates passed"
    if len(group) >= 10:
        return "WEAK", "; ".join(failures)
    return "UNVALIDATED", "; ".join(failures)


def _metric_rows(
    replay: pd.DataFrame, run_id: str, config: dict[str, Any]
) -> tuple[list[list[Any]], list[list[Any]], list[list[Any]]]:
    mappings, scorecards, regime_rows = [], [], []
    for (secid, horizon, variant), family in replay.groupby(["secid", "horizon", "variant"]):
        calibration_all = family[family.phase == "calibration"]
        thresholds = sorted(calibration_all.similarity_threshold.unique())
        if not thresholds:
            continue
        losses = {
            threshold: np.mean(
                np.abs(
                    calibration_all[calibration_all.similarity_threshold == threshold].predicted_return
                    - calibration_all[calibration_all.similarity_threshold == threshold].actual_return
                )
            )
            for threshold in thresholds
            if len(calibration_all[calibration_all.similarity_threshold == threshold])
        }
        selected = min(losses, key=lambda value: (losses[value], -value))
        calibration = calibration_all[calibration_all.similarity_threshold == selected]
        residual = np.abs(calibration.actual_return - calibration.predicted_return)
        radius60 = float(np.quantile(residual, 0.60, method="higher")) if len(residual) else None
        radius80 = float(np.quantile(residual, 0.80, method="higher")) if len(residual) else None
        probability_method, coefficient, intercept = _fit_probability(
            calibration.raw_up_frequency, calibration.actual_return, int(config["random_seed"])
        )
        mappings.append([
            run_id, secid, horizon, variant, selected, len(calibration), radius60, radius80,
            probability_method, coefficient, intercept, True, True,
        ])
        test = family[
            (family.phase == "frozen_test") & (family.similarity_threshold == selected)
        ].sort_values("evaluation_date")
        if test.empty or radius60 is None or radius80 is None:
            continue
        error = test.predicted_return - test.actual_return
        probabilities = _apply_probability(test.raw_up_frequency, probability_method, coefficient, intercept)
        actual_up = (test.actual_return > 0).astype(float).to_numpy()
        metrics: dict[str, float | None] = {
            "mae": float(np.mean(np.abs(error))),
            "median_ae": float(np.median(np.abs(error))),
            "direction": float(np.mean(np.sign(test.predicted_return) == np.sign(test.actual_return))),
            "brier": float(np.mean((probabilities - actual_up) ** 2)),
            "coverage60": float(np.mean(np.abs(error) <= radius60)),
            "width60": 2 * radius60,
            "coverage80": float(np.mean(np.abs(error) <= radius80)),
            "width80": 2 * radius80,
            "no_change_mae": float(np.mean(np.abs(test.no_change_return - test.actual_return))),
            "unconditional_mae": float(np.mean(np.abs(test.unconditional_return - test.actual_return))),
            "momentum_mae": float(np.mean(np.abs(test.momentum_return - test.actual_return))),
            "mean_reversion_mae": float(np.mean(np.abs(test.mean_reversion_return - test.actual_return))),
        }
        reliability, reason = _reliability(test, metrics, config)
        scorecards.append([
            run_id, secid, horizon, variant, len(test), metrics["mae"], metrics["median_ae"],
            metrics["direction"], metrics["brier"], metrics["coverage60"], metrics["width60"],
            metrics["coverage80"], metrics["width80"], metrics["no_change_mae"],
            metrics["unconditional_mae"], metrics["momentum_mae"],
            metrics["mean_reversion_mae"], float(test.effective_sample_size.median()),
            selected, reliability, reason, True,
        ])
        if variant == "plus_weighting":
            for bucket, rows in test.groupby("evaluation_regime"):
                bucket_error = rows.predicted_return - rows.actual_return
                regime_rows.append([
                    run_id, secid, horizon, bucket, len(rows),
                    float(np.mean(np.abs(bucket_error))),
                    float(np.mean(np.abs(rows.no_change_return - rows.actual_return))),
                    float(np.mean(np.abs(bucket_error) <= radius60)),
                    "diagnostic_only" if len(rows) < 20 else "frozen_oos", True,
                ])
    return mappings, scorecards, regime_rows


def _current_rows(
    con: duckdb.DuckDBPyConnection, run_id: str, scorecards: list[list[Any]], mappings: list[list[Any]]
) -> list[list[Any]]:
    score_map = {(row[1], row[2], row[3]): row for row in scorecards}
    mapping_map = {(row[1], row[2], row[3]): row for row in mappings}
    path_run = con.execute(
        "SELECT run_id FROM conditional_path_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    rows = []
    for secid in SECIDS:
        for horizon in HORIZONS:
            curve = con.execute(
                """SELECT status,weighted_median_price,stress_low,stress_high,raw_n,
                effective_sample_size FROM conditional_path_curves
                WHERE run_id=? AND secid=? AND session=?""",
                [path_run, secid, horizon],
            ).fetchone()
            price = con.execute(
                """SELECT close FROM canonical_daily_prices WHERE canonical_secid=?
                ORDER BY trade_date DESC LIMIT 1""",
                [secid],
            ).fetchone()
            up_row = con.execute(
                """SELECT sum(CASE WHEN normalized_return>0 THEN weight ELSE 0 END),sum(weight)
                FROM conditional_analog_paths WHERE run_id=? AND secid=? AND session=?
                AND scenario_role='EXPECTED_CONDITIONAL' AND weight>0""",
                [path_run, secid, horizon],
            ).fetchone()
            if not curve or not price:
                continue
            current_price = float(price[0])
            raw_up = up_row[0] / up_row[1] if up_row and up_row[1] else None
            mapping = mapping_map.get((secid, horizon, "plus_weighting"))
            score = score_map.get((secid, horizon, "plus_weighting"))
            center = curve[1]
            if not mapping or center is None:
                rows.append([
                    run_id, secid, horizon, current_price, center, None, None, None, None,
                    curve[2], curve[3], None, False, curve[4], curve[5],
                    "INSUFFICIENT_HISTORY", "not_validated", True,
                ])
                continue
            radius60, radius80 = mapping[6], mapping[7]
            reliability = score[19] if score else "UNVALIDATED"
            selected_threshold = mapping[4]
            compatible_current_method = float(selected_threshold) == 45.0
            range_status = (
                "validated" if reliability == "VALIDATED" and compatible_current_method else "not_validated"
            )
            calibrated_probability = None
            if raw_up is not None:
                calibrated_probability = float(
                    _apply_probability(pd.Series([raw_up]), mapping[8], mapping[9], mapping[10])[0]
                )
            rows.append([
                run_id, secid, horizon, current_price, center,
                center - current_price * radius60 if radius60 is not None else None,
                center + current_price * radius60 if radius60 is not None else None,
                center - current_price * radius80 if radius80 is not None else None,
                center + current_price * radius80 if radius80 is not None else None,
                curve[2], curve[3], calibrated_probability, False, curve[4], curve[5],
                reliability, range_status, True,
            ])
    return rows


def build_conditional_validation(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    config, family_weights, signature = _config()
    cutoff = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    dependencies = con.execute(
        """SELECT
        (SELECT run_id FROM conditional_similarity_runs ORDER BY created_at DESC LIMIT 1),
        (SELECT run_id FROM conditional_regime_runs ORDER BY created_at DESC LIMIT 1),
        (SELECT run_id FROM conditional_path_runs ORDER BY created_at DESC LIMIT 1)"""
    ).fetchone()
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}|{dependencies}|{signature}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT replay_rows,scorecards,status FROM conditional_validation_runs WHERE run_id=?", [run_id]
    ).fetchone()
    if existing:
        return {
            "run_id": run_id, "replay_rows": existing[0], "scorecards": existing[1],
            "status": existing[2], "idempotent": True,
        }
    column_names = [
        name.strip()
        for name in """run_id,secid,horizon,evaluation_date,phase,variant,similarity_threshold,
        evaluation_regime,status,raw_n,effective_sample_size,predicted_return,actual_return,
        raw_low60,raw_high60,raw_low80,raw_high80,raw_up_frequency,no_change_return,
        unconditional_return,momentum_return,mean_reversion_return,history_end,immutable""".split(",")
    ]
    columns = ",".join(column_names)
    partial_count = con.execute(
        "SELECT count(*) FROM conditional_replay_forecasts WHERE run_id=?", [run_id]
    ).fetchone()[0]
    replay_rows: list[list[Any]] = (
        [
            list(row)
            for row in con.execute(
                f"SELECT {columns} FROM conditional_replay_forecasts WHERE run_id=?",
                [run_id],
            ).fetchall()
        ]
        if partial_count
        else []
    )
    for secid in (() if partial_count else SECIDS):
        frame, families = build_state_panel(con, secid, cutoff)
        if len(frame) < int(config["minimum_history"]) + 20:
            continue
        regimes = classify_regimes(frame, families["rates"])
        validation_start = max(
            int(config["minimum_history"]), int(len(frame) * float(config["train_fraction"]))
        )
        schedule = range(validation_start, len(frame) - 1, int(config["schedule_sessions"]))
        for position in schedule:
            current = frame.iloc[position]
            distances = family_distances(frame.iloc[:position], current, families)
            phase = _phase(
                position, len(frame), float(config["train_fraction"]),
                float(config["calibration_fraction"]),
            )
            evaluation_regime = _regime_bucket(regimes.iloc[position])
            score_cache = {}
            for variant, selected_families in VARIANTS.items():
                selected_weights = {name: family_weights[name] for name in selected_families}
                total = _total_distance(distances, selected_weights)
                score_cache[variant] = pd.Series(similarity_score(total), index=total.index)
            current_state = regimes.iloc[position].to_dict()
            compatibility_cache = pd.Series(
                [
                    regime_compatibility(current_state, regimes.loc[date].to_dict())
                    for date in distances.index
                ],
                index=distances.index,
            )
            for horizon in HORIZONS:
                if position + horizon >= len(frame):
                    continue
                actual = float(frame.close.iloc[position + horizon] / frame.close.iloc[position] - 1)
                for variant in VARIANTS:
                    thresholds = (
                        config["similarity_thresholds"]
                        if variant == "plus_weighting"
                        else [config["default_similarity_threshold"]]
                    )
                    for threshold in thresholds:
                        forecast = _forecast_one(
                            frame, distances, regimes, position, horizon, variant,
                            family_weights, float(threshold), score_cache[variant],
                            compatibility_cache,
                        )
                        if forecast["status"] != "ready":
                            continue
                        replay_rows.append([
                            run_id, secid, horizon, frame.index[position].date(), phase, variant,
                            float(threshold), evaluation_regime, forecast["status"],
                            forecast["raw_n"], forecast["ess"], forecast["prediction"], actual,
                            forecast["low60"], forecast["high60"], forecast["low80"],
                            forecast["high80"], forecast["up"], 0.0, forecast["unconditional"],
                            forecast["momentum"], forecast["mean_reversion"],
                            pd.Timestamp(forecast["history_end"]).date(), True,
                        ])
    if replay_rows and not partial_count:
        placeholders = ",".join("?" for _ in columns.split(","))
        con.executemany(
            f"INSERT INTO conditional_replay_forecasts ({columns}) VALUES ({placeholders})",
            replay_rows,
        )
    replay = pd.DataFrame(replay_rows, columns=column_names)
    mappings, scorecards, regime_rows = _metric_rows(replay, run_id, config) if len(replay) else ([], [], [])
    if mappings:
        con.executemany(
            """INSERT INTO conditional_calibration_mappings
            (run_id,secid,horizon,variant,selected_threshold,calibration_n,radius60,radius80,
            probability_method,probability_coef,probability_intercept,frozen_before_test,immutable)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _native_rows(mappings),
        )
    if scorecards:
        con.executemany(
            """INSERT INTO conditional_validation_scorecards
            (run_id,secid,horizon,variant,oos_n,mae,median_ae,directional_accuracy,brier,
            coverage60,median_width60,coverage80,median_width80,no_change_mae,
            unconditional_mae,momentum_mae,mean_reversion_mae,median_ess,selected_threshold,
            reliability,reliability_reason,immutable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _native_rows(scorecards),
        )
    if regime_rows:
        con.executemany(
            """INSERT INTO conditional_regime_validation
            (run_id,secid,horizon,regime_bucket,oos_n,mae,no_change_mae,coverage60,status,immutable)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            _native_rows(regime_rows),
        )
    current_rows = _current_rows(con, run_id, scorecards, mappings)
    if current_rows:
        con.executemany(
            """INSERT INTO conditional_calibrated_forecasts
            (run_id,secid,horizon,current_price,center_price,expected60_low,expected60_high,
            plausible80_low,plausible80_high,stress_low,stress_high,up_probability,
            probability_published,raw_n,effective_sample_size,reliability,range_status,immutable)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _native_rows(current_rows),
        )
    details = {
        "split": [config["train_fraction"], config["calibration_fraction"], config["frozen_test_fraction"]],
        "candidate_dates_strictly_before_evaluation": True,
        "calibration_frozen_before_test": True,
        "production_changes": 0,
    }
    con.execute(
        """INSERT INTO conditional_validation_runs
        (run_id,created_at,cutoff,validation_version,config_signature,feature_version,
        regime_version,similarity_version,weighting_version,calibration_version,random_seed,
        replay_rows,scorecards,immutable,production_unchanged,probability_gate_unchanged,
        status,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,TRUE,TRUE,TRUE,'completed',?)""",
        [run_id, datetime.now(UTC), cutoff, VERSION, signature, "pit-state-families-v1",
         "multidimensional-regime-v1.1", "conditional-similarity-v2",
         "conditional-weighting-v1.1", "split-conformal-v1", int(config["random_seed"]),
         len(replay_rows), len(scorecards), json.dumps(details, sort_keys=True)],
    )
    return {
        "run_id": run_id, "replay_rows": len(replay_rows), "scorecards": len(scorecards),
        "status": "completed", "idempotent": False,
    }
