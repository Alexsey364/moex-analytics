"""Calibration gates fitted chronologically and isolated from untouched holdout."""

from __future__ import annotations

import hashlib
import itertools
import math
import time
from datetime import datetime

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from .schema import DDL

MIN_CALIBRATION = 120
METHODS = ("raw", "platt", "isotonic")


def ensure_schema(con) -> None:
    con.execute(DDL)


def _ece(y: np.ndarray, probability: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    result = 0.0
    for lower, upper in itertools.pairwise(edges):
        mask = (probability >= lower) & (probability < upper if upper < 1 else probability <= upper)
        if mask.any():
            result += float(mask.mean() * abs(y[mask].mean() - probability[mask].mean()))
    return result


def _calibration_line(y: np.ndarray, probability: np.ndarray) -> tuple[float, float]:
    clipped = np.clip(probability, 1e-5, 1 - 1e-5)
    logits = np.log(clipped / (1 - clipped))
    if len(np.unique(y)) < 2 or np.std(logits) == 0:
        return math.nan, math.nan
    model = LogisticRegression(C=1e6).fit(logits.reshape(-1, 1), y)
    return float(model.coef_[0, 0]), float(model.intercept_[0])


def _temporal_calibrate(
    train_y: np.ndarray, train_p: np.ndarray, test_p: np.ndarray, method: str
) -> np.ndarray:
    if method == "raw":
        return np.clip(test_p, 1e-5, 1 - 1e-5)
    if method == "platt":
        logits = np.log(np.clip(train_p, 1e-5, 1 - 1e-5) / np.clip(1 - train_p, 1e-5, 1))
        test_logits = np.log(np.clip(test_p, 1e-5, 1 - 1e-5) / np.clip(1 - test_p, 1e-5, 1))
        fitted = LogisticRegression().fit(logits.reshape(-1, 1), train_y)
        return fitted.predict_proba(test_logits.reshape(-1, 1))[:, 1]
    fitted = IsotonicRegression(out_of_bounds="clip").fit(train_p, train_y)
    return np.clip(fitted.predict(test_p), 1e-5, 1 - 1e-5)


def _intervals(
    train_actual: np.ndarray, train_predicted: np.ndarray, test_actual: np.ndarray, test_predicted: np.ndarray
) -> dict:
    residual = train_actual - train_predicted
    result = {}
    for level in (0.5, 0.8, 0.9):
        alpha = (1 - level) / 2
        lower, upper = np.quantile(residual, [alpha, 1 - alpha])
        covered = (test_actual >= test_predicted + lower) & (test_actual <= test_predicted + upper)
        result[level] = (float(covered.mean()), float(upper - lower))
    return result


def _gate(
    n: int, auc: float, brier: float, baseline: float, ece: float, slope: float, holdout: bool, stable: bool
) -> tuple[bool, str]:
    checks = {
        "effective sample": n >= MIN_CALIBRATION,
        "AUC": np.isfinite(auc) and auc > 0.52,
        "Brier vs baseline": brier < baseline,
        "ECE": ece <= 0.08,
        "slope": np.isfinite(slope) and 0.7 <= slope <= 1.3,
        "untouched holdout": holdout,
        "fold/regime stability": stable,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return not failed, "all gates passed" if not failed else "failed: " + ", ".join(failed)


def run_calibration_audit(con) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    con.execute("UPDATE calibration_runs SET status='interrupted' WHERE status='running'")
    source = con.execute(
        "SELECT run_id FROM tournament_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise RuntimeError("completed model tournament is required")
    source_run = source[0]
    run_id = hashlib.sha256(f"{source_run}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO calibration_runs VALUES (?,?,current_timestamp,'running',NULL,0,0,?)",
        [run_id, source_run, "research only; no probability publication"],
    )
    combinations = con.execute(
        """SELECT DISTINCT secid,horizon,model FROM tournament_predictions
        WHERE run_id=? AND split='pseudo_oos' AND probability IS NOT NULL""",
        [source_run],
    ).fetchall()
    approved = 0
    audited = 0
    for secid, horizon, model in combinations:
        frame = con.execute(
            """SELECT trade_date,actual_direction,probability,actual_return,predicted_return,regime
            FROM tournament_predictions WHERE run_id=? AND secid=? AND horizon=? AND model=?
            AND split='pseudo_oos' ORDER BY trade_date""",
            [source_run, secid, horizon, model],
        ).df()
        if len(frame) < 60 or frame.actual_direction.nunique() < 2:
            continue
        split = max(30, int(len(frame) * 0.7))
        train, test = frame.iloc[:split], frame.iloc[split:]
        if test.empty or train.actual_direction.nunique() < 2:
            continue
        holdout_row = con.execute(
            """SELECT advantage,ci_low,regime_stability,status FROM tournament_results
            WHERE run_id=? AND secid=? AND horizon=? AND model=? AND split='untouched_holdout'""",
            [source_run, secid, horizon, model],
        ).fetchone()
        holdout_ok = bool(holdout_row and holdout_row[0] > 0 and holdout_row[1] >= 0)
        stable = bool(holdout_row and holdout_row[2] is not None and holdout_row[2] >= 0.6)
        for method in METHODS:
            calibrated = _temporal_calibrate(
                train.actual_direction.to_numpy(int),
                train.probability.to_numpy(float),
                test.probability.to_numpy(float),
                method,
            )
            y = test.actual_direction.to_numpy(int)
            auc = float(roc_auc_score(y, calibrated)) if len(np.unique(y)) == 2 else math.nan
            brier = float(brier_score_loss(y, calibrated))
            baseline = float(brier_score_loss(y, np.full(len(y), train.actual_direction.mean())))
            ece = _ece(y, calibrated)
            slope, intercept = _calibration_line(y, calibrated)
            allowed, reason = _gate(len(test), auc, brier, baseline, ece, slope, holdout_ok, stable)
            approved += int(allowed)
            con.execute(
                """INSERT INTO probability_calibration_audit VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,TRUE,current_timestamp)""",
                [
                    run_id,
                    secid,
                    horizon,
                    model,
                    method,
                    train.trade_date.max(),
                    test.trade_date.min(),
                    len(train),
                    len(test),
                    auc,
                    brier,
                    baseline,
                    float(log_loss(y, calibrated, labels=[0, 1])),
                    ece,
                    slope,
                    intercept,
                    stable,
                    holdout_ok,
                    stable,
                    False,
                    allowed,
                    "approved" if allowed else "gated",
                    reason,
                ],
            )
            intervals = _intervals(
                train.actual_return.to_numpy(float),
                train.predicted_return.to_numpy(float),
                test.actual_return.to_numpy(float),
                test.predicted_return.to_numpy(float),
            )
            interval_status = (
                "acceptable"
                if all(abs(intervals[level][0] - level) <= 0.1 for level in intervals)
                else "miscalibrated"
            )
            con.execute(
                """INSERT INTO prediction_interval_audit VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,TRUE,current_timestamp)""",
                [
                    run_id,
                    secid,
                    horizon,
                    model,
                    f"temporal_residual_{method}",
                    len(train),
                    len(test),
                    intervals[0.5][0],
                    intervals[0.8][0],
                    intervals[0.9][0],
                    intervals[0.5][1],
                    intervals[0.8][1],
                    intervals[0.9][1],
                    interval_status,
                ],
            )
            audited += 1
        disagreement = float(frame.groupby("trade_date").probability.std().fillna(0).mean())
        con.execute(
            "INSERT INTO uncertainty_decomposition VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                secid,
                horizon,
                model,
                float(frame.probability.std()),
                disagreement,
                float(frame.groupby("regime").probability.mean().std()),
                None,
                1.0,
                "live_insufficient",
            ],
        )
    runtime = time.perf_counter() - started
    con.execute(
        """UPDATE calibration_runs SET status='completed',runtime_seconds=?,
        models_audited=?,probability_approved=? WHERE run_id=?""",
        [runtime, audited, approved, run_id],
    )
    return {
        "run_id": run_id,
        "audits": audited,
        "probability_approved": approved,
        "runtime_seconds": runtime,
        "production_change": False,
    }


def calibration_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,runtime_seconds,models_audited,probability_approved
        FROM calibration_runs ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    return {"latest": latest}
