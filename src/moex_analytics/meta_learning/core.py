"""Temporal meta-learning that learns when a primary OOS forecast is unreliable."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .schema import DDL

MIN_TRAIN = 120
COVERAGES = (1.0, 0.7, 0.5, 0.3)
META_FEATURES = ("probability_margin", "regime_novelty", "model_disagreement", "return_scale")


def ensure_schema(con) -> None:
    con.execute(DDL)


def _meta_frame(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = predictions.copy().sort_values("trade_date")
    frame["primary_correct"] = frame.actual_direction == frame.predicted_direction
    frame["large_error"] = (
        frame.actual_return - frame.predicted_return
    ).abs() > frame.actual_return.abs().median()
    frame["interval_failure"] = frame.large_error
    frame["probability_margin"] = (frame.probability - 0.5).abs()
    regime_count = frame.regime.value_counts()
    frame["regime_novelty"] = frame.regime.map(lambda value: 1 / max(regime_count.get(value, 1), 1))
    if "model_disagreement" not in frame:
        frame["model_disagreement"] = 0.0
    frame["return_scale"] = frame.actual_return.expanding().std().shift(1)
    return frame


def _fit_meta(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(class_weight="balanced")),
        ]
    )
    model.fit(train[list(META_FEATURES)], train.primary_correct.astype(int))
    return model.predict_proba(test[list(META_FEATURES)])[:, 1]


def _selective_curve(
    train_confidence: np.ndarray, test_confidence: np.ndarray, correct: np.ndarray
) -> list[tuple]:
    rows = []
    for coverage in COVERAGES:
        threshold = float(np.quantile(train_confidence, 1 - coverage)) if coverage < 1 else 0.0
        selected = test_confidence >= threshold
        rows.append(
            (
                coverage,
                threshold,
                float(selected.mean()),
                float(correct[selected].mean()) if selected.any() else np.nan,
                int(selected.sum()),
            )
        )
    return rows


def _policy(confidence: float, unknown: bool) -> str:
    if unknown or confidence < 0.45:
        return "abstain"
    if confidence < 0.6:
        return "publish_with_caution"
    return "publish_signal"


def run_meta_learning(con) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id FROM tournament_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise RuntimeError("completed model tournament is required")
    source_run = source[0]
    run_id = hashlib.sha256(f"{source_run}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute("UPDATE meta_learning_runs SET status='interrupted' WHERE status='running'")
    con.execute(
        "INSERT INTO meta_learning_runs VALUES (?,?,current_timestamp,'running',NULL,0,?)",
        [run_id, source_run, "research only; OOS primary predictions only"],
    )
    leaders = con.execute(
        "SELECT secid,horizon,winner FROM tournament_leaderboard WHERE run_id=?",
        [source_run],
    ).fetchall()
    models = 0
    for secid, horizon, model in leaders:
        predictions = con.execute(
            """SELECT trade_date,actual_direction,predicted_direction,probability,
            actual_return,predicted_return,regime FROM tournament_predictions
            WHERE run_id=? AND secid=? AND horizon=? AND model=? AND split='pseudo_oos'
            ORDER BY trade_date""",
            [source_run, secid, horizon, model],
        ).df()
        if len(predictions) < MIN_TRAIN * 2:
            continue
        all_models = con.execute(
            """SELECT trade_date,stddev_pop(probability) disagreement
            FROM tournament_predictions WHERE run_id=? AND secid=? AND horizon=?
            AND split='pseudo_oos' GROUP BY trade_date""",
            [source_run, secid, horizon],
        ).df()
        predictions = predictions.merge(all_models, on="trade_date", how="left").rename(
            columns={"disagreement": "model_disagreement"}
        )
        frame = _meta_frame(predictions)
        split = int(len(frame) * 0.7)
        train, test = frame.iloc[:split].copy(), frame.iloc[split:].copy()
        if train.primary_correct.nunique() < 2 or test.empty:
            continue
        train_confidence = _fit_meta(train, train)
        test_confidence = _fit_meta(train, test)
        curve = _selective_curve(train_confidence, test_confidence, test.primary_correct.to_numpy(bool))
        selected_row = next(row for row in curve if row[0] == 0.5)
        base_accuracy = float(test.primary_correct.mean())
        selected_accuracy = selected_row[3]
        benefit = selected_accuracy - base_accuracy if np.isfinite(selected_accuracy) else None
        novelty_cutoff = float(train.regime_novelty.quantile(0.95))
        unknown = test.regime_novelty > novelty_cutoff
        policies = [
            _policy(confidence, novel) for confidence, novel in zip(test_confidence, unknown, strict=True)
        ]
        for row, confidence, novel, policy in zip(
            test.itertuples(), test_confidence, unknown, policies, strict=True
        ):
            con.execute(
                "INSERT INTO meta_oos_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,TRUE)",
                [
                    run_id,
                    secid,
                    horizon,
                    model,
                    row.trade_date,
                    row.primary_correct,
                    row.large_error,
                    row.interval_failure,
                    confidence,
                    row.regime_novelty,
                    float(novel),
                    row.model_disagreement,
                    policy,
                ],
            )
        for coverage, threshold, actual_coverage, accuracy, sample in curve:
            con.execute(
                "INSERT INTO selective_accuracy_curves VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    secid,
                    horizon,
                    model,
                    coverage,
                    threshold,
                    actual_coverage,
                    accuracy,
                    sample,
                    "train_only_quantile",
                ],
            )
        status = "beneficial" if benefit is not None and benefit > 0.02 else "no_stable_benefit"
        con.execute(
            """INSERT INTO meta_confidence_scorecards VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,TRUE,current_timestamp)""",
            [
                run_id,
                secid,
                horizon,
                model,
                train.trade_date.max(),
                test.trade_date.min(),
                len(train),
                len(test),
                base_accuracy,
                selected_accuracy,
                selected_row[2],
                benefit,
                float(unknown.mean()),
                "fixed_train_thresholds",
                status,
                "holdout not used for threshold selection",
            ],
        )
        models += 1
    runtime = time.perf_counter() - started
    con.execute(
        "UPDATE meta_learning_runs SET status='completed',runtime_seconds=?,models=? WHERE run_id=?",
        [runtime, models, run_id],
    )
    return {"run_id": run_id, "models": models, "runtime_seconds": runtime, "production_change": False}


def meta_learning_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        "SELECT run_id,status,runtime_seconds,models FROM meta_learning_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": latest}
