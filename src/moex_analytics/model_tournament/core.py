"""Leakage-controlled tournament; never changes production models or policy."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from moex_analytics.adaptive_learning.core import FAMILIES, SECTOR, _add_targets, _build_frame, _macro

from .schema import DDL

VERSION = "walk-forward-tournament-v1"
INSTRUMENTS = ("SBERP", "LKOH", "MTSS", "TRNFP", "MOEX")
HORIZONS = (5, 20, 60, 120)
HOLDOUT_FRACTION = 0.15
NEUTRAL_POLICY = "volatility band frozen before holdout; neutral rows excluded from binary duel"


def ensure_schema(con) -> None:
    con.execute(DDL)


def development_holdout(
    n: int, horizon: int, fraction: float = HOLDOUT_FRACTION
) -> tuple[np.ndarray, np.ndarray]:
    """Chronological untouched holdout with a horizon-sized embargo."""
    holdout_n = max(60, int(n * fraction))
    holdout_start = n - holdout_n
    development_end = holdout_start - horizon
    if development_end < max(300, horizon * 4):
        return np.array([], dtype=int), np.array([], dtype=int)
    return np.arange(development_end), np.arange(holdout_start, n)


def walk_forward_folds(n: int, horizon: int, count: int = 3) -> list[dict[str, np.ndarray | int]]:
    """Expanding folds with validation and test blocks separated by embargo."""
    minimum = max(240, horizon * 4)
    available = n - minimum - 2 * horizon
    block = available // (count * 2) if available > 0 else 0
    if block < 20:
        return []
    folds = []
    for fold in range(count):
        train_end = minimum + fold * 2 * block
        val_start = train_end + horizon
        val_end = min(val_start + block, n)
        test_start = val_end + horizon
        test_end = min(test_start + block, n)
        if test_end - test_start < 15:
            break
        folds.append(
            {
                "fold": fold + 1,
                "train": np.arange(train_end),
                "validation": np.arange(val_start, val_end),
                "test": np.arange(test_start, test_end),
                "embargo": horizon,
            }
        )
    return folds


def _models(seed: int = 23) -> dict[str, tuple[str, object]]:
    def linear(ratio: float):
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            StandardScaler(),
            LogisticRegression(
                l1_ratio=ratio,
                C=0.2,
                solver="saga" if ratio > 0 else "lbfgs",
                max_iter=1000,
                random_state=seed,
            ),
        )

    return {
        "logistic": ("linear", linear(0)),
        "lasso_logistic": ("linear", linear(1)),
        "elastic_net": ("linear", linear(0.25)),
        "random_forest": (
            "tree",
            make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                RandomForestClassifier(
                    n_estimators=50, max_depth=5, min_samples_leaf=20, n_jobs=1, random_state=seed
                ),
            ),
        ),
        "extra_trees": (
            "tree",
            make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                ExtraTreesClassifier(
                    n_estimators=50, max_depth=6, min_samples_leaf=15, n_jobs=1, random_state=seed
                ),
            ),
        ),
        "hist_gradient": (
            "tree",
            make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                HistGradientBoostingClassifier(
                    max_iter=60,
                    max_leaf_nodes=15,
                    min_samples_leaf=25,
                    l2_regularization=2,
                    random_state=seed,
                ),
            ),
        ),
        "knn_diagnostic": (
            "diagnostic",
            make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                StandardScaler(),
                KNeighborsClassifier(35, weights="distance"),
            ),
        ),
    }


def _effective_n(values: np.ndarray) -> float:
    if len(values) < 3:
        return float(len(values))
    rho = np.corrcoef(values[:-1], values[1:])[0, 1]
    rho = float(np.nan_to_num(rho))
    return float(len(values) * (1 - rho) / max(1e-6, 1 + rho))


def _ece(y: np.ndarray, p: np.ndarray) -> float:
    total = 0.0
    for low, high in zip(np.linspace(0, 0.9, 10), np.linspace(0.1, 1, 10), strict=True):
        mask = (p >= low) & (p <= high if high == 1 else p < high)
        if mask.any():
            total += float(mask.mean() * abs(y[mask].mean() - p[mask].mean()))
    return total


def _metrics(y: np.ndarray, p: np.ndarray, actual: np.ndarray, predicted: np.ndarray) -> dict:
    probability = np.clip(p, 1e-6, 1 - 1e-6)
    auc = float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else math.nan
    errors = actual - predicted
    return {
        "balanced_accuracy": float(balanced_accuracy_score(y, probability >= 0.5)),
        "roc_auc": auc,
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "rank_ic": float(pd.Series(actual).corr(pd.Series(predicted), method="spearman")),
        "ece": _ece(y, probability),
    }


def _baseline(
    train_y: np.ndarray, train_r: np.ndarray, test_x: pd.DataFrame, kind: str
) -> tuple[np.ndarray, np.ndarray]:
    frequency = float(np.mean(train_y))
    if kind == "unconditional":
        return np.full(len(test_x), frequency), np.full(len(test_x), float(np.mean(train_r)))
    signal = test_x["ret_20"].fillna(0).to_numpy()
    if kind == "mean_reversion":
        signal = -signal
    probability = np.where(signal >= 0, max(0.5, frequency), min(0.5, frequency))
    return probability.astype(float), signal.astype(float)


def _baseline_oos(sample: pd.DataFrame, horizon: int, kind: str, split: str = "test") -> OOS:
    development, _ = development_holdout(len(sample), horizon)
    output = OOS([], [], [], [], [], [], [])
    for fold in walk_forward_folds(len(development), horizon):
        train, test = fold["train"], fold[split]
        train_y = sample.direction.iloc[train].to_numpy(int)
        train_r = sample.forward_return.iloc[train].to_numpy(float)
        if kind == "historical_conditional":
            probabilities = []
            predictions = []
            for regime in sample.regime.iloc[test]:
                mask = sample.regime.iloc[train].to_numpy() == regime
                eligible = mask if mask.sum() >= 30 else np.ones(len(train), dtype=bool)
                probabilities.append(float(np.mean(train_y[eligible])))
                predictions.append(float(np.median(train_r[eligible])))
            probability = np.asarray(probabilities)
            predicted = np.asarray(predictions)
        else:
            probability, predicted = _baseline(train_y, train_r, sample.iloc[test], kind)
        output.dates.extend(sample.index[test].tolist())
        output.y.extend(sample.direction.iloc[test].astype(int).tolist())
        output.p.extend(probability.tolist())
        output.actual.extend(sample.forward_return.iloc[test].astype(float).tolist())
        output.predicted.extend(predicted.tolist())
        output.regimes.extend(sample.regime.iloc[test].astype(str).tolist())
        output.folds.extend([int(fold["fold"])] * len(test))
    return output


def _baseline_holdout(sample: pd.DataFrame, horizon: int, kind: str) -> OOS:
    development, holdout = development_holdout(len(sample), horizon)
    train_y = sample.direction.iloc[development].to_numpy(int)
    train_r = sample.forward_return.iloc[development].to_numpy(float)
    if kind == "historical_conditional":
        probabilities = []
        predictions = []
        for regime in sample.regime.iloc[holdout]:
            mask = sample.regime.iloc[development].to_numpy() == regime
            eligible = mask if mask.sum() >= 30 else np.ones(len(development), dtype=bool)
            probabilities.append(float(np.mean(train_y[eligible])))
            predictions.append(float(np.median(train_r[eligible])))
        probability = np.asarray(probabilities)
        predicted = np.asarray(predictions)
    else:
        probability, predicted = _baseline(train_y, train_r, sample.iloc[holdout], kind)
    return OOS(
        sample.index[holdout].tolist(),
        sample.direction.iloc[holdout].astype(int).tolist(),
        probability.tolist(),
        sample.forward_return.iloc[holdout].astype(float).tolist(),
        predicted.tolist(),
        sample.regime.iloc[holdout].astype(str).tolist(),
        [0] * len(holdout),
    )


@dataclass
class OOS:
    dates: list
    y: list
    p: list
    actual: list
    predicted: list
    regimes: list
    folds: list


def _bootstrap_advantage(
    y: np.ndarray, p: np.ndarray, bp: np.ndarray, seed: int = 23
) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    point = balanced_accuracy_score(y, p >= 0.5) - balanced_accuracy_score(y, bp >= 0.5)
    values = []
    for _ in range(500):
        idx = rng.integers(0, len(y), len(y))
        if len(np.unique(y[idx])) < 2:
            continue
        values.append(
            balanced_accuracy_score(y[idx], p[idx] >= 0.5) - balanced_accuracy_score(y[idx], bp[idx] >= 0.5)
        )
    return float(point), float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def _sanity_checks(y: np.ndarray, p: np.ndarray, baseline_p: np.ndarray) -> tuple[float, bool, bool]:
    """Prediction-label permutation and deterministic random-noise diagnostics."""
    rng = np.random.default_rng(23)
    observed = balanced_accuracy_score(y, p >= 0.5) - balanced_accuracy_score(y, baseline_p >= 0.5)
    permuted = []
    for _ in range(500):
        shuffled = rng.permutation(y)
        if len(np.unique(shuffled)) == 2:
            permuted.append(
                balanced_accuracy_score(shuffled, p >= 0.5)
                - balanced_accuracy_score(shuffled, baseline_p >= 0.5)
            )
    p_value = float((1 + np.sum(np.asarray(permuted) >= observed)) / (len(permuted) + 1))
    noise_p = rng.random(len(y))
    noise_advantage = balanced_accuracy_score(y, noise_p >= 0.5) - balanced_accuracy_score(
        y, baseline_p >= 0.5
    )
    return p_value, p_value <= 0.05, observed > noise_advantage


def _bh_qvalues(pvalues: list[float]) -> list[float]:
    order = np.argsort(pvalues)
    result = np.ones(len(pvalues))
    running = 1.0
    for rank_index in range(len(order) - 1, -1, -1):
        idx = order[rank_index]
        rank = rank_index + 1
        running = min(running, pvalues[idx] * len(pvalues) / rank)
        result[idx] = running
    return result.tolist()


def _apply_final_gates(con, run_id: str) -> None:
    """Apply multiple-testing and untouched-holdout gates without selecting on holdout."""
    con.execute(
        """UPDATE tournament_results SET status='unstable'
        WHERE run_id=? AND split='pseudo_oos' AND status='shadow_candidate' AND fdr_q>0.10""",
        [run_id],
    )
    con.execute(
        """UPDATE tournament_leaderboard SET winner='unconditional',status='no_reliable_winner',
        reason='final FDR or untouched-holdout gate failed; no production promotion'
        WHERE run_id=? AND winner<>'unconditional' AND (
          EXISTS(SELECT 1 FROM tournament_results r
            WHERE r.run_id=tournament_leaderboard.run_id
              AND r.secid=tournament_leaderboard.secid
              AND r.horizon=tournament_leaderboard.horizon
              AND r.model=tournament_leaderboard.winner
              AND r.split='pseudo_oos' AND (r.fdr_q>0.10 OR r.status<>'shadow_candidate'))
          OR NOT EXISTS(SELECT 1 FROM tournament_results h
            WHERE h.run_id=tournament_leaderboard.run_id
              AND h.secid=tournament_leaderboard.secid
              AND h.horizon=tournament_leaderboard.horizon
              AND h.model=tournament_leaderboard.winner
              AND h.split='untouched_holdout' AND h.advantage>0 AND h.ci_low>=0
              AND h.permutation_pass AND h.noise_pass)
        )""",
        [run_id],
    )


def _fit_oos(sample: pd.DataFrame, features: list[str], horizon: int, model, split: str = "test") -> OOS:
    development, _ = development_holdout(len(sample), horizon)
    folds = walk_forward_folds(len(development), horizon)
    output = OOS([], [], [], [], [], [], [])
    for fold in folds:
        train, test = fold["train"], fold[split]
        y_train = sample.direction.iloc[train].to_numpy(int)
        fitted = clone(model).fit(sample[features].iloc[train], y_train)
        probability = fitted.predict_proba(sample[features].iloc[test])[:, 1]
        train_returns = sample.forward_return.iloc[train].to_numpy(float)
        predicted = np.where(
            probability >= 0.5, np.median(train_returns[y_train == 1]), np.median(train_returns[y_train == 0])
        )
        output.dates.extend(sample.index[test].tolist())
        output.y.extend(sample.direction.iloc[test].astype(int).tolist())
        output.p.extend(probability.tolist())
        output.actual.extend(sample.forward_return.iloc[test].astype(float).tolist())
        output.predicted.extend(predicted.tolist())
        output.regimes.extend(sample.regime.iloc[test].astype(str).tolist())
        output.folds.extend([int(fold["fold"])] * len(test))
    return output


def _fit_regime_oos(
    sample: pd.DataFrame, features: list[str], horizon: int, model, split: str = "test"
) -> OOS:
    development, _ = development_holdout(len(sample), horizon)
    output = OOS([], [], [], [], [], [], [])
    for fold in walk_forward_folds(len(development), horizon):
        train, test = fold["train"], fold[split]
        train_y = sample.direction.iloc[train].to_numpy(int)
        global_model = clone(model).fit(sample[features].iloc[train], train_y)
        probability = np.empty(len(test))
        for regime in sample.regime.iloc[test].unique():
            train_mask = sample.regime.iloc[train].to_numpy() == regime
            test_mask = sample.regime.iloc[test].to_numpy() == regime
            eligible = train[train_mask]
            fitted = global_model
            if len(eligible) >= 80 and len(np.unique(sample.direction.iloc[eligible])) == 2:
                fitted = clone(model).fit(
                    sample[features].iloc[eligible], sample.direction.iloc[eligible].to_numpy(int)
                )
            probability[test_mask] = fitted.predict_proba(sample[features].iloc[test[test_mask]])[:, 1]
        train_returns = sample.forward_return.iloc[train].to_numpy(float)
        predicted = np.where(
            probability >= 0.5,
            np.median(train_returns[train_y == 1]),
            np.median(train_returns[train_y == 0]),
        )
        output.dates.extend(sample.index[test].tolist())
        output.y.extend(sample.direction.iloc[test].astype(int).tolist())
        output.p.extend(probability.tolist())
        output.actual.extend(sample.forward_return.iloc[test].astype(float).tolist())
        output.predicted.extend(predicted.tolist())
        output.regimes.extend(sample.regime.iloc[test].astype(str).tolist())
        output.folds.extend([int(fold["fold"])] * len(test))
    return output


def _fit_pooled_oos(
    sample: pd.DataFrame,
    pool: dict[str, pd.DataFrame],
    features: list[str],
    horizon: int,
    model,
    split: str = "test",
) -> OOS:
    development, _ = development_holdout(len(sample), horizon)
    output = OOS([], [], [], [], [], [], [])
    for fold in walk_forward_folds(len(development), horizon):
        test = fold[split]
        cutoff = sample.index[fold["train"][-1]] - pd.offsets.BDay(horizon)
        training = pd.concat(
            [frame.loc[frame.index <= cutoff] for frame in pool.values()], axis=0
        ).sort_index()
        training = training[training.direction >= 0].dropna(subset=["forward_return"])
        if len(training) < 240 or training.direction.nunique() < 2:
            continue
        fitted = clone(model).fit(training[features], training.direction.to_numpy(int))
        probability = fitted.predict_proba(sample[features].iloc[test])[:, 1]
        train_y = training.direction.to_numpy(int)
        train_returns = training.forward_return.to_numpy(float)
        predicted = np.where(
            probability >= 0.5,
            np.median(train_returns[train_y == 1]),
            np.median(train_returns[train_y == 0]),
        )
        output.dates.extend(sample.index[test].tolist())
        output.y.extend(sample.direction.iloc[test].astype(int).tolist())
        output.p.extend(probability.tolist())
        output.actual.extend(sample.forward_return.iloc[test].astype(float).tolist())
        output.predicted.extend(predicted.tolist())
        output.regimes.extend(sample.regime.iloc[test].astype(str).tolist())
        output.folds.extend([int(fold["fold"])] * len(test))
    return output


def _save_result(
    con,
    run_id: str,
    secid: str,
    horizon: int,
    model: str,
    family: str,
    split: str,
    output: OOS,
    baseline_p: np.ndarray,
    baseline_model: str = "unconditional",
) -> dict:
    y = np.asarray(output.y, dtype=int)
    p = np.asarray(output.p, dtype=float)
    actual = np.asarray(output.actual, dtype=float)
    predicted = np.asarray(output.predicted, dtype=float)
    metrics = _metrics(y, p, actual, predicted)
    advantage, low, high = _bootstrap_advantage(y, p, baseline_p)
    folds = np.asarray(output.folds)
    wins = sum(
        balanced_accuracy_score(y[folds == fold], p[folds == fold] >= 0.5)
        > balanced_accuracy_score(y[folds == fold], baseline_p[folds == fold] >= 0.5)
        for fold in np.unique(folds)
        if len(np.unique(y[folds == fold])) == 2
    )
    regime_scores = []
    regimes = np.asarray(output.regimes)
    for regime in np.unique(regimes):
        mask = regimes == regime
        if mask.sum() >= 20 and len(np.unique(y[mask])) == 2:
            regime_scores.append(balanced_accuracy_score(y[mask], p[mask] >= 0.5))
    stability = 1 - float(np.std(regime_scores)) if regime_scores else 0.0
    p_value, permutation_pass, noise_pass = _sanity_checks(y, p, baseline_p)
    status = (
        "shadow_candidate"
        if low > 0 and wins >= 2 and stability >= 0.85 and permutation_pass and noise_pass
        else "experimental"
        if advantage > 0
        else "rejected"
    )
    details = {
        "common_sample": True,
        "untouched_holdout_not_used": split == "pseudo_oos",
        "automatic_promotion": False,
    }
    row = [
        run_id,
        secid,
        horizon,
        model,
        family,
        split,
        len(y),
        _effective_n(actual),
        metrics["balanced_accuracy"],
        metrics["roc_auc"],
        metrics["brier"],
        metrics["log_loss"],
        metrics["mae"],
        metrics["rmse"],
        metrics["rank_ic"],
        metrics["rank_ic"],
        metrics["ece"],
        None,
        None,
        None,
        baseline_model,
        float(balanced_accuracy_score(y, baseline_p >= 0.5)),
        advantage,
        low,
        high,
        wins,
        stability,
        p_value,
        None,
        permutation_pass,
        noise_pass,
        False,
        status,
        json.dumps(details),
    ]
    con.execute("INSERT INTO tournament_results VALUES (" + ",".join("?" for _ in row) + ")", row)
    for day, yy, pp, aa, pred, regime, fold in zip(
        output.dates,
        output.y,
        output.p,
        output.actual,
        output.predicted,
        output.regimes,
        output.folds,
        strict=True,
    ):
        con.execute(
            "INSERT INTO tournament_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, secid, horizon, model, split, fold, day, yy, int(pp >= 0.5), pp, aa, pred, regime],
        )
    return {
        "model": model,
        "family": family,
        "status": status,
        "advantage": advantage,
        "ci_low": low,
        "ci_high": high,
        "metrics": metrics,
    }


def _holdout(sample: pd.DataFrame, features: list[str], horizon: int, model) -> OOS:
    development, holdout = development_holdout(len(sample), horizon)
    fitted = clone(model).fit(
        sample[features].iloc[development], sample.direction.iloc[development].to_numpy(int)
    )
    probability = fitted.predict_proba(sample[features].iloc[holdout])[:, 1]
    train_y = sample.direction.iloc[development].to_numpy(int)
    train_r = sample.forward_return.iloc[development].to_numpy(float)
    predicted = np.where(
        probability >= 0.5, np.median(train_r[train_y == 1]), np.median(train_r[train_y == 0])
    )
    return OOS(
        sample.index[holdout].tolist(),
        sample.direction.iloc[holdout].astype(int).tolist(),
        probability.tolist(),
        sample.forward_return.iloc[holdout].astype(float).tolist(),
        predicted.tolist(),
        sample.regime.iloc[holdout].astype(str).tolist(),
        [0] * len(holdout),
    )


def _holdout_regime(sample: pd.DataFrame, features: list[str], horizon: int, model) -> OOS:
    development, holdout = development_holdout(len(sample), horizon)
    train_y = sample.direction.iloc[development].to_numpy(int)
    global_model = clone(model).fit(sample[features].iloc[development], train_y)
    probability = np.empty(len(holdout))
    for regime in sample.regime.iloc[holdout].unique():
        train_mask = sample.regime.iloc[development].to_numpy() == regime
        test_mask = sample.regime.iloc[holdout].to_numpy() == regime
        eligible = development[train_mask]
        fitted = global_model
        if len(eligible) >= 80 and len(np.unique(sample.direction.iloc[eligible])) == 2:
            fitted = clone(model).fit(
                sample[features].iloc[eligible], sample.direction.iloc[eligible].to_numpy(int)
            )
        probability[test_mask] = fitted.predict_proba(sample[features].iloc[holdout[test_mask]])[:, 1]
    train_r = sample.forward_return.iloc[development].to_numpy(float)
    predicted = np.where(
        probability >= 0.5,
        np.median(train_r[train_y == 1]),
        np.median(train_r[train_y == 0]),
    )
    return OOS(
        sample.index[holdout].tolist(),
        sample.direction.iloc[holdout].astype(int).tolist(),
        probability.tolist(),
        sample.forward_return.iloc[holdout].astype(float).tolist(),
        predicted.tolist(),
        sample.regime.iloc[holdout].astype(str).tolist(),
        [0] * len(holdout),
    )


def _holdout_pooled(
    sample: pd.DataFrame,
    pool: dict[str, pd.DataFrame],
    features: list[str],
    horizon: int,
    model,
) -> OOS:
    _, holdout = development_holdout(len(sample), horizon)
    cutoff = sample.index[holdout[0]] - pd.offsets.BDay(horizon)
    training = pd.concat([frame.loc[frame.index <= cutoff] for frame in pool.values()], axis=0).sort_index()
    training = training[training.direction >= 0].dropna(subset=["forward_return"])
    fitted = clone(model).fit(training[features], training.direction.to_numpy(int))
    probability = fitted.predict_proba(sample[features].iloc[holdout])[:, 1]
    train_y = training.direction.to_numpy(int)
    train_r = training.forward_return.to_numpy(float)
    predicted = np.where(
        probability >= 0.5,
        np.median(train_r[train_y == 1]),
        np.median(train_r[train_y == 0]),
    )
    return OOS(
        sample.index[holdout].tolist(),
        sample.direction.iloc[holdout].astype(int).tolist(),
        probability.tolist(),
        sample.forward_return.iloc[holdout].astype(float).tolist(),
        predicted.tolist(),
        sample.regime.iloc[holdout].astype(str).tolist(),
        [0] * len(holdout),
    )


def run_tournament(
    con, instruments: tuple[str, ...] = INSTRUMENTS, horizons: tuple[int, ...] = HORIZONS
) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    con.execute(
        """UPDATE tournament_runs SET status='interrupted',
        notes=coalesce(notes,'') || '; superseded after interrupted execution'
        WHERE status='running'"""
    )
    state = con.execute("SELECT count(*),max(trade_date) FROM canonical_daily_prices").fetchone()
    dataset = hashlib.sha256(repr((state, VERSION)).encode()).hexdigest()[:20]
    run_id = hashlib.sha256(f"{dataset}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO tournament_runs VALUES (?,?,current_timestamp,'running',?,?,?,?,NULL,0,0,?)",
        [
            run_id,
            dataset,
            json.dumps(instruments),
            json.dumps(horizons),
            NEUTRAL_POLICY,
            HOLDOUT_FRACTION,
            "research only; production frozen",
        ],
    )
    macro = _macro(con)
    frames = {secid: _build_frame(con, secid, macro) for secid in instruments}
    samples = {
        (secid, horizon): _add_targets(frame, horizon, sector_col=SECTOR.get(secid))
        for secid, frame in frames.items()
        for horizon in horizons
    }
    all_results = []
    total_folds = 0
    model_specs = _models()
    for secid, _frame in frames.items():
        for horizon in horizons:
            target = samples[(secid, horizon)]
            available = [
                feature for feature in FAMILIES if feature in target and target[feature].notna().any()
            ]
            sample = target[target.direction >= 0].dropna(subset=["forward_return"]).copy()
            development, holdout = development_holdout(len(sample), horizon)
            folds = walk_forward_folds(len(development), horizon)
            if not folds or not len(holdout):
                continue
            for fold in folds:
                con.execute(
                    "INSERT INTO tournament_folds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        run_id,
                        secid,
                        horizon,
                        fold["fold"],
                        sample.index[fold["train"][0]],
                        sample.index[fold["train"][-1]],
                        sample.index[fold["validation"][0]],
                        sample.index[fold["validation"][-1]],
                        sample.index[fold["test"][0]],
                        sample.index[fold["test"][-1]],
                        horizon,
                        len(fold["train"]),
                        len(fold["validation"]),
                        len(fold["test"]),
                    ],
                )
            total_folds += len(folds)
            baseline_outputs = {
                name: _baseline_oos(sample, horizon, name)
                for name in (
                    "unconditional",
                    "historical_conditional",
                    "momentum",
                    "mean_reversion",
                )
            }
            unconditional_p = np.asarray(baseline_outputs["unconditional"].p)
            for baseline_name, baseline_output in baseline_outputs.items():
                _save_result(
                    con,
                    run_id,
                    secid,
                    horizon,
                    baseline_name,
                    "baseline",
                    "pseudo_oos",
                    baseline_output,
                    unconditional_p,
                )
            best_baseline = max(
                baseline_outputs,
                key=lambda name: balanced_accuracy_score(
                    baseline_outputs[name].y,
                    np.asarray(baseline_outputs[name].p) >= 0.5,
                ),
            )
            baseline_p = np.asarray(baseline_outputs[best_baseline].p)
            candidates = []
            ensemble_components = []
            for name, (family, estimator) in model_specs.items():
                output = _fit_oos(sample, available, horizon, estimator)
                result = _save_result(
                    con,
                    run_id,
                    secid,
                    horizon,
                    name,
                    family,
                    "pseudo_oos",
                    output,
                    baseline_p,
                    best_baseline,
                )
                candidates.append((result, estimator))
                if family in {"linear", "tree", "diagnostic"}:
                    ensemble_components.append(output)
                all_results.append({"secid": secid, "horizon": horizon, **result})
            regime_specs = {
                "regime_linear": model_specs["logistic"][1],
                "regime_tree": model_specs.get("extra_trees", model_specs["logistic"])[1],
            }
            for name, estimator in regime_specs.items():
                output = _fit_regime_oos(sample, available, horizon, estimator)
                result = _save_result(
                    con,
                    run_id,
                    secid,
                    horizon,
                    name,
                    "regime",
                    "pseudo_oos",
                    output,
                    baseline_p,
                    best_baseline,
                )
                candidates.append((result, estimator))
                all_results.append({"secid": secid, "horizon": horizon, **result})
            pool = {
                pool_secid: pool_target[pool_target.direction >= 0].dropna(subset=["forward_return"])
                for (pool_secid, pool_horizon), pool_target in samples.items()
                if pool_horizon == horizon
            }
            pooled_specs = {
                "pooled_linear": model_specs["logistic"][1],
                "pooled_tree": model_specs.get("extra_trees", model_specs["logistic"])[1],
            }
            for name, estimator in pooled_specs.items():
                output = _fit_pooled_oos(sample, pool, available, horizon, estimator)
                if len(output.y) != len(baseline_p):
                    continue
                result = _save_result(
                    con,
                    run_id,
                    secid,
                    horizon,
                    name,
                    "pooled",
                    "pseudo_oos",
                    output,
                    baseline_p,
                    best_baseline,
                )
                candidates.append((result, estimator))
                all_results.append({"secid": secid, "horizon": horizon, **result})
            if ensemble_components:
                ensemble = OOS(
                    ensemble_components[0].dates,
                    ensemble_components[0].y,
                    np.mean([item.p for item in ensemble_components], axis=0).tolist(),
                    ensemble_components[0].actual,
                    np.mean([item.predicted for item in ensemble_components], axis=0).tolist(),
                    ensemble_components[0].regimes,
                    ensemble_components[0].folds,
                )
                ensemble_result = _save_result(
                    con,
                    run_id,
                    secid,
                    horizon,
                    "simple_ensemble",
                    "ensemble",
                    "pseudo_oos",
                    ensemble,
                    baseline_p,
                    best_baseline,
                )
                candidates.append((ensemble_result, None))
                all_results.append({"secid": secid, "horizon": horizon, **ensemble_result})
            best, estimator = max(candidates, key=lambda item: item[0]["advantage"])
            for split_name, fold_key in (("train", "train"), ("validation", "validation")):
                split_baseline = _baseline_oos(sample, horizon, best_baseline, split=fold_key)
                if estimator is None:
                    split_components = [
                        _fit_oos(
                            sample,
                            available,
                            horizon,
                            item_estimator,
                            split=fold_key,
                        )
                        for result, item_estimator in candidates
                        if item_estimator is not None and result["family"] in {"linear", "tree", "diagnostic"}
                    ]
                    split_output = OOS(
                        split_components[0].dates,
                        split_components[0].y,
                        np.mean([item.p for item in split_components], axis=0).tolist(),
                        split_components[0].actual,
                        np.mean([item.predicted for item in split_components], axis=0).tolist(),
                        split_components[0].regimes,
                        split_components[0].folds,
                    )
                elif best["family"] == "regime":
                    split_output = _fit_regime_oos(sample, available, horizon, estimator, split=fold_key)
                elif best["family"] == "pooled":
                    split_output = _fit_pooled_oos(
                        sample, pool, available, horizon, estimator, split=fold_key
                    )
                else:
                    split_output = _fit_oos(sample, available, horizon, estimator, split=fold_key)
                _save_result(
                    con,
                    run_id,
                    secid,
                    horizon,
                    best["model"],
                    best["family"],
                    split_name,
                    split_output,
                    np.asarray(split_baseline.p),
                    best_baseline,
                )
            if estimator is None:
                hold_components = [
                    _holdout(sample, available, horizon, item_estimator)
                    for result, item_estimator in candidates
                    if item_estimator is not None and result["family"] in {"linear", "tree", "diagnostic"}
                ]
                hold = OOS(
                    hold_components[0].dates,
                    hold_components[0].y,
                    np.mean([item.p for item in hold_components], axis=0).tolist(),
                    hold_components[0].actual,
                    np.mean([item.predicted for item in hold_components], axis=0).tolist(),
                    hold_components[0].regimes,
                    hold_components[0].folds,
                )
            elif best["family"] == "regime":
                hold = _holdout_regime(sample, available, horizon, estimator)
            elif best["family"] == "pooled":
                hold = _holdout_pooled(sample, pool, available, horizon, estimator)
            else:
                hold = _holdout(sample, available, horizon, estimator)
            hold_baseline = _baseline_holdout(sample, horizon, best_baseline)
            _save_result(
                con,
                run_id,
                secid,
                horizon,
                best["model"],
                best["family"],
                "untouched_holdout",
                hold,
                np.asarray(hold_baseline.p),
                best_baseline,
            )
            by_family = {
                family: max(
                    (r for r, _ in candidates if r["family"] == family),
                    key=lambda r: r["advantage"],
                    default=None,
                )
                for family in ("linear", "tree", "regime", "pooled")
            }
            best_ensemble = max(
                (r for r, _ in candidates if r["family"] == "ensemble"),
                key=lambda r: r["advantage"],
                default=None,
            )
            winner = best["model"] if best["status"] == "shadow_candidate" else "unconditional"
            status = best["status"] if winner != "unconditional" else "no_reliable_winner"
            con.execute(
                "INSERT INTO tournament_leaderboard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    secid,
                    horizon,
                    best_baseline,
                    by_family["linear"]["model"] if by_family["linear"] else None,
                    by_family["tree"]["model"] if by_family["tree"] else None,
                    by_family["regime"]["model"] if by_family["regime"] else None,
                    by_family["pooled"]["model"] if by_family["pooled"] else None,
                    "momentum",
                    best_ensemble["model"] if best_ensemble else None,
                    winner,
                    status,
                    "strict CI/fold/stability gate; no production promotion",
                ],
            )
    rows = con.execute(
        "SELECT rowid,p_value FROM tournament_results WHERE run_id=? AND split='pseudo_oos' ORDER BY rowid",
        [run_id],
    ).fetchall()
    qvalues = _bh_qvalues([float(row[1]) for row in rows]) if rows else []
    for (rowid, _), qvalue in zip(rows, qvalues, strict=True):
        con.execute("UPDATE tournament_results SET fdr_q=? WHERE rowid=?", [qvalue, rowid])
    _apply_final_gates(con, run_id)
    runtime = time.perf_counter() - started
    con.execute(
        """UPDATE tournament_runs SET status='completed',runtime_seconds=?,
        models_tested=?,folds=? WHERE run_id=?""",
        [runtime, len(all_results), total_folds, run_id],
    )
    return {
        "run_id": run_id,
        "dataset_version": dataset,
        "models_tested": len(all_results),
        "folds": total_folds,
        "runtime_seconds": runtime,
        "shadow_candidates": sum(r["status"] == "shadow_candidate" for r in all_results),
        "automatic_promotion": False,
    }


def tournament_status(con, ensure: bool = True) -> dict:
    if ensure:
        ensure_schema(con)
    latest = con.execute(
        """SELECT run_id,status,runtime_seconds,models_tested,folds
        FROM tournament_runs ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not latest:
        return {"latest": None, "leaderboard": []}
    return {
        "latest": latest,
        "leaderboard": con.execute(
            """SELECT secid,horizon,winner,status,reason FROM tournament_leaderboard
            WHERE run_id=? ORDER BY secid,horizon""",
            [latest[0]],
        ).fetchall(),
    }
