"""Feature memory with long-run shrinkage and no automatic production chasing."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd

from moex_analytics.adaptive_learning.core import (
    FAMILIES,
    HORIZONS,
    INSTRUMENTS,
    SECTOR,
    _add_targets,
    _build_frame,
    _macro,
)

from .schema import DDL

VERSION = "dynamic-feature-learning-v1"
MIN_SAMPLE = 80


def ensure_schema(con) -> None:
    con.execute(DDL)


def _correlations(frame: pd.DataFrame, feature: str) -> tuple[float, float, int]:
    data = frame[[feature, "forward_return"]].dropna()
    if len(data) < MIN_SAMPLE or data[feature].nunique() < 3:
        return math.nan, math.nan, len(data)
    return (
        float(data[feature].corr(data.forward_return)),
        float(data[feature].corr(data.forward_return, method="spearman")),
        len(data),
    )


def _fold_values(frame: pd.DataFrame, feature: str, folds: int = 5) -> list[float]:
    data = frame[[feature, "forward_return"]].dropna()
    if len(data) < MIN_SAMPLE * 2:
        return []
    values = []
    for block in np.array_split(data, folds):
        if len(block) >= 30 and block[feature].nunique() >= 3:
            values.append(float(block[feature].corr(block.forward_return, method="spearman")))
    return values


def _classify(long_ic: float, recent_ic: float, folds: list[float], sample: int) -> tuple[str, str]:
    if sample < MIN_SAMPLE or not np.isfinite(long_ic):
        return "insufficient_sample", "fewer than minimum point-in-time observations"
    signs = {int(np.sign(value)) for value in folds if abs(value) >= 0.01}
    stability = float(np.mean(np.sign(folds) == np.sign(long_ic))) if folds else 0.0
    if len(signs) > 1 and stability < 0.65:
        return "sign_flip", "effect sign changes across chronological folds"
    if np.isfinite(recent_ic) and abs(recent_ic) < abs(long_ic) * 0.4 and abs(long_ic) >= 0.03:
        return "decaying", "recent absolute IC is less than 40% of long-run IC"
    if abs(long_ic) < 0.015:
        return "noise", "long-run absolute IC is economically negligible"
    if stability >= 0.7:
        return ("stable_positive" if long_ic > 0 else "stable_negative"), "stable fold sign"
    return "regime_dependent", "effect exists but is not stable across all folds"


def _periods(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    end = frame.index.max()
    return {
        "expanding": frame,
        "5y": frame.loc[frame.index >= end - pd.DateOffset(years=5)],
        "3y": frame.loc[frame.index >= end - pd.DateOffset(years=3)],
    }


def run_feature_learning(con) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    state = con.execute("SELECT count(*),max(trade_date) FROM canonical_daily_prices").fetchone()
    dataset = hashlib.sha256(repr((state, VERSION)).encode()).hexdigest()[:20]
    run_id = hashlib.sha256(f"{dataset}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO feature_learning_runs VALUES (?,?,current_timestamp,'running',?,?,NULL,0,?)",
        [run_id, dataset, json.dumps(INSTRUMENTS), json.dumps(HORIZONS), "research only"],
    )
    macro = _macro(con)
    records = 0
    for instrument in INSTRUMENTS:
        raw = _build_frame(con, instrument, macro)
        for horizon in HORIZONS:
            target = _add_targets(raw, horizon, sector_col=SECTOR.get(instrument))
            features = [name for name in FAMILIES if name in target]
            for feature in features:
                fold_values = _fold_values(target, feature)
                stability = (
                    float(np.mean(np.sign(fold_values) == np.sign(np.nanmean(fold_values))))
                    if fold_values
                    else 0.0
                )
                period_values = {}
                for period, subset in _periods(target).items():
                    ic, rank_ic, sample = _correlations(subset, feature)
                    period_values[period] = (ic, rank_ic, sample)
                    con.execute(
                        """INSERT INTO feature_performance_history VALUES
                        (?,?,?,?,?,'all',?,?,?,?,?,?,?,?,?,?,?,TRUE,current_timestamp)""",
                        [
                            run_id,
                            feature,
                            FAMILIES[feature],
                            instrument,
                            horizon,
                            period,
                            subset.index.min(),
                            subset.index.max(),
                            ic,
                            rank_ic,
                            int(np.sign(rank_ic)) if np.isfinite(rank_ic) else 0,
                            stability,
                            sample,
                            abs(rank_ic) if np.isfinite(rank_ic) else None,
                            rank_ic if np.isfinite(rank_ic) else None,
                            "measured" if sample >= MIN_SAMPLE else "insufficient_sample",
                        ],
                    )
                    records += 1
                long_ic, _, long_n = period_values["expanding"]
                recent_ic, _, recent_n = period_values["3y"]
                status, reason = _classify(long_ic, recent_ic, fold_values, long_n)
                shrunk = (
                    0.75 * long_ic + 0.25 * recent_ic
                    if np.isfinite(long_ic) and np.isfinite(recent_ic) and recent_n >= MIN_SAMPLE
                    else long_ic
                )
                regimes_worked = 0
                for regime, subset in target.groupby("regime"):
                    ic, rank_ic, sample = _correlations(subset, feature)
                    if sample >= MIN_SAMPLE and np.isfinite(rank_ic) and abs(rank_ic) >= 0.02:
                        regimes_worked += 1
                    con.execute(
                        """INSERT INTO feature_performance_history VALUES
                        (?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?,?,?,TRUE,current_timestamp)""",
                        [
                            run_id,
                            feature,
                            FAMILIES[feature],
                            instrument,
                            horizon,
                            str(regime),
                            "expanding",
                            subset.index.min(),
                            subset.index.max(),
                            ic,
                            rank_ic,
                            int(np.sign(rank_ic)) if np.isfinite(rank_ic) else 0,
                            stability,
                            sample,
                            abs(rank_ic) if np.isfinite(rank_ic) else None,
                            rank_ic if np.isfinite(rank_ic) else None,
                            "measured" if sample >= MIN_SAMPLE else "insufficient_sample",
                        ],
                    )
                    records += 1
                sign_changes = int(sum(np.sign(a) != np.sign(b) for a, b in itertools.pairwise(fold_values)))
                years = (target.index.max() - target.index.min()).days / 365.25
                con.execute(
                    "INSERT INTO feature_dynamic_scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        run_id,
                        feature,
                        FAMILIES[feature],
                        instrument,
                        horizon,
                        long_ic,
                        recent_ic,
                        shrunk,
                        stability,
                        regimes_worked,
                        years,
                        sign_changes,
                        long_n,
                        status,
                        reason,
                    ],
                )
            families = set(FAMILIES[feature] for feature in features)
            for family in families:
                rows = con.execute(
                    """SELECT shrunk_ic,status FROM feature_dynamic_scorecards
                    WHERE run_id=? AND instrument=? AND horizon=? AND family=?""",
                    [run_id, instrument, horizon, family],
                ).fetchall()
                finite = [abs(row[0]) for row in rows if row[0] is not None and np.isfinite(row[0])]
                contribution = float(np.mean(finite)) if finite else None
                stable = sum(row[1] in {"stable_positive", "stable_negative"} for row in rows)
                decaying = sum(row[1] == "decaying" for row in rows)
                status = "useful" if stable else "insufficient_or_unstable"
                con.execute(
                    "INSERT INTO feature_family_contribution VALUES (?,?,?,?,?,?,?,?,?)",
                    [run_id, instrument, horizon, family, len(rows), contribution, stable, decaying, status],
                )
    runtime = time.perf_counter() - started
    con.execute(
        """UPDATE feature_learning_runs SET status='completed',runtime_seconds=?,records=?
        WHERE run_id=?""",
        [runtime, records, run_id],
    )
    return {
        "run_id": run_id,
        "records": records,
        "runtime_seconds": runtime,
        "automatic_production_change": False,
    }


def feature_learning_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,runtime_seconds,records FROM feature_learning_runs
        ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not latest:
        return {"latest": None, "statuses": []}
    statuses = con.execute(
        """SELECT status,count(*) FROM feature_dynamic_scorecards
        WHERE run_id=? GROUP BY 1 ORDER BY 1""",
        [latest[0]],
    ).fetchall()
    return {"latest": latest, "statuses": statuses}
