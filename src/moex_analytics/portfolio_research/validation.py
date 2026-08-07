"""Leakage-controlled validation of stage-13 screening candidates."""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from .core import purged_walk_forward
from .schema import DDL

VERSION = "portfolio-validation-v1"
CANDIDATES = {
    "LKOH": "drawdown_60",
    "LSNGP": "volatility_60",
    "MOEX": "drawdown_60",
    "MTSS": "return_60",
    "PHOR": "return_120",
    "SBER": "return_60",
    "SBERP": "return_60",
    "TATN": "return_20",
    "TATNP": "return_20",
    "TRNFP": "drawdown_60",
}


def effective_sample_size(values, max_lag=None):
    x = np.asarray(values, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float(n)
    x = x - x.mean()
    den = float(x @ x)
    if den <= 0:
        return float(n)
    lag = min(max_lag or int(math.sqrt(n)), n - 2)
    total = 0.0
    for k in range(1, lag + 1):
        rho = float(x[:-k] @ x[k:] / den)
        if rho <= 0:
            break
        total += (1 - k / n) * rho
    return float(max(1, min(n, n / (1 + 2 * total))))


def block_bootstrap(actual, prediction, baseline, block_length, draws=400, seed=42):
    y, p, b = map(lambda z: np.asarray(z, float), (actual, prediction, baseline))
    n = len(y)
    rng = np.random.default_rng(seed)
    out = []
    if not n:
        return np.array([])
    for _ in range(draws):
        idx = []
        while len(idx) < n:
            start = int(rng.integers(0, max(n - block_length + 1, 1)))
            idx.extend(range(start, min(start + block_length, n)))
        idx = np.asarray(idx[:n])
        delta = float(np.mean((y[idx] - b[idx]) ** 2) - np.mean((y[idx] - p[idx]) ** 2))
        ic = float(np.corrcoef(y[idx], p[idx])[0, 1]) if np.std(p[idx]) else math.nan
        out.append((delta, ic))
    return np.asarray(out)


def newey_west_t(values, lags):
    x = np.asarray(values, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return math.nan
    u = x - x.mean()
    var = float(u @ u / n)
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - lag / (lags + 1)) * float(u[lag:] @ u[:-lag] / n)
    return float(x.mean() / math.sqrt(max(var / n, 1e-18)))


def _feature(frame, name):
    close = frame.close.astype(float)
    ret = close.pct_change(fill_method=None)
    if name.startswith("return_"):
        return close.pct_change(int(name.split("_")[1]), fill_method=None)
    if name.startswith("volatility_"):
        return ret.rolling(int(name.split("_")[1])).std()
    if name == "drawdown_60":
        return close / close.rolling(60).max() - 1
    raise ValueError(name)


def validate_series(frame, feature, horizon, draws=400):
    data = frame[["trade_date", "close"]].copy()
    data["x"] = _feature(data, feature)
    data["y"] = data.close.shift(-horizon) / data.close - 1
    data["mom"] = data.close.pct_change(horizon, fill_method=None)
    data = data.dropna().reset_index(drop=True)
    folds = purged_walk_forward(len(data), horizon, min_train=max(250, 3 * horizon), folds=5)
    predictions = []
    coefficients = []
    for fold, (train, test) in enumerate(folds):
        tr = data.iloc[train]
        te = data.iloc[test]
        mean = float(tr.x.mean())
        std = float(tr.x.std()) or 1.0
        z = (tr.x - mean) / std
        design = np.c_[np.ones(len(tr)), z]
        beta = np.linalg.lstsq(design, tr.y.to_numpy(), rcond=None)[0]
        pred = beta[0] + beta[1] * (te.x.to_numpy() - mean) / std
        base = np.full(len(te), float(tr.y.mean()))
        regime = np.where(te.x.abs() >= tr.x.abs().quantile(0.7), "stress", "normal")
        for i, row in enumerate(te.itertuples()):
            predictions.append(
                (
                    fold,
                    row.trade_date,
                    row.y,
                    float(pred[i]),
                    float(base[i]),
                    float(row.mom),
                    float(beta[1]),
                    str(regime[i]),
                )
            )
        coefficients.append(float(beta[1]))
    if not predictions:
        return {
            "status": "insufficient_history",
            "n": len(data),
            "predictions": [],
            "bootstrap": np.empty((0, 2)),
        }
    out = pd.DataFrame(
        predictions,
        columns=[
            "fold",
            "trade_date",
            "actual",
            "prediction",
            "baseline",
            "momentum",
            "coefficient",
            "regime",
        ],
    )
    boot = block_bootstrap(out.actual, out.prediction, out.baseline, max(horizon, 5), draws)
    delta = (out.actual - out.baseline) ** 2 - (out.actual - out.prediction) ** 2
    ic = float(out.actual.corr(out.prediction))
    ric = float(out.actual.rank().corr(out.prediction.rank()))
    ci = np.quantile(boot[:, 0], [0.025, 0.975])
    stable = max(sum(np.asarray(coefficients) > 0), sum(np.asarray(coefficients) < 0))
    status = (
        "validated_candidate"
        if ci[0] > 0 and stable >= 4
        else "rejected"
        if ci[1] < 0
        else "conditional_candidate"
        if boot[:, 0].mean() > 0 and stable >= 3
        else "unstable"
        if abs(ic) >= 0.02
        else "rejected"
    )
    if len(data) < 750:
        status = "insufficient_history"
    regimes = {
        r: {
            "n": len(g),
            "effective_n": effective_sample_size(g.actual),
            "ic": g.actual.corr(g.prediction),
            "rank_ic": g.actual.rank().corr(g.prediction.rank()),
            "sign": float(np.sign(g.coefficient.median())),
        }
        for r, g in out.groupby("regime")
    }
    rng = np.random.default_rng(7)
    perm = []
    noise = []
    for _ in range(100):
        perm.append(abs(np.corrcoef(rng.permutation(out.actual), out.prediction)[0, 1]))
        noise.append(abs(np.corrcoef(rng.normal(size=len(out)), out.actual)[0, 1]))
    return {
        "status": status,
        "n": len(data),
        "effective_n": effective_sample_size(out.actual),
        "oos_ic": ic,
        "oos_rank_ic": ric,
        "baseline_mse": float(np.mean((out.actual - out.baseline) ** 2)),
        "model_mse": float(np.mean((out.actual - out.prediction) ** 2)),
        "momentum_mse": float(np.mean((out.actual - out.momentum) ** 2)),
        "hac_t": newey_west_t(delta, horizon),
        "ci": ci,
        "stable": stable,
        "folds": len(coefficients),
        "regimes": regimes,
        "sanity": {
            "label_permutation_p": float(np.mean(np.asarray(perm) >= abs(ic))),
            "noise_p": float(np.mean(np.asarray(noise) >= abs(ic))),
        },
        "predictions": predictions,
        "bootstrap": boot,
    }


def validate_portfolio_alpha(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    run = hashlib.sha256(VERSION.encode()).hexdigest()[:16]
    for secid, feature in CANDIDATES.items():
        frame = con.execute(
            "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? ORDER BY trade_date",
            [secid],
        ).df()
        horizon = int(feature.split("_")[-1]) if feature.startswith("return_") else 20
        result = validate_series(frame, feature, horizon)
        con.execute("DELETE FROM portfolio_alpha_validations WHERE run_id=? AND secid=?", [run, secid])
        con.execute("DELETE FROM portfolio_alpha_fold_predictions WHERE run_id=? AND secid=?", [run, secid])
        con.execute("DELETE FROM portfolio_alpha_bootstrap WHERE run_id=? AND secid=?", [run, secid])
        con.execute(
            "INSERT INTO portfolio_alpha_validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run,
                secid,
                feature,
                horizon,
                result["status"],
                result["n"],
                result.get("effective_n"),
                result.get("oos_ic"),
                result.get("oos_rank_ic"),
                result.get("baseline_mse"),
                result.get("model_mse"),
                result.get("momentum_mse"),
                result.get("hac_t"),
                float(result.get("ci", [math.nan, math.nan])[0]),
                float(result.get("ci", [math.nan, math.nan])[1]),
                int(result.get("stable", 0)),
                int(result.get("folds", 0)),
                json.dumps(result.get("regimes", {})),
                json.dumps(result.get("sanity", {})),
                VERSION,
            ],
        )
        for row in result.get("predictions", []):
            con.execute(
                "INSERT INTO portfolio_alpha_fold_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [run, secid, feature, horizon, *row],
            )
        for i, row in enumerate(result.get("bootstrap", [])):
            con.execute(
                "INSERT INTO portfolio_alpha_bootstrap VALUES (?,?,?,?,?,?,?)",
                [run, secid, feature, horizon, i, *map(float, row)],
            )
    return {
        "run_id": run,
        "candidates": len(CANDIDATES),
        "statuses": dict(
            con.execute(
                "SELECT status,count(*) FROM portfolio_alpha_validations WHERE run_id=? GROUP BY 1", [run]
            ).fetchall()
        ),
    }


def validate_cross_instrument_factors(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    run = hashlib.sha256((VERSION + "cross").encode()).hexdigest()[:16]
    con.execute("DELETE FROM portfolio_cross_validations WHERE run_id=?", [run])
    features = ("volatility_20", "volatility_60", "drawdown_60", "return_250")
    frames = {
        s: con.execute(
            "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? ORDER BY trade_date",
            [s],
        ).df()
        for (s,) in con.execute("SELECT secid FROM portfolio_instruments WHERE secid<>'X5'").fetchall()
    }
    for feature in features:
        stats = {}
        for secid, frame in frames.items():
            x = _feature(frame, feature)
            y = frame.close.shift(-20) / frame.close - 1
            valid = pd.DataFrame({"x": x, "y": y}).dropna()
            stats[secid] = (float(valid.x.corr(valid.y)), len(valid), effective_sample_size(valid.y))
        for held, (ic, n, neff) in stats.items():
            train = [v[0] for k, v in stats.items() if k != held and np.isfinite(v[0])]
            sign = float(np.sign(np.median(train)))
            test = ic * sign
            cls = "universal" if np.mean(np.asarray(train) * sign > 0) >= 0.7 else "issuer-specific"
            status = "conditional_candidate" if test > 0.02 else "rejected"
            con.execute(
                "INSERT INTO portfolio_cross_validations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [run, feature, 20, held, sign, test, test, n, neff, cls, status],
            )
    return {
        "run_id": run,
        "rows": con.execute(
            "SELECT count(*) FROM portfolio_cross_validations WHERE run_id=?", [run]
        ).fetchone()[0],
    }
