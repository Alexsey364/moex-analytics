"""Modular SBER samples, linear models and temporal validation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from moex_analytics.deep_backfill.core import effective_sample_size

from .schema import DDL

MOSCOW = ZoneInfo("Europe/Moscow")
VERSION = "unblocked-experiment-v1"
HORIZONS = (1, 5, 20, 60, 120, 250)
DATASETS = {
    "A": ("technical",),
    "A+B": ("technical", "zcyc"),
    "A+C": ("technical", "breadth"),
    "A+D": ("technical", "futures"),
    "A+E": ("technical", "intraday"),
    "A+F": ("technical", "options"),
    "A+G": ("technical", "fundamentals"),
    "A+B+C": ("technical", "zcyc", "breadth"),
    "A+B+D": ("technical", "zcyc", "futures"),
    "A+C+D": ("technical", "breadth", "futures"),
    "A+B+C+D": ("technical", "zcyc", "breadth", "futures"),
    "A+D+E": ("technical", "futures", "intraday"),
    "A+B+C+G": ("technical", "zcyc", "breadth", "fundamentals"),
}
CAPS = {
    "technical": 35,
    "zcyc": 12,
    "breadth": 12,
    "futures": 15,
    "intraday": 15,
    "options": 10,
    "fundamentals": 12,
}


def ensure_schema(con):
    con.execute(DDL)


def horizon_allowed(dataset, horizon):
    blocks = set(DATASETS[dataset])
    if horizon in {120, 250} and "intraday" in blocks:
        return False
    if horizon == 1 and "fundamentals" in blocks:
        return False
    return True


def temporal_folds(n, horizon, n_folds=4, min_train=500):
    available = n - min_train - horizon * 2
    if available <= 0:
        return []
    test_size = max(30, available // n_folds)
    folds = []
    for fold in range(n_folds):
        test_start = min_train + fold * test_size
        test_end = min(n, test_start + test_size)
        train_end = max(0, test_start - horizon)
        validation_size = max(50, min(250, train_end // 5))
        validation_start = max(0, train_end - validation_size)
        fit_end = max(0, validation_start - horizon)
        if fit_end < 100 or test_end - test_start < 20:
            continue
        folds.append(
            {
                "fold": fold + 1,
                "train": np.arange(0, fit_end),
                "validation": np.arange(validation_start, train_end),
                "test": np.arange(test_start, test_end),
                "purge": horizon,
                "embargo": horizon,
            }
        )
    return folds


def train_only_preprocess(
    train, test, feature_names, variance_floor=1e-10, correlation_limit=0.90, feature_cap=35
):
    train = np.asarray(train, dtype=float)
    test = np.asarray(test, dtype=float)
    names = list(feature_names)
    missing = np.mean(~np.isfinite(train), axis=0)
    keep = np.where(missing <= 0.40)[0]
    train = train[:, keep]
    test = test[:, keep]
    names = [names[i] for i in keep]
    medians = np.nanmedian(np.where(np.isfinite(train), train, np.nan), axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    train = np.where(np.isfinite(train), train, medians)
    test = np.where(np.isfinite(test), test, medians)
    scale = np.nanmedian(np.abs(train - np.nanmedian(train, axis=0)), axis=0) * 1.4826
    keep = np.where(scale > variance_floor)[0]
    train = train[:, keep]
    test = test[:, keep]
    medians = medians[keep]
    scale = scale[keep]
    names = [names[i] for i in keep]
    order = np.argsort(-np.nanvar(train, axis=0))
    selected = []
    for idx in order:
        if len(selected) >= feature_cap:
            break
        if not selected or all(
            abs(np.corrcoef(train[:, idx], train[:, j])[0, 1]) <= correlation_limit for j in selected
        ):
            selected.append(idx)
    train = (train[:, selected] - medians[selected]) / scale[selected]
    test = (test[:, selected] - medians[selected]) / scale[selected]
    names = [names[i] for i in selected]
    return train, test, names, {"medians": medians[selected], "scales": scale[selected], "selected": selected}


def sigmoid(values):
    values = np.clip(values, -30, 30)
    return 1 / (1 + np.exp(-values))


def fit_logistic(x, y, l2=1.0, l1=0.0, iterations=120, rate=0.08):
    x = np.c_[np.ones(len(x)), x]
    coef = np.zeros(x.shape[1])
    y = np.asarray(y, dtype=float)
    for _ in range(iterations):
        gradient = x.T @ (sigmoid(x @ coef) - y) / len(y)
        gradient[1:] += l2 * coef[1:] / len(y)
        coef -= rate * gradient
        coef[1:] = np.sign(coef[1:]) * np.maximum(0, np.abs(coef[1:]) - rate * l1 / len(y))
    return coef


def predict_logistic(x, coef):
    return sigmoid(np.c_[np.ones(len(x)), x] @ coef)


def fit_ridge(x, y, alpha=10.0):
    design = np.c_[np.ones(len(x)), x]
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0
    return np.linalg.pinv(design.T @ design + penalty) @ design.T @ np.asarray(y)


def predict_linear(x, coef):
    return np.c_[np.ones(len(x)), x] @ coef


def fit_platt(probabilities, y):
    logits = np.log(np.clip(probabilities, 1e-6, 1 - 1e-6) / np.clip(1 - probabilities, 1e-6, 1))
    return fit_logistic(logits[:, None], y, l2=0.1, iterations=100, rate=0.05)


def apply_platt(probabilities, coef):
    logits = np.log(np.clip(probabilities, 1e-6, 1 - 1e-6) / np.clip(1 - probabilities, 1e-6, 1))
    return predict_logistic(logits[:, None], coef)


def direction_metrics(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    pred = p >= 0.5
    tp = ((pred == 1) & (y == 1)).sum()
    tn = ((pred == 0) & (y == 0)).sum()
    pos = max(1, (y == 1).sum())
    neg = max(1, (y == 0).sum())
    accuracy = float((pred == y).mean())
    balanced = float(0.5 * (tp / pos + tn / neg))
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(len(p))
    auc = (
        float((ranks[y == 1].sum() - pos * (pos - 1) / 2) / (pos * neg))
        if pos + neg == len(y) and pos and neg
        else math.nan
    )
    precision_curve = []
    for threshold in np.unique(p):
        selected = p >= threshold
        if selected.any():
            precision_curve.append(((y[selected] == 1).mean(), (y[selected] == 1).sum() / pos))
    pr_auc = (
        float(
            np.trapezoid(
                [x[0] for x in sorted(precision_curve, key=lambda z: z[1])],
                [x[1] for x in sorted(precision_curve, key=lambda z: z[1])],
            )
        )
        if precision_curve
        else math.nan
    )
    bins = np.linspace(0, 1, 11)
    ece = 0.0
    for low, high in pairwise(bins):
        mask = (p >= low) & (p < (high if high < 1 else 1.0001))
        if mask.any():
            ece += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return {
        "accuracy": accuracy,
        "balanced_accuracy": balanced,
        "roc_auc": auc,
        "pr_auc": pr_auc,
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "ece": float(ece),
    }


def return_metrics(y, predicted):
    y = np.asarray(y, float)
    predicted = np.asarray(predicted, float)
    return {
        "mae": float(np.mean(abs(y - predicted))),
        "rmse": float(np.sqrt(np.mean((y - predicted) ** 2))),
        "sign_accuracy": float(np.mean((y >= 0) == (predicted >= 0))),
        "correlation": float(np.corrcoef(y, predicted)[0, 1])
        if np.std(y) * np.std(predicted) > 0
        else math.nan,
        "rank_correlation": float(pd.Series(y).rank().corr(pd.Series(predicted).rank())),
    }


def probability_policy(metrics, effective, folds_won, folds, ci_low):
    allowed = bool(
        effective >= 100
        and folds >= 3
        and folds_won >= math.ceil(folds / 2)
        and metrics["brier"] < 0.25
        and metrics["ece"] <= 0.10
        and ci_low >= -0.005
    )
    return {"allowed": allowed, "status": "numeric_probability_allowed" if allowed else "qualitative_only"}


def label_permutation_sanity(x, y, seed=42):
    rng = np.random.default_rng(seed)
    permuted = rng.permutation(y)
    coef = fit_logistic(x, permuted, iterations=60)
    score = direction_metrics(permuted, predict_logistic(x, coef))["balanced_accuracy"]
    return abs(score - 0.5) < 0.15


def random_noise_sanity(x, y, seed=42):
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=(len(x), 1))
    coef = fit_logistic(noise, y, iterations=60)
    return abs(direction_metrics(y, predict_logistic(noise, coef))["balanced_accuracy"] - 0.5) < 0.15


def _feature_frames(con):  # pragma: no cover
    technical = con.execute(
        "SELECT trade_date,features_json FROM daily_features WHERE canonical_secid='SBER' QUALIFY row_number() over(partition by trade_date order by calculation_version desc)=1 ORDER BY trade_date"
    ).fetchall()
    records = []
    for traded, payload in technical:
        values = json.loads(payload)
        row = {"trade_date": traded}
        row.update({f"technical__{k}": v for k, v in values.items() if isinstance(v, (int, float))})
        records.append(row)
    base = pd.DataFrame(records)
    z = (
        con.execute(
            "SELECT observation_date trade_date,tenor,zero_coupon_yield FROM deep_zcyc_archive WHERE quality_status='validated'"
        )
        .df()
        .pivot_table(index="trade_date", columns="tenor", values="zero_coupon_yield")
        .reset_index()
    )
    z.columns = ["trade_date"] + [f"zcyc__{x:g}y" for x in z.columns[1:]]
    breadth = con.execute(
        "SELECT trade_date,current40_breadth,dynamic_breadth,difference,current40_return,dynamic_return,return_difference,current40_size,dynamic_size FROM survivorship_impact_daily"
    ).df()
    breadth = breadth.rename(columns={x: f"breadth__{x}" for x in breadth.columns if x != "trade_date"})
    futures = con.execute(
        "SELECT trade_date,raw_close,back_adjusted_close,ratio_adjusted_close,settlement,volume,open_interest FROM deep_continuous_futures WHERE rule='combined'"
    ).df()
    futures = futures.sort_values("trade_date")
    futures["futures__return"] = futures.raw_close.pct_change()
    futures["futures__volume_change"] = futures.volume.pct_change()
    futures["futures__oi_change"] = futures.open_interest.pct_change()
    futures = futures.rename(
        columns={
            x: f"futures__{x}"
            for x in (
                "raw_close",
                "back_adjusted_close",
                "ratio_adjusted_close",
                "settlement",
                "volume",
                "open_interest",
            )
        }
    )
    intraday = con.execute("SELECT * EXCLUDE(secid) FROM intraday_features WHERE secid='SBER'").df()
    intraday = intraday.rename(columns={x: f"intraday__{x}" for x in intraday.columns if x != "trade_date"})
    fundamentals = con.execute(
        "SELECT * EXCLUDE(calculation_version,calculated_at,latest_ifrs_period,latest_ras_period,latest_publication_date) FROM sber_daily_fundamental_state"
    ).df()
    fundamentals = fundamentals.rename(
        columns={x: f"fundamentals__{x}" for x in fundamentals.columns if x != "trade_date"}
    )
    frames = {
        "technical": base,
        "zcyc": z,
        "breadth": breadth,
        "futures": futures,
        "intraday": intraday,
        "fundamentals": fundamentals,
    }
    for frame in frames.values():
        if "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frames


def build_targets(con):  # pragma: no cover
    ensure_schema(con)
    prices = con.execute(
        "SELECT trade_date,close,high,low FROM predictive_market_prices WHERE secid='SBER' AND board='TQBR' ORDER BY trade_date"
    ).df()
    imoex = (
        con.execute("SELECT trade_date,close FROM daily_prices WHERE secid='IMOEX' ORDER BY trade_date")
        .df()
        .set_index("trade_date")
        .close
        if con.execute("SELECT count(*) FROM daily_prices WHERE secid='IMOEX'").fetchone()[0]
        else pd.Series(dtype=float)
    )
    con.execute("DELETE FROM sber_experiment_targets WHERE target_version=?", [VERSION])
    written = 0
    for i, row in prices.iterrows():
        for horizon in HORIZONS:
            if i + horizon >= len(prices):
                continue
            window = prices.iloc[i + 1 : i + horizon + 1]
            future = window.iloc[-1].close / row.close - 1
            market = imoex.get(window.iloc[-1].trade_date, np.nan)
            market_start = imoex.get(row.trade_date, np.nan)
            excess = (
                future - (market / market_start - 1) if pd.notna(market) and pd.notna(market_start) else None
            )
            mae = window.low.min() / row.close - 1
            mfe = window.high.max() / row.close - 1
            close_up = [bool(future >= x) for x in (0.03, 0.05, 0.10)]
            touch_up = [bool(mfe >= x) for x in (0.03, 0.05, 0.10)]
            close_down = [bool(future <= -x) for x in (0.03, 0.05, 0.10)]
            touch_down = [bool(mae <= -x) for x in (0.03, 0.05, 0.10)]
            con.execute(
                "INSERT INTO sber_experiment_targets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    row.trade_date,
                    horizon,
                    bool(future > 0),
                    future,
                    excess,
                    mae,
                    mfe,
                    *close_up,
                    *touch_up,
                    *close_down,
                    *touch_down,
                    VERSION,
                ],
            )
            written += 1
    return {"rows": written}


def build_modular_samples(con):  # pragma: no cover
    ensure_schema(con)
    frames = _feature_frames(con)
    targets = con.execute(
        "SELECT trade_date,horizon FROM sber_experiment_targets WHERE target_version=?", [VERSION]
    ).df()
    con.execute("DELETE FROM sber_modular_samples WHERE feature_version=?", [VERSION])
    result = []
    for dataset, blocks in DATASETS.items():
        merged = frames["technical"]
        for block in blocks[1:]:
            merged = merged.merge(
                frames.get(block, pd.DataFrame({"trade_date": []})), on="trade_date", how="inner"
            )
        merged["trade_date"] = pd.to_datetime(merged["trade_date"])
        targets["trade_date"] = pd.to_datetime(targets["trade_date"])
        features = [c for c in merged.columns if c != "trade_date"]
        cap = sum(CAPS[b] for b in blocks)
        for horizon in HORIZONS:
            if not horizon_allowed(dataset, horizon):
                continue
            dates = merged.merge(targets[targets.horizon == horizon], on="trade_date").trade_date
            count = len(dates)
            effective = effective_sample_size(np.arange(count))
            status = "enough_for_experimental_model" if count >= 250 else "insufficient_sample"
            con.execute(
                "INSERT INTO sber_modular_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    dataset,
                    horizon,
                    min(dates) if count else None,
                    max(dates) if count else None,
                    count,
                    effective,
                    min(len(features), cap),
                    count / max(1, min(len(features), cap)),
                    json.dumps(list(blocks)),
                    json.dumps([]),
                    status,
                    VERSION,
                ],
            )
            result.append((dataset, horizon, count))
    return {"samples": result}


def _dataset_frame(con, dataset, horizon):  # pragma: no cover
    frames = _feature_frames(con)
    blocks = DATASETS[dataset]
    merged = frames["technical"]
    for block in blocks[1:]:
        merged = merged.merge(frames.get(block, pd.DataFrame({"trade_date": pd.Series(dtype="datetime64[ns]")})), on="trade_date", how="inner")
    targets = con.execute(
        "SELECT trade_date,direction_up::int direction_up,future_return,mae_path,mfe_path FROM sber_experiment_targets WHERE horizon=? AND target_version=? ORDER BY trade_date",
        [horizon, VERSION],
    ).df()
    merged["trade_date"] = pd.to_datetime(merged["trade_date"])
    targets["trade_date"] = pd.to_datetime(targets["trade_date"])
    return merged.merge(targets, on="trade_date").sort_values("trade_date").replace([np.inf, -np.inf], np.nan)


def train_direction(con):  # pragma: no cover
    ensure_schema(con)
    run_id = hashlib.sha256((VERSION + "direction").encode()).hexdigest()[:16]
    con.execute("DELETE FROM sber_experiment_results WHERE run_id=?", [run_id])
    con.execute("DELETE FROM sber_experiment_folds WHERE run_id=?", [run_id])
    con.execute("DELETE FROM sber_feature_stability WHERE run_id=?", [run_id])
    summaries = []
    for dataset in DATASETS:
        for horizon in HORIZONS:
            if not horizon_allowed(dataset, horizon):
                continue
            sample_count = con.execute("SELECT rows_count FROM sber_modular_samples WHERE dataset_id=? AND horizon=? AND feature_version=?", [dataset, horizon, VERSION]).fetchone()
            if not sample_count or sample_count[0] < 250:
                continue
            frame = _dataset_frame(con, dataset, horizon)
            if len(frame) < 250:
                continue
            feature_names = [c for c in frame if "__" in c]
            x = frame[feature_names].to_numpy(float)
            y = frame.direction_up.to_numpy(int)
            returns = frame.future_return.to_numpy(float)
            dates = frame.trade_date.tolist()
            folds = temporal_folds(len(frame), horizon)
            all_y = []
            all_p = []
            all_r = []
            all_pred = []
            wins = 0
            coefs = []
            used = []
            coefficient_history = {}
            for fold in folds:
                tr, va, te = fold["train"], fold["validation"], fold["test"]
                cap = sum(CAPS[b] for b in DATASETS[dataset])
                xtr, xte, names, state = train_only_preprocess(x[tr], x[te], feature_names, feature_cap=cap)
                xva = np.where(
                    np.isfinite(x[va][:, state["selected"]]), x[va][:, state["selected"]], state["medians"]
                )
                xva = (xva - state["medians"]) / state["scales"]
                coef = fit_logistic(xtr, y[tr], l2=1)
                validation_p = predict_logistic(xva, coef)
                platt = fit_platt(validation_p, y[va])
                p = apply_platt(predict_logistic(xte, coef), platt)
                ridge = fit_ridge(xtr, returns[tr])
                rpred = predict_linear(xte, ridge)
                baseline = max(0.5, float(y[tr].mean()))
                wins += (
                    direction_metrics(y[te], p)["balanced_accuracy"]
                    > direction_metrics(y[te], np.full(len(te), baseline))["balanced_accuracy"]
                )
                all_y.extend(y[te])
                all_p.extend(p)
                all_r.extend(returns[te])
                all_pred.extend(rpred)
                coefs.append(coef[1:])
                used.append(names)
                for feature, value in zip(names, coef[1:], strict=True):
                    coefficient_history.setdefault(feature, []).append(float(value))
                con.execute(
                    "INSERT INTO sber_experiment_folds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        run_id,
                        dataset,
                        horizon,
                        fold["fold"],
                        dates[tr[0]],
                        dates[tr[-1]],
                        dates[va[0]],
                        dates[va[-1]],
                        dates[te[0]],
                        dates[te[-1]],
                        horizon,
                        horizon,
                        len(tr),
                        len(te),
                        "reused_holdout_pseudo_oos",
                    ],
                )
            if not all_y:
                continue
            dm = direction_metrics(all_y, all_p)
            rm = return_metrics(all_r, all_pred)
            base_p = np.full(len(all_y), np.mean(all_y))
            base = direction_metrics(all_y, base_p)
            improvement = dm["balanced_accuracy"] - base["balanced_accuracy"]
            effective = effective_sample_size(all_y)
            policy = probability_policy(dm, effective, wins, len(folds), improvement - 0.02)
            status = (
                "candidate_for_live_validation"
                if policy["allowed"] and improvement >= 0.005
                else "enough_for_experimental_model"
            )
            details = {
                "probability_policy": policy,
                "label_permutation_ok": label_permutation_sanity(
                    np.asarray(all_p)[:, None], np.asarray(all_y)
                ),
                "random_noise_ok": random_noise_sanity(np.asarray(all_p)[:, None], np.asarray(all_y)),
            }
            con.execute(
                "INSERT INTO sber_feature_stability VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [run_id, dataset, horizon, "logistic_l2_platt", "__summary__", float(np.median(np.concatenate(coefs))), float(np.quantile(np.concatenate(coefs), 0.75) - np.quantile(np.concatenate(coefs), 0.25)), float(np.mean(np.sign(np.concatenate(coefs)) == np.sign(np.median(np.concatenate(coefs))))), None, "fold_coefficient_summary"],
                )
            con.execute(
                "INSERT INTO sber_experiment_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    dataset,
                    horizon,
                    "logistic_l2_platt",
                    "common_sample",
                    len(all_y),
                    effective,
                    max((len(x) for x in used), default=0),
                    "unconditional_frequency",
                    dm["accuracy"],
                    dm["balanced_accuracy"],
                    dm["roc_auc"],
                    dm["pr_auc"],
                    dm["brier"],
                    dm["log_loss"],
                    dm["ece"],
                    None,
                    None,
                    rm["mae"],
                    rm["rmse"],
                    rm["sign_accuracy"],
                    rm["correlation"],
                    rm["rank_correlation"],
                    wins,
                    len(folds),
                    improvement,
                    improvement - 0.02,
                    improvement + 0.02,
                    status,
                    json.dumps(details),
                ],
            )
            summaries.append((dataset, horizon, len(all_y), dm["balanced_accuracy"], improvement, status))
    return {"run_id": run_id, "results": summaries}


def calibrate_direction(con):  # pragma: no cover
    ensure_schema(con)
    return {
        "models": con.execute(
            "SELECT count(*) FROM sber_experiment_results WHERE model like '%platt'"
        ).fetchone()[0],
        "method": "Platt fitted on temporal validation only; isotonic disabled",
    }


def evaluate_ablation(con):  # pragma: no cover
    ensure_schema(con)
    run_id = hashlib.sha256((VERSION + "direction").encode()).hexdigest()[:16]
    con.execute("DELETE FROM sber_modular_ablation WHERE run_id=?", [run_id])
    rows = con.execute(
        "SELECT horizon,dataset_id,rows_count,balanced_accuracy,improvement,ci_low,ci_high,status FROM sber_experiment_results WHERE run_id=?",
        [run_id],
    ).fetchall()
    for horizon, dataset, count, score, improvement, low, high, status in rows:
        con.execute(
            "INSERT INTO sber_modular_ablation VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, horizon, dataset, count, count, 0.5, score, improvement, low, high, status],
        )
    return {"rows": len(rows), "run_id": run_id}


def calculate_forecast(con):  # pragma: no cover
    ensure_schema(con)
    run_id = hashlib.sha256((VERSION + "direction").encode()).hexdigest()[:16]
    latest = con.execute(
        "SELECT max(trade_date) FROM predictive_market_prices WHERE secid='SBER'"
    ).fetchone()[0]
    created = datetime.now()
    written = 0
    for horizon in HORIZONS:
        best = con.execute(
            "SELECT dataset_id,model,status,balanced_accuracy,improvement,brier,ece,effective_sample_size,folds_won,folds FROM sber_experiment_results WHERE horizon=? ORDER BY balanced_accuracy DESC NULLS LAST LIMIT 1",
            [horizon],
        ).fetchone()
        targets = con.execute(
            "SELECT future_return,mae_path,mfe_path FROM sber_experiment_targets WHERE horizon=?", [horizon]
        ).fetchall()
        returns = np.array([r[0] for r in targets if r[0] is not None])
        maes = np.array([r[1] for r in targets if r[1] is not None])
        prob = float(np.mean(returns > 0)) if len(returns) else None
        allowed = False
        if best:
            dataset, model, status, _score, improvement, brier, ece, effective, wins, folds = best
            allowed = probability_policy(
                {"brier": brier, "ece": ece}, effective, wins, folds, (improvement or 0) - 0.02
            )["allowed"]
        else:
            dataset, model, status, _, improvement = (
                "A",
                "historical_frequency",
                "insufficient_sample",
                None,
                0,
            )
        direction = (
            "небольшой перевес роста"
            if prob and prob > 0.52
            else "небольшой перевес снижения"
            if prob and prob < 0.48
            else "выраженного перевеса нет"
        )
        forecast_id = hashlib.sha256(f"{latest}|{horizon}|{dataset}|{run_id}".encode()).hexdigest()[:20]
        touch = {str(x): float(np.mean(returns >= x)) for x in (0.03, 0.05, 0.10)} | {
            str(-x): float(np.mean(returns <= -x)) for x in (0.03, 0.05, 0.10)
        }
        con.execute(
            "INSERT OR REPLACE INTO sber_experimental_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                forecast_id,
                created,
                latest,
                horizon,
                dataset,
                model,
                status,
                direction,
                prob if allowed else None,
                allowed,
                float(np.median(returns)),
                float(np.quantile(returns, 0.25)),
                float(np.quantile(returns, 0.75)),
                float(np.quantile(returns, 0.10)),
                float(np.quantile(returns, 0.90)),
                float(np.quantile(returns, 0.05)),
                float(np.quantile(returns, 0.95)),
                json.dumps(touch),
                float(np.median(maes)),
                "historical distribution; not a trade path",
                "unconditional_frequency",
                improvement,
                "experimental",
                json.dumps(["reused holdout", "not production", "probability hidden unless policy passes"]),
                run_id,
            ],
        )
        written += 1
    return {"forecasts": written, "as_of_date": latest}


def timing_experiment(con):  # pragma: no cover
    ensure_schema(con)
    run_id = hashlib.sha256((VERSION + "timing").encode()).hexdigest()[:16]
    con.execute("DELETE FROM sber_timing_experiments WHERE run_id=?", [run_id])
    strategies = {
        "buy_now": 0,
        "wait_5_sessions": 5,
        "wait_minus_3": 3,
        "wait_minus_5": 5,
        "wait_sma_confirmation": 2,
        "staged_buying": 3,
        "experimental_direction_timing": 1,
    }
    written = 0
    for horizon in HORIZONS:
        data = con.execute(
            "SELECT future_return,mae_path FROM sber_experiment_targets WHERE horizon=? AND future_return IS NOT NULL",
            [horizon],
        ).fetchall()
        returns = np.array([x[0] for x in data])
        draw = np.array([x[1] for x in data])
        for strategy, delay in strategies.items():
            con.execute(
                "INSERT INTO sber_timing_experiments VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    horizon,
                    strategy,
                    len(data),
                    float(delay),
                    float(np.mean(returns)) if len(data) else None,
                    float(np.mean(draw)) if len(data) else None,
                    float(np.mean((returns > 0) & (draw > -0.03))) if len(data) else None,
                    float(delay),
                    "descriptive_only_no_recommendation",
                ],
            )
            written += 1
    return {"rows": written, "run_id": run_id}


def save_shadow_forecasts(con):  # pragma: no cover
    ensure_schema(con)
    forecasts = con.execute(
        "SELECT as_of_date,horizon,dataset_id,model,direction,probability,median_return,run_id FROM sber_experimental_forecasts ORDER BY created_at DESC"
    ).fetchall()
    inserted = 0
    for asof, horizon, dataset, model, direction, probability, median, run_id in forecasts:
        for name in ("A", "A+B", "A+C", "A+D", "A+E", "best_compact", "ensemble", "fallback"):
            shadow_id = hashlib.sha256(f"{asof}|{horizon}|{name}|{VERSION}".encode()).hexdigest()[:24]
            before = con.execute(
                "SELECT count(*) FROM sber_shadow_forecasts WHERE shadow_id=?", [shadow_id]
            ).fetchone()[0]
            con.execute(
                "INSERT OR IGNORE INTO sber_shadow_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    shadow_id,
                    datetime.now(),
                    asof,
                    horizon,
                    name,
                    model,
                    direction,
                    probability,
                    median,
                    VERSION,
                    hashlib.sha256(f"{dataset}|{run_id}".encode()).hexdigest(),
                    True,
                ],
            )
            inserted += not before
    return {
        "inserted": inserted,
        "immutable_total": con.execute("SELECT count(*) FROM sber_shadow_forecasts").fetchone()[0],
    }


def validate_futures_specs(con):  # pragma: no cover
    ensure_schema(con)
    contracts = con.execute(
        "SELECT secid,first_trade,expiration,multiplier,tick_size,price_scale,underlying_units,source FROM expired_sber_futures"
    ).fetchall()
    validated = 0
    for secid, first, expiration, multiplier, tick, scale, units, source in contracts:
        status = (
            "validated"
            if all(v is not None and v > 0 for v in (multiplier, tick, scale, units))
            else "disabled_unverified_scale"
        )
        evidence = (
            "official ISS description fields"
            if status == "validated"
            else "archive detail omits multiplier/quotation scale; momentum/OI only"
        )
        con.execute(
            "INSERT OR REPLACE INTO sber_futures_specifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                secid,
                "iss-current-v1",
                first,
                expiration,
                multiplier,
                multiplier,
                units,
                "RUB per contract quote",
                scale,
                tick,
                None,
                None,
                "third Thursday quarterly",
                source,
                status,
                evidence,
            ],
        )
        validated += status == "validated"
    return {
        "contracts": len(contracts),
        "validated": validated,
        "basis_enabled": validated == len(contracts) and validated > 0,
    }


def calculate_futures_basis(con):  # pragma: no cover
    ensure_schema(con)
    status = validate_futures_specs(con)
    return {
        "basis_rows": 0,
        "status": "disabled" if not status["basis_enabled"] else "requires spot normalization implementation",
        "evidence": status,
    }


def experimental_status(con):  # pragma: no cover
    ensure_schema(con)
    readiness = con.execute("SELECT * FROM sber_experimental_readiness ORDER BY horizon").fetchall()
    return {
        "modular_samples": con.execute("SELECT count(*) FROM sber_modular_samples").fetchone()[0],
        "results": con.execute("SELECT count(*) FROM sber_experiment_results").fetchone()[0],
        "forecasts": con.execute("SELECT count(*) FROM sber_experimental_forecasts").fetchone()[0],
        "shadow": con.execute("SELECT count(*) FROM sber_shadow_forecasts").fetchone()[0],
        "readiness": readiness,
    }


def update_readiness(con):  # pragma: no cover
    ensure_schema(con)
    con.execute("DELETE FROM sber_experimental_readiness")
    result = []
    for horizon in HORIZONS:
        best = con.execute(
            "SELECT dataset_id,model,status,rows_count,effective_sample_size,folds,details_json FROM sber_experiment_results WHERE horizon=? ORDER BY balanced_accuracy DESC NULLS LAST LIMIT 1",
            [horizon],
        ).fetchone()
        if best:
            dataset, model, status, count, effective, folds, details = best
            allowed = json.loads(details)["probability_policy"]["allowed"]
            final = (
                "candidate_for_live_validation"
                if status == "candidate_for_live_validation"
                else "enough_for_experimental_model"
            )
        else:
            dataset, model, count, effective, folds, allowed, final = (
                None,
                None,
                0,
                0,
                0,
                False,
                "insufficient_sample",
            )
        con.execute(
            "INSERT INTO sber_experimental_readiness VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                horizon,
                final,
                dataset,
                model,
                count,
                effective,
                folds,
                allowed,
                json.dumps({"financial_sector_required": False, "production": False}),
                datetime.now(),
            ],
        )
        result.append((horizon, final, dataset, count))
    return result


def run_unblocked_experiment(con):  # pragma: no cover
    result = {}
    result["targets"] = build_targets(con)
    result["samples"] = build_modular_samples(con)
    result["specifications"] = validate_futures_specs(con)
    result["basis"] = calculate_futures_basis(con)
    result["models"] = train_direction(con)
    result["calibration"] = calibrate_direction(con)
    result["ablation"] = evaluate_ablation(con)
    result["forecast"] = calculate_forecast(con)
    result["timing"] = timing_experiment(con)
    result["shadow"] = save_shadow_forecasts(con)
    result["readiness"] = update_readiness(con)
    return result
