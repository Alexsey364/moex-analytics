"""Leakage-aware challenger research; this module cannot promote production models."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.metrics import (
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    ndcg_score,
    roc_auc_score,
)
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .schema import DDL

VERSION = "adaptive-learning-v1"
INSTRUMENTS = ("SBERP", "LKOH", "MTSS", "TRNFP", "MOEX")
HORIZONS = (5, 20, 60, 120)
SECTOR = {
    "SBERP": "moex_finance",
    "LKOH": "moex_oil_gas",
    "MTSS": "moex_telecom",
    "TRNFP": "moex_oil_gas",
    "MOEX": "moex_finance",
}
FAMILIES = {
    "ret_1": "technical",
    "ret_5": "technical",
    "ret_20": "technical",
    "ret_60": "technical",
    "vol_5": "volatility",
    "vol_20": "volatility",
    "vol_60": "volatility",
    "drawdown_20": "drawdown",
    "drawdown_60": "drawdown",
    "turnover_log": "liquidity",
    "turnover_change": "liquidity",
    "volume_change": "liquidity",
    "trades_change": "turnover",
    "amihud": "liquidity",
    "relative_20": "relative_strength",
    "breadth_balance": "breadth",
    "turnover_balance": "breadth",
    "dispersion": "dispersion",
    "market_ret": "relative_strength",
    "rvi_change": "volatility",
    "rusfar_change": "rates",
    "rgbi_change": "rates",
    "cny_change": "fx",
    "usd_fix_change": "fx",
    "momentum_x_volatility": "interaction",
    "relative_x_breadth": "interaction",
    "drawdown_x_stress": "interaction",
    "liquidity_x_volatility": "interaction",
}


def ensure_schema(con) -> None:
    con.execute(DDL)


def temporal_folds(n: int, horizon: int, folds: int = 3) -> list[dict]:
    """Expanding train, separate validation/test and an embargo at least equal to horizon."""
    minimum = max(300, horizon * 4)
    if n < minimum + 3 * horizon + 60:
        return []
    initial = max(minimum, int(n * 0.45))
    remaining = n - initial - horizon
    block = remaining // (folds * 2)
    if block < max(20, horizon // 2):
        return []
    result = []
    for fold in range(folds):
        train_end = initial + fold * 2 * block
        val_start = train_end + horizon
        val_end = min(val_start + block, n)
        test_start = val_end + horizon
        test_end = min(test_start + block, n)
        if test_end - test_start < 15:
            break
        result.append(
            {
                "fold": fold + 1,
                "train": np.arange(train_end),
                "validation": np.arange(val_start, val_end),
                "test": np.arange(test_start, test_end),
                "embargo": horizon,
            }
        )
    return result


def _dataset_version(con) -> str:
    state = con.execute(
        "SELECT count(*),max(trade_date),count(distinct secid) FROM moex_equity_eod"
    ).fetchone()
    canonical = con.execute("SELECT count(*),max(trade_date) FROM canonical_daily_prices").fetchone()
    return hashlib.sha256(repr((state, canonical, VERSION)).encode()).hexdigest()[:20]


def _macro(con) -> pd.DataFrame:
    ids = (
        "moex_imoex",
        "moex_rvi",
        "moex_rusfar",
        "moex_rgbi",
        "moex_cny_rub",
        "cbr_usd_rub",
        "moex_finance",
        "moex_oil_gas",
        "moex_consumer",
        "moex_power",
    )
    marks = ",".join("?" for _ in ids)
    frame = con.execute(
        f"""SELECT observation_date trade_date,series_id,value FROM macro_observations
        WHERE series_id IN ({marks}) QUALIFY row_number() over(PARTITION BY series_id,observation_date
        ORDER BY available_from DESC)=1""",
        list(ids),
    ).df()
    if frame.empty:
        return pd.DataFrame({"trade_date": pd.Series(dtype="datetime64[ns]")})
    return frame.pivot(index="trade_date", columns="series_id", values="value").reset_index()


def _build_frame(con, secid: str, macro: pd.DataFrame) -> pd.DataFrame:
    p = con.execute(
        """SELECT trade_date,open,high,low,close,volume,value turnover,
        number_of_trades num_trades FROM canonical_daily_prices
        WHERE canonical_secid=? ORDER BY trade_date""",
        [secid],
    ).df()
    p.trade_date = pd.to_datetime(p.trade_date)
    p = p.drop_duplicates("trade_date").set_index("trade_date")
    returns = p.close.pct_change(fill_method=None)
    for h in (1, 5, 20, 60):
        p[f"ret_{h}"] = p.close.pct_change(h, fill_method=None)
    for h in (5, 20, 60):
        p[f"vol_{h}"] = returns.rolling(h).std() * np.sqrt(252)
    for h in (20, 60):
        p[f"drawdown_{h}"] = p.close / p.close.rolling(h).max() - 1
    p["turnover_log"] = np.log1p(p.turnover.clip(lower=0))
    p["turnover_change"] = p.turnover.pct_change(fill_method=None).clip(-10, 10)
    p["volume_change"] = p.volume.pct_change(fill_method=None).clip(-10, 10)
    p["trades_change"] = p.num_trades.pct_change(fill_method=None).clip(-10, 10)
    p["amihud"] = returns.abs() / p.turnover.replace(0, np.nan)
    breadth = con.execute("""SELECT trade_date,
        (advancing-declining)::DOUBLE/nullif(tradable_count,0) breadth_balance,
        (advancing_turnover-declining_turnover)/nullif(total_turnover,0) turnover_balance,
        return_dispersion dispersion FROM market_breadth_daily ORDER BY trade_date""").df()
    breadth.trade_date = pd.to_datetime(breadth.trade_date)
    market = macro.copy()
    market.trade_date = pd.to_datetime(market.trade_date)
    p = (
        p.reset_index()
        .merge(breadth, on="trade_date", how="left")
        .merge(market, on="trade_date", how="left")
        .set_index("trade_date")
    )
    p["market_ret"] = p.get("moex_imoex", pd.Series(index=p.index, dtype=float)).pct_change(fill_method=None)
    p["rvi_change"] = p.get("moex_rvi", pd.Series(index=p.index, dtype=float)).pct_change(fill_method=None)
    p["rusfar_change"] = p.get("moex_rusfar", pd.Series(index=p.index, dtype=float)).diff()
    p["rgbi_change"] = p.get("moex_rgbi", pd.Series(index=p.index, dtype=float)).pct_change(fill_method=None)
    p["cny_change"] = p.get("moex_cny_rub", pd.Series(index=p.index, dtype=float)).pct_change(
        fill_method=None
    )
    p["usd_fix_change"] = p.get("cbr_usd_rub", pd.Series(index=p.index, dtype=float)).pct_change(
        fill_method=None
    )
    p["relative_20"] = p.ret_20 - p.market_ret.rolling(20).sum()
    p["stress"] = (
        (p.rvi_change > p.rvi_change.rolling(250).quantile(0.75)) | (p.breadth_balance < -0.35)
    ).astype(int)
    p["regime"] = np.where(p.stress == 1, "stress", "normal")
    p["momentum_x_volatility"] = p.ret_20 * p.vol_20
    p["relative_x_breadth"] = p.relative_20 * p.breadth_balance
    p["drawdown_x_stress"] = p.drawdown_60 * p.stress
    p["liquidity_x_volatility"] = p.turnover_log * p.vol_20
    return p.replace([np.inf, -np.inf], np.nan)


def _add_targets(frame: pd.DataFrame, horizon: int, market_col="moex_imoex", sector_col=None) -> pd.DataFrame:
    f = frame.copy()
    f["forward_return"] = f.close.shift(-horizon) / f.close - 1
    market = f.get(market_col, pd.Series(index=f.index, dtype=float))
    f["excess_imoex"] = f.forward_return - (market.shift(-horizon) / market - 1)
    sector = f.get(sector_col, pd.Series(index=f.index, dtype=float)) if sector_col else None
    f["excess_sector"] = (
        f.forward_return - (sector.shift(-horizon) / sector - 1) if sector is not None else np.nan
    )
    highs = pd.concat([f.high.shift(-i) for i in range(1, horizon + 1)], axis=1).max(axis=1)
    lows = pd.concat([f.low.shift(-i) for i in range(1, horizon + 1)], axis=1).min(axis=1)
    f["mfe"] = highs / f.close - 1
    f["mae"] = lows / f.close - 1
    neutral_band = np.maximum(
        0.002, f.vol_20.fillna(f.vol_20.median()) / np.sqrt(252) * np.sqrt(horizon) * 0.15
    )
    f["neutral"] = f.forward_return.abs() <= neutral_band
    f["direction"] = np.where(f.neutral, -1, (f.forward_return > 0).astype(int))
    for level, name in ((0.03, "3"), (0.05, "5"), (0.10, "10")):
        f[f"touch_up_{name}"] = f.mfe >= level
        f[f"touch_down_{name}"] = f.mae <= -level
    return f.iloc[:-horizon]


def _effective_n(values) -> float:
    x = pd.Series(values).dropna().to_numpy(float)
    if len(x) < 3:
        return float(len(x))
    rho = np.corrcoef(x[:-1], x[1:])[0, 1]
    return float(len(x) * (1 - np.nan_to_num(rho)) / max(1e-6, 1 + np.nan_to_num(rho)))


def _ece(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    value = 0.0
    for lo, hi in pairwise(edges):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if mask.any():
            value += mask.mean() * abs(y[mask].mean() - p[mask].mean())
    return float(value)


def _calibrate(validation_y, validation_p, test_p):
    if len(np.unique(validation_y)) < 2:
        return test_p, None, None
    eps = 1e-5
    x = np.log(np.clip(validation_p, eps, 1 - eps) / np.clip(1 - validation_p, eps, 1 - eps))[:, None]
    model = LogisticRegression(C=1).fit(x, validation_y)
    tx = np.log(np.clip(test_p, eps, 1 - eps) / np.clip(1 - test_p, eps, 1 - eps))[:, None]
    return model.predict_proba(tx)[:, 1], float(model.coef_[0, 0]), float(model.intercept_[0])


def _specs(seed=22):
    def linear():
        return make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.2, max_iter=500)
        )

    return {
        "logistic_l2": (
            linear(),
            make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=5)),
        ),
        "elastic_net": (
            make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                LogisticRegression(
                    C=0.15,
                    l1_ratio=0.25,
                    solver="saga",
                    max_iter=800,
                    random_state=seed,
                ),
            ),
            TransformedTargetRegressor(
                regressor=make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                    ElasticNet(alpha=0.002, l1_ratio=0.25, max_iter=2000),
                ),
                transformer=StandardScaler(),
            ),
        ),
        "random_forest": (
            make_pipeline(
                SimpleImputer(strategy="median"),
                RandomForestClassifier(
                    n_estimators=80,
                    max_depth=5,
                    min_samples_leaf=20,
                    max_features=0.6,
                    n_jobs=1,
                    random_state=seed,
                ),
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                RandomForestRegressor(
                    n_estimators=80,
                    max_depth=5,
                    min_samples_leaf=20,
                    max_features=0.6,
                    n_jobs=1,
                    random_state=seed,
                ),
            ),
        ),
        "extra_trees": (
            make_pipeline(
                SimpleImputer(strategy="median"),
                ExtraTreesClassifier(
                    n_estimators=80,
                    max_depth=6,
                    min_samples_leaf=15,
                    max_features=0.7,
                    n_jobs=1,
                    random_state=seed,
                ),
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                ExtraTreesRegressor(
                    n_estimators=80,
                    max_depth=6,
                    min_samples_leaf=15,
                    max_features=0.7,
                    n_jobs=1,
                    random_state=seed,
                ),
            ),
        ),
        "hist_gradient": (
            make_pipeline(
                SimpleImputer(strategy="median"),
                HistGradientBoostingClassifier(
                    max_iter=80,
                    max_leaf_nodes=15,
                    min_samples_leaf=25,
                    l2_regularization=2,
                    random_state=seed,
                ),
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                HistGradientBoostingRegressor(
                    max_iter=80,
                    max_leaf_nodes=15,
                    min_samples_leaf=25,
                    l2_regularization=2,
                    random_state=seed,
                ),
            ),
        ),
        "knn": (
            make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                KNeighborsClassifier(n_neighbors=35, weights="distance"),
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                KNeighborsRegressor(n_neighbors=35, weights="distance"),
            ),
        ),
    }


def _safe_auc(y, p):
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else math.nan


@dataclass
class ModelResult:
    model: str
    y: list
    p: list
    actual: list
    predicted: list
    dates: list
    regimes: list
    folds: int
    wins: int
    slopes: list
    intercepts: list
    q: list
    importances: list


def _fit_instrument(con, run_id, secid, horizon, frame, features, scope="per_instrument"):
    target = _add_targets(frame, horizon, sector_col=SECTOR.get(secid))
    sample = target[target.direction >= 0].dropna(subset=["forward_return"])
    folds = temporal_folds(len(sample), horizon)
    if not folds:
        return []
    x = sample[features].to_numpy(float)
    y = sample.direction.to_numpy(int)
    r = sample.forward_return.to_numpy(float)
    baseline_rate = []
    baseline_y = []
    results = []
    for name, (classifier, regressor) in _specs().items():
        out = ModelResult(name, [], [], [], [], [], [], 0, 0, [], [], [], [])
        for fold in folds:
            tr, va, te = fold["train"], fold["validation"], fold["test"]
            if len(np.unique(y[tr])) < 2:
                continue
            clf, reg = clone(classifier), clone(regressor)
            clf.fit(x[tr], y[tr])
            reg.fit(x[tr], r[tr])
            vp = clf.predict_proba(x[va])[:, 1]
            raw = clf.predict_proba(x[te])[:, 1]
            p, slope, intercept = _calibrate(y[va], vp, raw)
            pred = reg.predict(x[te])
            residual = r[va] - reg.predict(x[va])
            quantiles = np.quantile(residual, [0.1, 0.25, 0.5, 0.75, 0.9])
            q = pred[:, None] + quantiles[None, :]
            base = np.repeat(y[tr].mean(), len(te))
            baseline_rate.extend(base)
            baseline_y.extend(y[te])
            win = balanced_accuracy_score(y[te], p >= 0.5) > balanced_accuracy_score(y[te], base >= 0.5)
            out.wins += int(win)
            out.folds += 1
            out.y.extend(y[te])
            out.p.extend(p)
            out.actual.extend(r[te])
            out.predicted.extend(pred)
            out.dates.extend(sample.index[te])
            out.regimes.extend(sample.regime.iloc[te])
            out.slopes.append(slope)
            out.intercepts.append(intercept)
            out.q.extend(q.tolist())
            con.execute(
                "INSERT OR IGNORE INTO adaptive_folds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    secid,
                    horizon,
                    scope,
                    fold["fold"],
                    sample.index[tr[0]],
                    sample.index[tr[-1]],
                    sample.index[va[0]],
                    sample.index[va[-1]],
                    sample.index[te[0]],
                    sample.index[te[-1]],
                    fold["embargo"],
                    len(tr),
                    len(va),
                    len(te),
                ],
            )
            for i, index in enumerate(te):
                con.execute(
                    "INSERT INTO adaptive_fold_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        run_id,
                        secid,
                        horizon,
                        scope,
                        name,
                        fold["fold"],
                        sample.index[index],
                        int(y[index]),
                        int(p[i] >= 0.5),
                        float(p[i]),
                        False,
                        float(r[index]),
                        float(pred[i]),
                        *map(float, q[i]),
                        sample.regime.iloc[index],
                    ],
                )
            if name in {"logistic_l2", "random_forest", "extra_trees"}:
                try:
                    pi = permutation_importance(
                        clf, x[te], y[te], n_repeats=3, random_state=22, scoring="balanced_accuracy"
                    )
                    out.importances.append(pi.importances_mean)
                except ValueError:
                    pass
        if out.y:
            results.append(
                (out, np.asarray(baseline_y[-len(out.y) :]), np.asarray(baseline_rate[-len(out.y) :]))
            )
    return results


def _fit_pooled_loo(con, run_id, heldout, horizon, frames, features):
    """Train on other issuers only and test transfer to a completely held-out instrument."""
    target = _add_targets(frames[heldout], horizon, sector_col=SECTOR.get(heldout))
    test_sample = target[target.direction >= 0].dropna(subset=["forward_return"])
    split = int(len(test_sample) * 0.75)
    if split < 300 or len(test_sample) - split < 50:
        return []
    embargo_pos = max(0, split - horizon)
    cutoff = test_sample.index[embargo_pos]
    pooled = []
    for secid, frame in frames.items():
        if secid == heldout:
            continue
        other = _add_targets(frame, horizon, sector_col=SECTOR.get(secid))
        other = other[(other.direction >= 0) & (other.index < cutoff)].dropna(subset=["forward_return"])
        pooled.append(other)
    if not pooled:
        return []
    train_all = pd.concat(pooled).sort_index()
    val_start = int(len(train_all) * 0.85)
    train, validation = train_all.iloc[:val_start], train_all.iloc[val_start:]
    test = test_sample.iloc[split:]
    xtr, xva, xte = (part[features].to_numpy(float) for part in (train, validation, test))
    ytr, yva, yte = (part.direction.to_numpy(int) for part in (train, validation, test))
    rtr, rva, rte = (part.forward_return.to_numpy(float) for part in (train, validation, test))
    specs = _specs()
    selected = {name: specs[name] for name in ("logistic_l2", "extra_trees")}
    results = []
    for name, (classifier, regressor) in selected.items():
        clf, reg = clone(classifier), clone(regressor)
        clf.fit(xtr, ytr)
        reg.fit(xtr, rtr)
        vp, raw = clf.predict_proba(xva)[:, 1], clf.predict_proba(xte)[:, 1]
        p, slope, intercept = _calibrate(yva, vp, raw)
        pred = reg.predict(xte)
        residual = rva - reg.predict(xva)
        quantiles = np.quantile(residual, [0.1, 0.25, 0.5, 0.75, 0.9])
        q = pred[:, None] + quantiles[None, :]
        base = np.repeat(ytr.mean(), len(yte))
        result = ModelResult(
            f"pooled_{name}",
            yte.tolist(),
            p.tolist(),
            rte.tolist(),
            pred.tolist(),
            test.index.tolist(),
            test.regime.tolist(),
            1,
            int(balanced_accuracy_score(yte, p >= 0.5) > balanced_accuracy_score(yte, base >= 0.5)),
            [slope],
            [intercept],
            q.tolist(),
            [],
        )
        con.execute(
            "INSERT OR IGNORE INTO adaptive_folds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                heldout,
                horizon,
                "pooled_loo",
                1,
                train.index.min(),
                train.index.max(),
                validation.index.min(),
                validation.index.max(),
                test.index.min(),
                test.index.max(),
                horizon,
                len(train),
                len(validation),
                len(test),
            ],
        )
        for i, day in enumerate(test.index):
            con.execute(
                "INSERT INTO adaptive_fold_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    heldout,
                    horizon,
                    "pooled_loo",
                    result.model,
                    1,
                    day,
                    int(yte[i]),
                    int(p[i] >= 0.5),
                    float(p[i]),
                    False,
                    float(rte[i]),
                    float(pred[i]),
                    *map(float, q[i]),
                    test.regime.iloc[i],
                ],
            )
        results.append((result, yte, base))
    return results


def _save_result(con, run_id, secid, horizon, scope, result, base_y, base_p, features):
    y, p, a, pred = (
        np.asarray(result.y),
        np.asarray(result.p),
        np.asarray(result.actual),
        np.asarray(result.predicted),
    )
    ba = float(balanced_accuracy_score(y, p >= 0.5))
    bba = float(balanced_accuracy_score(base_y, base_p >= 0.5))
    auc = _safe_auc(y, p)
    brier = float(brier_score_loss(y, p))
    bbrier = float(brier_score_loss(base_y, base_p))
    ll = float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6)))
    ece = _ece(y, p)
    qs = np.asarray(result.q)
    coverage = [float(np.mean((a >= qs[:, i]) & (a <= qs[:, j]))) for i, j in ((1, 3), (0, 4), (0, 4))]
    regime_scores = []
    for regime in set(result.regimes):
        mask = np.asarray(result.regimes) == regime
        if mask.sum() >= 20 and len(np.unique(y[mask])) == 2:
            regime_scores.append(balanced_accuracy_score(y[mask], p[mask] >= 0.5))
    stability = 1 - float(np.std(regime_scores)) if regime_scores else 0.0
    slope = (
        float(np.nanmedian([v for v in result.slopes if v is not None]))
        if any(v is not None for v in result.slopes)
        else None
    )
    intercept = (
        float(np.nanmedian([v for v in result.intercepts if v is not None]))
        if any(v is not None for v in result.intercepts)
        else None
    )
    allowed = bool(
        len(y) >= 200
        and brier < bbrier
        and auc >= 0.55
        and ece <= 0.08
        and result.wins >= 2
        and slope is not None
        and 0.5 <= slope <= 1.5
    )
    delta = ba - bba
    confidence = float(
        np.clip(
            0.25 * np.log1p(len(y)) / np.log(1000)
            + 0.25 * result.wins / max(result.folds, 1)
            + 0.25 * stability
            + 0.25 * (1 - ece),
            0,
            1,
        )
    )
    status = (
        "candidate"
        if allowed and delta > 0.01
        else "shadow"
        if delta > 0 and result.wins >= 2
        else "experimental"
        if delta >= -0.01
        else "rejected"
    )
    details = {
        "probability_gate": {
            "sufficient_oos": len(y) >= 200,
            "brier_better": brier < bbrier,
            "auc": auc,
            "ece": ece,
            "fold_wins": result.wins,
        },
        "automatic_promotion": False,
        "neutral_policy": "excluded from binary fit; stored separately",
        "interval_note": "empirical validation residual quantiles; 80/90 share outer q10-q90 in v1",
    }
    con.execute(
        "INSERT INTO adaptive_model_leaderboard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            run_id,
            secid,
            horizon,
            scope,
            result.model,
            "tree" if result.model in {"random_forest", "extra_trees", "hist_gradient"} else "statistical",
            len(y),
            result.folds,
            ba,
            auc,
            brier,
            ll,
            bba,
            bbrier,
            delta,
            float(mean_absolute_error(a, pred)),
            float(mean_squared_error(a, pred) ** 0.5),
            float(pd.Series(a).corr(pd.Series(pred), method="spearman")),
            *coverage,
            slope,
            intercept,
            ece,
            stability,
            result.wins,
            allowed,
            confidence,
            status,
            json.dumps(details),
        ],
    )
    registry_id = hashlib.sha256(f"{run_id}:{secid}:{horizon}:{scope}:{result.model}".encode()).hexdigest()[
        :24
    ]
    live = con.execute(
        """SELECT count(*),avg(o.direction_correct::int) FROM forecast_registry f JOIN forecast_outcomes o USING(forecast_id)
        WHERE f.secid=? AND f.horizon_sessions=? AND o.outcome_status='matured'""",
        [secid, horizon],
    ).fetchone()
    oos = {"balanced_accuracy": ba, "roc_auc": auc, "brier": brier, "delta": delta}
    con.execute(
        "INSERT INTO adaptive_model_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp,TRUE,FALSE)",
        [
            registry_id,
            run_id,
            result.model,
            VERSION,
            scope,
            secid,
            horizon,
            json.dumps(features),
            max(result.dates),
            json.dumps({"calibration_slope": slope, "ece": ece}),
            json.dumps(oos),
            json.dumps({"n": live[0], "hit_rate": live[1]}),
            json.dumps({"stability": stability}),
            status,
        ],
    )
    recommendation = (
        "eligible_for_manual_review"
        if status == "candidate" and live[0] >= 100
        else "continue_shadow"
        if status in {"candidate", "shadow"}
        else "do_not_promote"
    )
    con.execute(
        "INSERT INTO adaptive_promotion_review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
        [
            run_id,
            secid,
            horizon,
            result.model,
            json.dumps(oos),
            json.dumps(oos),
            live[0],
            json.dumps({"hit_rate": live[1]}),
            json.dumps({"slope": slope, "intercept": intercept, "ece": ece, "allowed": allowed}),
            stability,
            "insufficient_live_sample" if live[0] < 100 else "monitor",
            delta,
            recommendation,
            "manual approval required; production frozen",
        ],
    )
    if result.importances:
        values = np.vstack(result.importances)
        for idx, feature in enumerate(features):
            con.execute(
                "INSERT INTO adaptive_feature_importance VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    secid,
                    horizon,
                    scope,
                    result.model,
                    feature,
                    FAMILIES[feature],
                    float(np.mean(values[:, idx])),
                    float(1 / (1 + np.std(values[:, idx]))),
                    None,
                    None,
                    "fold_permutation",
                ],
            )
    return {
        "model": result.model,
        "status": status,
        "delta": delta,
        "auc": auc,
        "brier": brier,
        "probability_allowed": allowed,
        "confidence": confidence,
    }


def _ranking(con, run_id, horizon, frames):
    rows = []
    for secid, frame in frames.items():
        target = _add_targets(frame, horizon, sector_col=SECTOR.get(secid))
        for day, row in target.dropna(subset=["forward_return", "relative_20"]).iterrows():
            rows.append((day, secid, row.forward_return, row.relative_20))
    data = pd.DataFrame(rows, columns=["date", "secid", "forward", "score"])
    if data.empty:
        return
    data["rank"] = data.groupby("date").forward.rank(pct=True)
    data["pred_rank"] = data.groupby("date").score.rank(pct=True)
    ic = float(data[["rank", "pred_rank"]].corr(method="spearman").iloc[0, 1])
    top = float(data[data.pred_rank >= 0.8].forward.mean())
    bottom = float(data[data.pred_rank <= 0.2].forward.mean())
    valid = [g for _, g in data.groupby("date") if len(g) >= 3]
    ndcg = (
        float(np.mean([ndcg_score([g.forward.rank().to_numpy()], [g.score.to_numpy()]) for g in valid]))
        if valid
        else math.nan
    )
    last = data[data.date == data.date.max()].sort_values("pred_rank", ascending=False)
    for scope, subset in (("tradable_focus", last), ("portfolio_only", last)):
        con.execute(
            "INSERT INTO adaptive_ranking_results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                horizon,
                scope,
                len(data),
                ic,
                top,
                bottom,
                ndcg,
                json.dumps(subset.secid.head(3).tolist()),
                json.dumps(subset.secid.tail(3).tolist()),
                "experimental_no_trading_action",
            ],
        )


def research_predictive_models(con) -> dict:
    started = time.perf_counter()
    ensure_schema(con)
    dataset = _dataset_version(con)
    run_id = hashlib.sha256(f"{dataset}:{datetime.now().isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO adaptive_research_runs VALUES (?,?,?,current_timestamp,?,?,'running',NULL,0,0,0,?)",
        [
            run_id,
            dataset,
            VERSION,
            json.dumps(INSTRUMENTS),
            json.dumps(HORIZONS),
            "research only; no production promotion",
        ],
    )
    for feature, family in FAMILIES.items():
        con.execute(
            "INSERT OR IGNORE INTO adaptive_feature_registry VALUES (?,?,?,?,?,?,?,?)",
            [
                dataset,
                feature,
                family,
                "MOEX/CBR/local PIT store",
                "known after source session/release",
                "v1",
                "verified_or_lagged",
                "research feature",
            ],
        )
    macro = _macro(con)
    frames = {secid: _build_frame(con, secid, macro) for secid in INSTRUMENTS}
    features = list(FAMILIES)
    leaderboard = []
    model_count = fold_count = target_rows = 0
    for secid, frame in frames.items():
        flags = con.execute(
            "SELECT count(*) FROM market_history_quality_issues WHERE secid=? AND issue_type='large_return_corporate_action_review'",
            [secid],
        ).fetchone()[0]
        for horizon in HORIZONS:
            target = _add_targets(frame, horizon, sector_col=SECTOR.get(secid))
            usable = target[target.direction >= 0].dropna(subset=["forward_return"])
            miss = float(usable[features].isna().mean().mean())
            effective = _effective_n(usable.forward_return)
            years = (usable.index.max() - usable.index.min()).days / 365.25
            quality = (
                "sufficient_for_challenger"
                if len(usable) >= 600 and len(features) / max(effective, 1) < 0.2
                else "simple_models_only"
            )
            con.execute(
                "INSERT INTO adaptive_data_sufficiency VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    secid,
                    horizon,
                    len(usable),
                    effective,
                    years,
                    usable.regime.nunique(),
                    miss,
                    len(features),
                    len(features) / max(len(usable), 1),
                    quality,
                    flags,
                ],
            )
            for day, row in target.iterrows():
                if pd.isna(row.forward_return):
                    continue
                con.execute(
                    "INSERT INTO adaptive_targets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                    [
                        run_id,
                        day,
                        secid,
                        horizon,
                        row.forward_return,
                        None if row.direction < 0 else int(row.direction),
                        bool(row.neutral),
                        row.excess_imoex,
                        row.excess_sector,
                        row.mae,
                        row.mfe,
                        bool(row.touch_up_3),
                        bool(row.touch_up_5),
                        bool(row.touch_up_10),
                        bool(row.touch_down_3),
                        bool(row.touch_down_5),
                        bool(row.touch_down_10),
                    ],
                )
                target_rows += 1
            results = _fit_instrument(con, run_id, secid, horizon, frame, features)
            for result, by, bp in results:
                leaderboard.append(
                    {
                        "secid": secid,
                        "horizon": horizon,
                        **_save_result(
                            con, run_id, secid, horizon, "per_instrument", result, by, bp, features
                        ),
                    }
                )
                model_count += 1
                fold_count += result.folds
            best = max(
                (x for x in leaderboard if x["secid"] == secid and x["horizon"] == horizon),
                key=lambda x: x["delta"],
                default=None,
            )
            if best:
                for family in ("technical", "breadth", "liquidity", "rates", "fx", "volatility"):
                    con.execute(
                        "INSERT INTO adaptive_feature_ablation VALUES (?,?,?,?,?,?,?,?)",
                        [
                            run_id,
                            secid,
                            horizon,
                            family,
                            best["delta"],
                            None,
                            None,
                            "scheduled_common_sample_research",
                        ],
                    )
    for horizon in HORIZONS:
        for heldout in INSTRUMENTS:
            for result, by, bp in _fit_pooled_loo(con, run_id, heldout, horizon, frames, features):
                leaderboard.append(
                    {
                        "secid": heldout,
                        "horizon": horizon,
                        **_save_result(con, run_id, heldout, horizon, "pooled_loo", result, by, bp, features),
                    }
                )
                model_count += 1
                fold_count += result.folds
    for horizon in HORIZONS:
        _ranking(con, run_id, horizon, frames)
    runtime = time.perf_counter() - started
    con.execute(
        "UPDATE adaptive_research_runs SET status='completed',runtime_seconds=?,rows_total=?,models_trained=?,folds=? WHERE run_id=?",
        [runtime, target_rows, model_count, fold_count, run_id],
    )
    return {
        "run_id": run_id,
        "dataset_version": dataset,
        "models_trained": model_count,
        "folds": fold_count,
        "target_rows": target_rows,
        "runtime_seconds": runtime,
        "leaderboard": leaderboard,
        "automatic_promotion": False,
    }


def research_status(con) -> dict:
    ensure_schema(con)
    latest = con.execute(
        "SELECT run_id,dataset_version,created_at,status,runtime_seconds,rows_total,models_trained,folds FROM adaptive_research_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {
        "latest": latest,
        "statuses": con.execute(
            "SELECT status,count(*) FROM adaptive_model_leaderboard GROUP BY 1 ORDER BY 1"
        ).fetchall(),
        "promotion": con.execute(
            "SELECT recommendation,count(*) FROM adaptive_promotion_review GROUP BY 1 ORDER BY 1"
        ).fetchall(),
    }
