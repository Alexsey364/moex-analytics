from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "dynamic-ensemble-v1-transparent"


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def component_weight(status: str) -> float:
    return {"VALIDATED": 1.0, "WEAK": 0.1}.get(status, 0.0)


def run_dynamic_ensemble(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = con.execute("select max(trade_date) from daily_returns").fetchone()[0]
    stats = con.execute(
        "select run_id from statistical_model_runs where status='completed' order by finished_at desc limit 1"
    ).fetchone()[0]
    baseline = con.execute(
        "select run_id from predictive_baseline_runs where status='completed' "
        "order by finished_at desc limit 1"
    ).fetchone()[0]
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}|{stats}|{baseline}".encode()).hexdigest()[:20]
    cached = con.execute("select status,rows from dynamic_ensemble_runs where run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "rows": cached[1], "cached": True}
    tickers = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
    components = []
    forecasts = []
    for secid in tickers:
        for horizon in (5, 20, 60, 120, 250):
            champion = con.execute(
                "select model,mae from predictive_baseline_scorecards "
                "where run_id=? and secid=? and horizon=? and rank=1",
                [baseline, secid, horizon],
            ).fetchone()
            current_base = (
                con.execute(
                    "select prediction from predictive_baseline_predictions "
                    "where run_id=? and secid=? and horizon=? and model=? "
                    "order by evaluation_date desc limit 1",
                    [baseline, secid, horizon, champion[0]],
                ).fetchone()
                if champion
                else None
            )
            values = [
                (
                    "baseline",
                    float(current_base[0]) if current_base else 0.0,
                    "VALIDATED",
                    1.0,
                    True,
                    "baseline champion",
                )
            ]
            model = con.execute(
                "select model_id,model,status,improvement from statistical_model_scorecards "
                "where run_id=? and secid=? and horizon=? order by improvement desc limit 1",
                [stats, secid, horizon],
            ).fetchone()
            if model:
                prediction = con.execute(
                    "select prediction from statistical_model_predictions "
                    "where run_id=? and model_id=? order by trade_date desc limit 1",
                    [stats, model[0]],
                ).fetchone()
                weight = component_weight(model[2])
                values.append(
                    (
                        f"statistical:{model[1]}",
                        float(prediction[0]),
                        model[2],
                        weight,
                        weight > 0,
                        f"OOS status {model[2]}",
                    )
                )
            values.extend(
                [
                    (
                        "fundamental",
                        0.0,
                        "INSUFFICIENT_DATA",
                        0.0,
                        False,
                        "unit-safe expected return unavailable",
                    ),
                    ("macro", 0.0, "UNPROVEN", 0.0, False, "OOS usefulness not proven"),
                    ("analog", 0.0, "CONTEXT_ONLY", 0.0, False, "stress/path context only"),
                ]
            )
            total = sum(v[3] for v in values)
            expected = sum(v[1] * v[3] for v in values) / total
            active = [v[1] for v in values if v[3] > 0]
            disagreement = float(np.std(active)) if len(active) > 1 else 0.0
            validated = sum(v[2] == "VALIDATED" and v[0] != "baseline" for v in values)
            status = (
                "ENSEMBLE_VALIDATED" if validated else ("NO_PROVEN_FORECAST_EDGE" if total == 1 else "WEAK")
            )
            confidence = min(0.8, 0.3 + 0.15 * validated - 0.5 * disagreement)
            for component, prediction, reliability, weight, included, reason in values:
                components.append(
                    [
                        run_id,
                        secid,
                        horizon,
                        component,
                        prediction,
                        reliability,
                        weight / total,
                        included,
                        reason,
                    ]
                )
            forecasts.append(
                [
                    run_id,
                    cutoff,
                    secid,
                    horizon,
                    expected,
                    expected,
                    None,
                    None,
                    None,
                    None,
                    False,
                    disagreement,
                    confidence,
                    status,
                    champion[0] if champion else "no_change",
                    json.dumps({"probability_gate": "closed", "production_changes": 0}),
                ]
            )
    cframe = pd.DataFrame(
        components,
        columns=(
            "run_id",
            "secid",
            "horizon",
            "component",
            "prediction",
            "reliability",
            "weight",
            "included",
            "reason",
        ),
    )
    fframe = pd.DataFrame(
        forecasts,
        columns=(
            "run_id",
            "cutoff",
            "secid",
            "horizon",
            "expected_return",
            "median_return",
            "lower_range",
            "upper_range",
            "expected_drawdown",
            "probability_up",
            "probability_allowed",
            "disagreement",
            "confidence",
            "status",
            "best_model",
            "details_json",
        ),
    )
    con.execute(
        "insert or replace into dynamic_ensemble_runs "
        "values (?,?,?,current_timestamp,current_timestamp,'completed',?,?)",
        [run_id, VERSION, cutoff, len(fframe), json.dumps({"production_changes": 0})],
    )
    for table, frame in (("dynamic_ensemble_components", cframe), ("dynamic_ensemble_forecasts", fframe)):
        con.register("_e", frame)
        cols = ",".join(frame.columns)
        con.execute(f"insert into {table} ({cols}) select {cols} from _e")
        con.unregister("_e")
    return {"run_id": run_id, "status": "completed", "rows": len(fframe), "cached": False}
