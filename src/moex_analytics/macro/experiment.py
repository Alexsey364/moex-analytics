"""Walk-forward comparison of technical, macro and combined linear models."""

from __future__ import annotations

import json
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from ..config import load_settings
from .validation import (
    LogisticModel,
    RidgeModel,
    classification_metrics,
    empirical_intervals,
    price_interval,
    regression_metrics,
    walk_forward_splits,
)

TECHNICAL_COLUMNS = [
    "return_20",
    "return_60",
    "distance_sma_50",
    "distance_sma_200",
    "volatility_20",
    "current_drawdown",
    "relative_strength_60",
]


def _evaluate(train: pd.DataFrame, test: pd.DataFrame, columns: list[str]) -> dict:
    prediction = RidgeModel().fit(train[columns], train["target"]).predict(test[columns])
    metrics = regression_metrics(test["target"], prediction)
    probability = LogisticModel().fit(train[columns], train["target"] > 0).predict_proba(test[columns])[:, 1]
    direction = classification_metrics(test["target"] > 0, probability)
    metrics.update({f"direction_{key}": value for key, value in direction.items()})
    return metrics


def _baseline_metrics(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    prediction = np.full(len(test), train["target"].mean())
    metrics = regression_metrics(test["target"], prediction)
    probability = np.full(len(test), np.mean(train["target"] > 0))
    direction = classification_metrics(test["target"] > 0, probability)
    metrics.update({f"direction_{key}": value for key, value in direction.items()})
    return metrics


def _dataset(
    con: duckdb.DuckDBPyConnection, secid: str, horizon: int
) -> tuple[pd.DataFrame, list[str], list[str]]:
    version = load_settings()["macro"]["calculation_version"]
    raw = con.execute(
        """SELECT m.trade_date,m.features_json,f.features_json technical_json,
        r.price_return target_return FROM macro_features m
        JOIN daily_features f ON m.trade_date=f.trade_date AND m.canonical_secid=f.canonical_secid
        JOIN forward_returns r ON m.trade_date=r.condition_date AND m.canonical_secid=r.canonical_secid
        WHERE m.canonical_secid=? AND m.horizon=? AND m.calculation_version=?
          AND r.horizon=? AND r.price_return IS NOT NULL ORDER BY m.trade_date""",
        [secid, horizon, version, horizon],
    ).fetchdf()
    if raw.empty:
        return raw, [], []
    macro = pd.json_normalize(raw["features_json"].map(json.loads)).apply(pd.to_numeric, errors="coerce")
    technical = pd.json_normalize(raw["technical_json"].map(json.loads)).apply(pd.to_numeric, errors="coerce")
    technical = technical.reindex(columns=[c for c in TECHNICAL_COLUMNS if c in technical])
    macro.columns = [f"macro__{c}" for c in macro.columns]
    frame = pd.concat([raw[["trade_date", "target_return"]], technical, macro], axis=1)
    frame = frame.rename(columns={"target_return": "target"})
    frame = frame.replace([np.inf, -np.inf], np.nan)
    macro_columns = list(macro.columns)
    return frame, list(technical.columns), macro_columns


def validate_all(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["macro"]
    version = cfg["model_version"]
    con.execute("DELETE FROM macro_model_results WHERE calculation_version=?", [version])
    total = 0
    for secid in ("IMOEX", "SBER", "LKOH", "GAZP"):
        for horizon in cfg["horizons"]:
            frame, technical, macro = _dataset(con, secid, horizon)
            pre_holdout = frame[
                pd.to_datetime(frame["trade_date"])
                < pd.Timestamp(cfg["walk_forward"]["untouched_holdout_start"])
            ]
            if len(pre_holdout) < cfg["walk_forward"]["minimum_train_sessions"]:
                continue
            models = {"technical": technical, "macro": macro, "combined": technical + macro}
            for fold, (train_idx, test_idx) in enumerate(
                walk_forward_splits(
                    len(pre_holdout),
                    cfg["walk_forward"]["minimum_train_sessions"],
                    cfg["walk_forward"]["test_sessions"],
                    cfg["walk_forward"]["step_sessions"],
                ),
                1,
            ):
                train, test = pre_holdout.iloc[train_idx], pre_holdout.iloc[test_idx]
                records = {"unconditional_mean": _baseline_metrics(train, test)}
                for name, columns in models.items():
                    if not columns:
                        continue
                    records[name] = _evaluate(train, test, columns)
                for name, metrics in records.items():
                    con.execute(
                        "INSERT INTO macro_model_results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            secid,
                            horizon,
                            name,
                            fold,
                            "walk-forward",
                            train["trade_date"].iloc[-1],
                            test["trade_date"].iloc[0],
                            test["trade_date"].iloc[-1],
                            json.dumps(metrics),
                            version,
                            datetime.now(),
                        ],
                    )
                    total += 1
            holdout = frame[
                pd.to_datetime(frame["trade_date"])
                >= pd.Timestamp(cfg["walk_forward"]["untouched_holdout_start"])
            ]
            train = pre_holdout
            if not holdout.empty:
                records = {"unconditional_mean": _baseline_metrics(train, holdout)}
                for name, columns in models.items():
                    if columns:
                        records[name] = _evaluate(train, holdout, columns)
                for name, metrics in records.items():
                    con.execute(
                        "INSERT INTO macro_model_results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            secid,
                            horizon,
                            name,
                            0,
                            "untouched-holdout",
                            train["trade_date"].iloc[-1],
                            holdout["trade_date"].iloc[0],
                            holdout["trade_date"].iloc[-1],
                            json.dumps(metrics),
                            version,
                            datetime.now(),
                        ],
                    )
                    total += 1
    return total


