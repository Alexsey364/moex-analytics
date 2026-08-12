"""Estimate changing macro exposures without hard-coded economic signs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "macro-sensitivity-v1"
FACTORS = (
    "market_return_20",
    "key_rate",
    "ruonia",
    "rgbi",
    "fx_return_20",
    "brent_return_20",
    "market_vol_20",
)


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def estimate_sensitivity(asset: pd.Series, factor: pd.Series) -> tuple[float | None, float]:
    sample = pd.concat([asset.rename("asset"), factor.rename("factor")], axis=1).dropna()
    if len(sample) < 60 or sample.factor.var() == 0:
        return None, 0.0
    chunks = [sample.iloc[index] for index in np.array_split(np.arange(len(sample)), 4)]
    betas = [
        part.asset.cov(part.factor) / part.factor.var()
        for part in chunks
        if len(part) >= 15 and part.factor.var() > 0
    ]
    beta = sample.asset.cov(sample.factor) / sample.factor.var()
    stability = abs(float(np.mean(np.sign(betas)))) if betas else 0.0
    return float(beta), stability


def run_macro_sensitivity(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    feature_run = con.execute(
        "select run_id from predictive_feature_runs where status='completed' "
        "order by finished_at desc limit 1"
    ).fetchone()[0]
    run_id = hashlib.sha256(f"{VERSION}|{feature_run}".encode()).hexdigest()[:20]
    cached = con.execute("select status,rows from macro_sensitivity_runs where run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "rows": cached[1], "cached": True}
    panel = con.execute(
        "select * from predictive_feature_store where run_id=? and secid<>'IMOEX'", [feature_run]
    ).df()
    rows = []
    for secid, group in panel.groupby("secid"):
        group = group.sort_values("trade_date")
        asset = group.price.pct_change()
        for factor in FACTORS:
            values = group[factor].diff() if factor in {"key_rate", "ruonia", "rgbi"} else group[factor]
            beta, stability = estimate_sensitivity(asset, values)
            n = int(pd.concat([asset, values], axis=1).dropna().shape[0])
            status = (
                "STABLE_EXPOSURE"
                if beta is not None and n >= 250 and stability >= 0.75
                else ("UNSTABLE" if beta is not None else "INSUFFICIENT_DATA")
            )
            rows.append(
                [
                    run_id,
                    secid,
                    factor,
                    756,
                    n,
                    beta,
                    stability,
                    None,
                    status,
                    False,
                    json.dumps({"predictive_usefulness": "not_proven", "production_changes": 0}),
                ]
            )
    frame = pd.DataFrame(
        rows,
        columns=(
            "run_id",
            "secid",
            "factor",
            "rolling_window",
            "observations",
            "sensitivity",
            "sign_stability",
            "oos_improvement",
            "status",
            "contribution_allowed",
            "details_json",
        ),
    )
    con.execute(
        "insert or replace into macro_sensitivity_runs values "
        "(?,?,current_timestamp,current_timestamp,'completed',?,?)",
        [run_id, VERSION, len(frame), json.dumps({"production_changes": 0})],
    )
    con.register("_m", frame)
    cols = ",".join(frame.columns)
    con.execute(f"insert into macro_sensitivity_results ({cols}) select {cols} from _m")
    con.unregister("_m")
    return {"run_id": run_id, "status": "completed", "rows": len(frame), "cached": False}
