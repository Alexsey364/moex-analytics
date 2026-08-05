"""Strict, reproducible audit of point-in-time macro model inputs and stability."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from ..config import load_settings
from .experiment import _dataset
from .validation import (
    ElasticNetModel,
    LeakageSafeTransformer,
    LogisticModel,
    RidgeModel,
    classification_metrics,
    nested_time_cv,
    regression_metrics,
    walk_forward_splits,
)

BLOCK_PREFIXES = {
    "currencies": ("macro__cbr_usd", "macro__cbr_eur", "macro__cbr_cny", "macro__moex_cny"),
    "rates_ruonia": ("macro__cbr_key_rate", "macro__cbr_ruonia"),
    "ofz": ("macro__moex_rgbi", "macro__moex_ofz"),
    "sector_indices": (
        "macro__moex_finance",
        "macro__moex_oil",
        "macro__moex_metals",
        "macro__moex_consumer",
        "macro__moex_transport",
        "macro__moex_power",
    ),
    "ruble_regime": ("macro__cbr_usd_rub_return", "macro__cbr_cny_rub_return", "macro__moex_cny_rub_return"),
    "debt_regime": ("macro__moex_rgbi_return", "macro__moex_ofz_"),
    "event_calendar": ("macro__event_",),
}


def feature_blocks(columns: list[str]) -> dict[str, list[str]]:
    return {
        name: [column for column in columns if column.startswith(prefixes)]
        for name, prefixes in BLOCK_PREFIXES.items()
    }


def common_sample(frame: pd.DataFrame, model_columns: dict[str, list[str]]) -> pd.DataFrame:
    if not model_columns:
        raise ValueError("At least one model is required")
    return frame.dropna(subset=["target"]).copy()


def own_available_sample(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return frame.dropna(subset=["target", *columns]).copy()


def target_alignment_is_valid(frame: pd.DataFrame, horizon: int, calendar: pd.Series | None = None) -> bool:
    dates = pd.to_datetime(frame["trade_date"]).reset_index(drop=True)
    exits = pd.to_datetime(frame["exit_date"]).reset_index(drop=True)
    calendar_index = pd.Index(
        pd.to_datetime(calendar).drop_duplicates().sort_values()
        if calendar is not None
        else pd.concat([dates, exits]).dropna().drop_duplicates().sort_values()
    )
    positions = pd.Series(np.arange(len(calendar_index)), index=calendar_index)
    return all(
        trade_date in positions.index
        and exit_date in positions.index
        and positions[exit_date] - positions[trade_date] == horizon
        for trade_date, exit_date in zip(dates, exits, strict=True)
    )


def matrix_diagnostics(frame: pd.DataFrame, columns: list[str]) -> dict:
    values = frame[columns].apply(pd.to_numeric, errors="coerce")
    missing = values.isna().mean()
    variance = values.var()
    usable = values.loc[:, variance.fillna(0) > 1e-14]
    transformed = (
        LeakageSafeTransformer().fit(usable).transform(usable)
        if not usable.empty
        else np.empty((len(values), 0))
    )
    correlation = usable.corr().abs() if not usable.empty else pd.DataFrame()
    pairs = []
    for i, left in enumerate(correlation.columns):
        for right in correlation.columns[i + 1 :]:
            if correlation.loc[left, right] > 0.90:
                pairs.append([left, right, float(correlation.loc[left, right])])
    med = values.median()
    mad = (values - med).abs().median().replace(0, np.nan)
    outliers = ((values - med).abs() > 8 * 1.4826 * mad).sum()
    first_valid = {column: str(values[column].first_valid_index()) for column in columns}
    return {
        "rows": len(values),
        "features": len(columns),
        "observations_per_feature": len(values) / max(len(columns), 1),
        "missing_share": missing.to_dict(),
        "near_constant": variance[variance.fillna(0) < 1e-10].index.tolist(),
        "low_variance": variance[variance.fillna(0) < 1e-6].index.tolist(),
        "high_correlation_pairs": pairs,
        "condition_number": float(np.linalg.cond(transformed)) if transformed.size else None,
        "outliers": {key: int(value) for key, value in outliers.items() if value},
        "first_valid": first_valid,
    }


def bootstrap_difference(actual, first, second, samples: int = 500) -> dict:
    """Paired technical-minus-candidate loss difference; positive favors candidate."""
    actual, first, second = map(np.asarray, (actual, first, second))
    mae_diff = np.abs(actual - first) - np.abs(actual - second)
    squared_diff = (actual - first) ** 2 - (actual - second) ** 2
    rng = np.random.default_rng(364)
    indices = rng.integers(0, len(actual), (samples, len(actual)))
    draws = mae_diff[indices].mean(axis=1)
    dm_denominator = np.std(squared_diff, ddof=1) / np.sqrt(len(squared_diff))
    dm_stat = float(np.mean(squared_diff) / dm_denominator) if dm_denominator else 0.0
    return {
        "mean_mae_improvement": float(np.mean(mae_diff)),
        "median_mae_improvement": float(np.median(mae_diff)),
        "mae_improvement_ci95": np.quantile(draws, [0.025, 0.975]).tolist(),
        "dm_statistic": dm_stat,
        "dm_pvalue": float(math.erfc(abs(dm_stat) / math.sqrt(2))),
        "extreme_dates_share": float(np.mean(np.abs(mae_diff) >= np.quantile(np.abs(mae_diff), 0.95))),
    }


def permutation_sanity(train, test, columns: list[str]) -> dict:
    model = RidgeModel().fit(train[columns], train["target"])
    normal = regression_metrics(test["target"], model.predict(test[columns]))["rmse"]
    rng = np.random.default_rng(364)
    shuffled = train["target"].to_numpy().copy()
    rng.shuffle(shuffled)
    permuted = RidgeModel().fit(train[columns], shuffled).predict(test[columns])
    noise_train = train[columns].copy()
    noise_test = test[columns].copy()
    noise_train["random_noise"] = rng.normal(size=len(train))
    noise_test["random_noise"] = rng.normal(size=len(test))
    noise = RidgeModel().fit(noise_train, train["target"]).predict(noise_test)
    return {
        "normal_rmse": normal,
        "permuted_label_rmse": regression_metrics(test["target"], permuted)["rmse"],
        "random_noise_rmse": regression_metrics(test["target"], noise)["rmse"],
    }


def detect_future_shift(source_dates: pd.Series, trade_dates: pd.Series) -> None:
    source = pd.to_datetime(source_dates, errors="coerce")
    trade = pd.to_datetime(trade_dates)
    if (source > trade).fillna(False).any():
        raise ValueError("Future macro observation detected")


def coefficient_stability(values) -> dict:
    values = np.asarray(values, dtype=float)
    sign_share = float(max(np.mean(values >= 0), np.mean(values <= 0)))
    return {
        "folds": len(values),
        "median": float(np.median(values)),
        "std": float(np.std(values)),
        "min": float(values.min()),
        "max": float(values.max()),
        "consistent_sign_share": sign_share,
        "unreliable": sign_share < 0.7,
    }


def maximum_forward_fill_age(trade_dates, source_dates) -> int | None:
    age = (pd.to_datetime(trade_dates) - pd.to_datetime(source_dates, errors="coerce")).dt.days
    return int(np.nanmax(age)) if np.isfinite(age).any() else None


def robustness_experiments(train, test, columns: list[str]) -> dict[str, dict]:
    results = {}
    transforms = {
        "standard": LeakageSafeTransformer("standard"),
        "robust": LeakageSafeTransformer("robust"),
        "winsor_standard": LeakageSafeTransformer("standard", winsor=(0.01, 0.99)),
        "rank": LeakageSafeTransformer("rank"),
    }
    for name, transformer in transforms.items():
        model = RidgeModel(transformer=transformer).fit(train[columns], train["target"])
        results[f"transform_{name}"] = regression_metrics(test["target"], model.predict(test[columns]))
    candidates = [
        {"alpha": 0.001, "l1_ratio": 1.0},
        {"alpha": 0.01, "l1_ratio": 1.0},
        {"alpha": 0.001, "l1_ratio": 0.5},
        {"alpha": 0.01, "l1_ratio": 0.5},
    ]
    selected = nested_time_cv(train[columns], train["target"], candidates)
    for name, params in {
        "lasso_fixed": {"alpha": 0.01, "l1_ratio": 1.0},
        "elastic_fixed": {"alpha": 0.01, "l1_ratio": 0.5},
        "elastic_nested": selected,
    }.items():
        model = ElasticNetModel(**params).fit(train[columns], train["target"])
        results[f"regularization_{name}"] = {
            **regression_metrics(test["target"], model.predict(test[columns])),
            "parameters": params,
        }
    for penalty in ("l1", "l2"):
        model = LogisticModel(alpha=0.01, penalty=penalty).fit(train[columns], train["target"] > 0)
        results[f"logistic_{penalty}"] = classification_metrics(
            test["target"] > 0, model.predict_proba(test[columns])[:, 1]
        )
    return results


def _data_audit(con, secid: str, series_id: str) -> dict:
    rows = con.execute(
        """SELECT trade_date,source_dates_json FROM macro_features WHERE canonical_secid=?
        AND horizon=1 ORDER BY trade_date""",
        [secid],
    ).fetchdf()
    source_dates = rows["source_dates_json"].map(json.loads).map(lambda value: value.get(series_id))
    dates = pd.to_datetime(rows["trade_date"])
    source = pd.to_datetime(source_dates, errors="coerce")
    detect_future_shift(source, dates)
    age = (dates - source).dt.days
    observation = con.execute(
        """SELECT observation_date,value,vintage FROM macro_observations
        WHERE series_id=? ORDER BY observation_date""",
        [series_id],
    ).fetchdf()
    values = observation["value"] if not observation.empty else pd.Series(dtype=float)
    median, mad = values.median(), (values - values.median()).abs().median()
    outliers = int(((values - median).abs() > 8 * 1.4826 * mad).sum()) if mad else 0
    changes = values.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    breaks = int((changes.abs() > max(0.5, 8 * changes.abs().median())).sum()) if len(changes) else 0
    revisions = max(0, len(observation) - observation["observation_date"].nunique())
    return {
        "first_available": str(source.dropna().min().date()) if source.notna().any() else None,
        "last_available": str(source.dropna().max().date()) if source.notna().any() else None,
        "filled_percent": float(100 * source.notna().mean()),
        "raw_observations": len(observation),
        "aligned_values": int(source.notna().sum()),
        "median_age_days": float(age.median()) if age.notna().any() else None,
        "max_age_days": int(age.max()) if age.notna().any() else None,
        "max_forward_fill_days": maximum_forward_fill_age(dates, source),
        "missing": int(source.isna().sum()),
        "outliers": outliers,
        "structural_breaks": breaks,
        "revisions": revisions,
        "training_period": [str(dates[source.notna()].min().date()), str(dates[source.notna()].max().date())]
        if source.notna().any()
        else [None, None],
        "stale_warning": bool(age.max() > 10) if age.notna().any() else False,
    }


def _fit_predict(train, test, columns):
    model = RidgeModel().fit(train[columns], train["target"])
    return model, model.predict(test[columns])


def _regime(value: str) -> str:
    if value == "стрессовый режим":
        return "stress"
    if value == "восстановление после стресса":
        return "recovery"
    if "восходящий" in value:
        return "growth"
    if "нисходящий" in value:
        return "decline"
    return "sideways"


def run_audit(con: duckdb.DuckDBPyConnection) -> dict:
    cfg = load_settings()["macro"]
    version, started = "macro-audit-v2", time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    now = datetime.now()
    source_updated = con.execute(
        """SELECT greatest(
        coalesce((SELECT max(loaded_at) FROM macro_observations),timestamp '1970-01-01'),
        coalesce((SELECT max(calculated_at) FROM macro_features),timestamp '1970-01-01'),
        coalesce((SELECT max(calculated_at) FROM forward_returns),timestamp '1970-01-01'))"""
    ).fetchone()[0]
    latest = con.execute(
        """SELECT run_id,finished_at,details_json FROM macro_audit_runs
        WHERE calculation_version=? AND status='success' ORDER BY finished_at DESC LIMIT 1""",
        [version],
    ).fetchone()
    expected = json.loads(latest[2]) if latest else {}
    stored = (
        {
            "data": con.execute(
                "SELECT count(*) FROM macro_data_audit WHERE run_id=?", [latest[0]]
            ).fetchone()[0],
            "matrix": con.execute(
                "SELECT count(*) FROM macro_matrix_audit WHERE run_id=?", [latest[0]]
            ).fetchone()[0],
            "ablation": con.execute(
                "SELECT count(*) FROM macro_ablation_results WHERE run_id=?", [latest[0]]
            ).fetchone()[0],
            "coefficients": con.execute(
                "SELECT count(*) FROM macro_coefficient_audit WHERE run_id=?", [latest[0]]
            ).fetchone()[0],
            "regimes": con.execute(
                "SELECT count(*) FROM macro_regime_audit WHERE run_id=?", [latest[0]]
            ).fetchone()[0],
            "decisions": con.execute(
                "SELECT count(*) FROM macro_feature_audit WHERE run_id=?", [latest[0]]
            ).fetchone()[0],
        }
        if latest
        else {}
    )
    if latest and latest[1] >= source_updated and stored == expected:
        return {
            "run_id": latest[0],
            "duration_seconds": 0.0,
            "mode": "incremental-no-change",
            **expected,
        }
    for table in (
        "macro_data_audit",
        "macro_matrix_audit",
        "macro_ablation_results",
        "macro_coefficient_audit",
        "macro_regime_audit",
        "macro_feature_audit",
    ):
        con.execute(f"DELETE FROM {table} WHERE calculation_version=?", [version])
    series_ids = [
        row[0]
        for row in con.execute(
            "SELECT series_id FROM macro_series WHERE is_point_in_time_safe ORDER BY 1"
        ).fetchall()
    ]
    counts = {"data": 0, "matrix": 0, "ablation": 0, "coefficients": 0, "regimes": 0, "decisions": 0}
    for secid in ("IMOEX", "SBER", "LKOH", "GAZP"):
        for series_id in series_ids:
            metrics = _data_audit(con, secid, series_id)
            con.execute(
                "INSERT INTO macro_data_audit VALUES (?,?,?,?,?,?)",
                [run_id, secid, series_id, json.dumps(metrics), version, now],
            )
            counts["data"] += 1
        for horizon in cfg["horizons"]:
            frame, technical, macro = _dataset(con, secid, horizon)
            exit_dates = con.execute(
                """SELECT condition_date,exit_date FROM forward_returns
                WHERE canonical_secid=? AND horizon=? AND price_return IS NOT NULL ORDER BY condition_date""",
                [secid, horizon],
            ).fetchdf()
            aligned = frame.merge(exit_dates, left_on="trade_date", right_on="condition_date", how="left")
            instrument_calendar = con.execute(
                """SELECT trade_date FROM canonical_daily_prices
                WHERE canonical_secid=? ORDER BY trade_date""",
                [secid],
            ).fetchdf()["trade_date"]
            if not target_alignment_is_valid(
                aligned[["trade_date", "exit_date"]], horizon, instrument_calendar
            ):
                raise ValueError(f"Invalid target horizon: {secid} {horizon}")
            diagnostic = matrix_diagnostics(frame, technical + macro)
            con.execute(
                "INSERT INTO macro_matrix_audit VALUES (?,?,?,?,?,?)",
                [run_id, secid, horizon, json.dumps(diagnostic), version, now],
            )
            counts["matrix"] += 1
            blocks = feature_blocks(macro)
            for name, columns in blocks.items():
                if not columns:
                    con.execute(
                        "INSERT INTO macro_feature_audit VALUES (?,?,?,?,?,?,?,?,?)",
                        [
                            run_id,
                            secid,
                            horizon,
                            name,
                            "исключён из-за point-in-time ограничений",
                            "Нет безопасных признаков блока в текущем PIT feature store",
                            json.dumps({"features": 0}),
                            version,
                            now,
                        ],
                    )
                    counts["decisions"] += 1
            model_columns = {"technical": technical, "macro_all": macro, "combined": technical + macro}
            model_columns.update({name: technical + columns for name, columns in blocks.items() if columns})
            common = common_sample(frame, model_columns)
            holdout_start = pd.Timestamp(cfg["walk_forward"]["untouched_holdout_start"])
            for sample_type in ("common", "own"):
                coefficients: dict[tuple[str, str], list[float]] = {}
                fold_metrics: dict[str, list[dict]] = {name: [] for name in model_columns}
                predictions: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {
                    name: [] for name in model_columns
                }
                base_frame = common if sample_type == "common" else frame
                pre = base_frame[pd.to_datetime(base_frame["trade_date"]) < holdout_start]
                for train_idx, test_idx in walk_forward_splits(
                    len(pre),
                    cfg["walk_forward"]["minimum_train_sessions"],
                    cfg["walk_forward"]["test_sessions"],
                    cfg["walk_forward"]["step_sessions"],
                ):
                    for name, columns in model_columns.items():
                        local = pre if sample_type == "common" else own_available_sample(pre, columns)
                        if len(local) <= test_idx[-1] or not columns:
                            continue
                        train, test = local.iloc[train_idx], local.iloc[test_idx]
                        model, prediction = _fit_predict(train, test, columns)
                        fold_metrics[name].append(regression_metrics(test["target"], prediction))
                        predictions[name].append((test["target"].to_numpy(), prediction))
                        if sample_type == "common":
                            for feature, coefficient in zip(columns, model.coef_[1:], strict=True):
                                coefficients.setdefault((name, feature), []).append(float(coefficient))
                for name, metrics in fold_metrics.items():
                    if not metrics:
                        continue
                    summary = {
                        key: float(np.mean([item[key] for item in metrics]))
                        for key in ("rmse", "mae", "sign_accuracy")
                    }
                    summary["folds"] = len(metrics)
                    summary["rows"] = int(sum(len(item[0]) for item in predictions[name]))
                    con.execute(
                        "INSERT INTO macro_ablation_results VALUES (?,?,?,?,?,?,?,?,?)",
                        [
                            run_id,
                            secid,
                            horizon,
                            name,
                            sample_type,
                            "walk-forward",
                            json.dumps(summary),
                            version,
                            now,
                        ],
                    )
                    counts["ablation"] += 1
                if sample_type == "common" and fold_metrics.get("technical"):
                    technical_rmse = np.array([item["rmse"] for item in fold_metrics["technical"]])
                    for name, metrics in fold_metrics.items():
                        if name == "technical" or not metrics:
                            continue
                        candidate_rmse = np.array([item["rmse"] for item in metrics])
                        length = min(len(technical_rmse), len(candidate_rmse))
                        improvements = technical_rmse[:length] - candidate_rmse[:length]
                        evidence = {
                            "fold_improvement_share": float(np.mean(improvements > 0)),
                            "mean_rmse_improvement": float(np.mean(improvements)),
                            "median_rmse_improvement": float(np.median(improvements)),
                        }
                        if len(improvements) < 3:
                            status = "недостаточно истории"
                        elif np.mean(improvements > 0) >= 0.6 and np.mean(improvements) > 0:
                            status = "условно полезен в отдельных режимах"
                        elif np.mean(improvements) < 0:
                            status = "ухудшает модель"
                        else:
                            status = "нейтрален"
                        con.execute(
                            "INSERT INTO macro_feature_audit VALUES (?,?,?,?,?,?,?,?,?)",
                            [
                                run_id,
                                secid,
                                horizon,
                                name,
                                status,
                                "common-sample walk-forward audit",
                                json.dumps(evidence),
                                version,
                                now,
                            ],
                        )
                        counts["decisions"] += 1
                if sample_type == "common":
                    for (name, feature), values in coefficients.items():
                        metrics = coefficient_stability(values)
                        con.execute(
                            "INSERT INTO macro_coefficient_audit VALUES (?,?,?,?,?,?,?,?)",
                            [run_id, secid, horizon, name, feature, json.dumps(metrics), version, now],
                        )
                        counts["coefficients"] += 1
            if len(common) > 100:
                period_boundaries = {
                    "before_2014": (None, "2014-01-01"),
                    "2014_2021": ("2014-01-01", "2022-01-01"),
                    "2022_2023": ("2022-01-01", "2024-01-01"),
                    "holdout_2024_plus": ("2024-01-01", None),
                }
                common_dates = pd.to_datetime(common["trade_date"])
                for period_name, (period_start, period_end) in period_boundaries.items():
                    start_date = pd.Timestamp(period_start) if period_start else common_dates.min()
                    end_date = (
                        pd.Timestamp(period_end) if period_end else common_dates.max() + pd.Timedelta(1, "D")
                    )
                    period_frame = common[(common_dates >= start_date) & (common_dates < end_date)]
                    prior = common[common_dates < start_date]
                    if len(prior) < 750:
                        prior = period_frame.iloc[: min(750, max(0, len(period_frame) - 1))]
                        period_frame = period_frame.iloc[len(prior) :]
                    if len(prior) >= 250 and len(period_frame):
                        _, technical_period = _fit_predict(prior, period_frame, technical)
                        _, combined_period = _fit_predict(prior, period_frame, technical + macro)
                        metrics = {
                            "train_rows": len(prior),
                            "test_rows": len(period_frame),
                            "technical": regression_metrics(period_frame["target"], technical_period),
                            "combined": regression_metrics(period_frame["target"], combined_period),
                        }
                        con.execute(
                            "INSERT INTO macro_ablation_results VALUES (?,?,?,?,?,?,?,?,?)",
                            [
                                run_id,
                                secid,
                                horizon,
                                "period_stability",
                                "common",
                                period_name,
                                json.dumps(metrics),
                                version,
                                now,
                            ],
                        )
                        counts["ablation"] += 1
                train = common[pd.to_datetime(common["trade_date"]) < holdout_start]
                test = common[pd.to_datetime(common["trade_date"]) >= holdout_start]
                if len(test):
                    _, technical_prediction = _fit_predict(train, test, technical)
                    _, combined_prediction = _fit_predict(train, test, technical + macro)
                    significance = bootstrap_difference(
                        test["target"], technical_prediction, combined_prediction
                    )
                    error_gap = np.abs(test["target"].to_numpy() - combined_prediction) - np.abs(
                        test["target"].to_numpy() - technical_prediction
                    )
                    worst = np.argsort(error_gap)[-10:][::-1]
                    diagnostics = {
                        "train_rows": len(train),
                        "test_rows": len(test),
                        "target_mean": float(test["target"].mean()),
                        "target_std": float(test["target"].std()),
                        "target_quantiles": test["target"].quantile([0.05, 0.5, 0.95]).tolist(),
                        "worst_dates": [
                            {
                                "date": str(test["trade_date"].iloc[index]),
                                "target": float(test["target"].iloc[index]),
                                "technical_error": float(
                                    abs(test["target"].iloc[index] - technical_prediction[index])
                                ),
                                "combined_error": float(
                                    abs(test["target"].iloc[index] - combined_prediction[index])
                                ),
                            }
                            for index in worst
                        ],
                    }
                    sanity = permutation_sanity(train, test, technical + macro)
                    con.execute(
                        "INSERT INTO macro_ablation_results VALUES (?,?,?,?,?,?,?,?,?)",
                        [
                            run_id,
                            secid,
                            horizon,
                            "combined_significance",
                            "common",
                            "untouched-holdout",
                            json.dumps(
                                {
                                    **significance,
                                    **sanity,
                                    **diagnostics,
                                    "technical": regression_metrics(test["target"], technical_prediction),
                                    "combined": regression_metrics(test["target"], combined_prediction),
                                }
                            ),
                            version,
                            now,
                        ],
                    )
                    counts["ablation"] += 1
                    for experiment, metrics in robustness_experiments(train, test, technical + macro).items():
                        con.execute(
                            "INSERT INTO macro_ablation_results VALUES (?,?,?,?,?,?,?,?,?)",
                            [
                                run_id,
                                secid,
                                horizon,
                                experiment,
                                "common",
                                "untouched-holdout",
                                json.dumps(metrics),
                                version,
                                now,
                            ],
                        )
                        counts["ablation"] += 1
                    regimes = con.execute("SELECT trade_date,regime FROM market_regimes").fetchdf()
                    evaluated = test.assign(
                        technical_error=np.abs(test["target"].to_numpy() - technical_prediction),
                        combined_error=np.abs(test["target"].to_numpy() - combined_prediction),
                    ).merge(regimes, on="trade_date", how="left")
                    evaluated["regime"] = evaluated["regime"].fillna("unknown").map(_regime)
                    for regime, group in evaluated.groupby("regime"):
                        metrics = {
                            "rows": len(group),
                            "technical_mae": float(group["technical_error"].mean()),
                            "combined_mae": float(group["combined_error"].mean()),
                            "mae_improvement": float(
                                (group["technical_error"] - group["combined_error"]).mean()
                            ),
                        }
                        con.execute(
                            "INSERT INTO macro_regime_audit VALUES (?,?,?,?,?,?,?)",
                            [run_id, secid, horizon, regime, json.dumps(metrics), version, now],
                        )
                        counts["regimes"] += 1
    duration = time.perf_counter() - started
    con.execute(
        "INSERT INTO macro_audit_runs VALUES (?,?,?,current_timestamp,?,'success',?)",
        [run_id, version, now, duration, json.dumps(counts)],
    )
    return {"run_id": run_id, "duration_seconds": duration, **counts}