def calculate_forecasts(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["macro"]
    version = cfg["model_version"]
    con.execute("DELETE FROM forecast_ranges WHERE calculation_version=?", [version])
    con.execute("DELETE FROM experimental_scores WHERE calculation_version=?", [version])
    total = 0
    for secid in ("IMOEX", "SBER", "LKOH", "GAZP"):
        macro_scores = []
        for horizon in cfg["horizons"]:
            frame, technical, macro = _dataset(con, secid, horizon)
            columns = technical + macro
            if len(frame) < 100 or not columns:
                continue
            latest = con.execute(
                """SELECT m.features_json,f.features_json FROM macro_features m
                JOIN daily_features f ON m.trade_date=f.trade_date
                  AND m.canonical_secid=f.canonical_secid
                WHERE m.canonical_secid=? AND m.horizon=?
                ORDER BY m.trade_date DESC LIMIT 1""",
                [secid, horizon],
            ).fetchone()
            current_technical = pd.json_normalize([json.loads(latest[1])])
            current_macro = pd.json_normalize([json.loads(latest[0])])
            current_macro.columns = [f"macro__{column}" for column in current_macro.columns]
            current = pd.concat([current_technical, current_macro], axis=1).reindex(columns=columns)
            model = RidgeModel().fit(frame[columns], frame["target"])
            fitted = model.predict(frame[columns])
            prediction = float(model.predict(current)[0])
            residuals = frame["target"].to_numpy() - fitted
            intervals = empirical_intervals(prediction, residuals)
            holdout = con.execute(
                """SELECT model_type,metrics_json FROM macro_model_results
                WHERE canonical_secid=? AND horizon=? AND period='untouched-holdout'
                  AND calculation_version=?""",
                [secid, horizon, version],
            ).fetchall()
            metrics = {name: json.loads(payload) for name, payload in holdout}
            proven = metrics.get("combined", {}).get("rmse", np.inf) < metrics.get("technical", {}).get(
                "rmse", -np.inf
            )
            if not proven:
                prediction = float(frame["target"].mean())
                intervals = empirical_intervals(prediction, frame["target"] - prediction)
            price_row = con.execute(
                """SELECT trade_date,close FROM canonical_daily_prices
                WHERE canonical_secid=? ORDER BY trade_date DESC LIMIT 1""",
                [secid],
            ).fetchone()
            price = float(price_row[1])
            prices = {}
            for level in (50, 80, 90):
                prices[level] = price_interval(
                    price, intervals[f"lower_{level}"], intervals[f"upper_{level}"]
                )
            positive = float(np.mean(frame["target"] > 0))
            con.execute(
                "INSERT INTO forecast_ranges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    price_row[0],
                    secid,
                    horizon,
                    price,
                    intervals["median"],
                    intervals["lower_50"],
                    intervals["upper_50"],
                    intervals["lower_80"],
                    intervals["upper_80"],
                    intervals["lower_90"],
                    intervals["upper_90"],
                    positive,
                    prices[50][0],
                    prices[50][1],
                    prices[80][0],
                    prices[80][1],
                    prices[90][0],
                    prices[90][1],
                    "подтверждена" if proven else "не подтверждена",
                    "combined ridge" if proven else "historical distribution",
                    datetime.now().astimezone(),
                    version,
                    datetime.now(),
                ],
            )
            macro_scores.append(prediction)
            total += 1
        if macro_scores:
            score = float(np.clip(np.mean(macro_scores) / 0.1, -1, 1))
            proven_any = (
                con.execute(
                    """SELECT count(*) FROM forecast_ranges WHERE canonical_secid=?
                AND calculation_version=? AND model_quality='подтверждена'""",
                    [secid, version],
                ).fetchone()[0]
                > 0
            )
            con.execute(
                "INSERT INTO experimental_scores VALUES (?,?,?,?,?,?,?,?)",
                [
                    price_row[0],
                    secid,
                    score,
                    None,
                    proven_any,
                    json.dumps({"message": "Experimental; technical-v1 remains unchanged"}),
                    version,
                    datetime.now(),
                ],
            )
    return total
