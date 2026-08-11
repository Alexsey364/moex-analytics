"""Expanding-window fusion of market, sector, issuer analog, and event context."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import duckdb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from moex_analytics.conditioned_stock_forecasting.core import SECTOR_MAP

from .schema import ensure_schema

VERSION = "stage76-v1"


def run_market_analog_fusion(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    ensure_schema(con)
    source = con.execute(
        """SELECT run_id FROM analog_trajectory_runs WHERE status='completed'
        ORDER BY finished_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed analog trajectory run is required")
    analog_run = source[0]
    analog = con.execute(
        """SELECT secid,horizon,cutoff,forecast_median_return,actual_return
        FROM analog_oos_replays WHERE run_id=? AND train_only AND horizon IN (5,20,60,120,250)
        ORDER BY secid,horizon,cutoff""",
        [analog_run],
    ).df()
    state_run = con.execute(
        "SELECT run_id FROM whole_market_state_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    market = (
        con.execute(
            "SELECT trade_date,return_20 FROM whole_market_state_daily WHERE run_id=?",
            [state_run],
        )
        .df()
        .rename(columns={"trade_date": "cutoff", "return_20": "market_feature"})
    )
    sector_run = con.execute(
        "SELECT run_id FROM sector_rotation_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    sector = con.execute(
        """SELECT trade_date cutoff,sector,horizon,momentum_score sector_feature
        FROM sector_rotation_scores WHERE run_id=?""",
        [sector_run],
    ).df()
    frames = []
    for secid, group in analog.groupby("secid"):
        feature = sector[sector.sector == SECTOR_MAP.get(secid, "")]
        frames.append(group.merge(feature, on=["cutoff", "horizon"], how="left"))
    data = pd.concat(frames, ignore_index=True).merge(market, on="cutoff", how="left")
    signature = hashlib.sha256(pd.util.hash_pandas_object(data, index=True).values.tobytes()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{analog_run}|{signature}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM market_analog_fusion_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    predictions: list[list[object]] = []
    cards: list[list[object]] = []
    features = ["forecast_median_return", "market_feature", "sector_feature"]
    for (secid, horizon), sample in data.groupby(["secid", "horizon"]):
        sample = sample.sort_values("cutoff").reset_index(drop=True)
        start = max(30, int(len(sample) * 0.6))
        for position in range(start, len(sample), 20):
            train = sample.iloc[:position]
            test = sample.iloc[position : position + 20]
            model = make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=10.0))
            model.fit(train[features], train.actual_return)
            fused_values = model.predict(test[features])
            for offset, (_, row) in enumerate(test.iterrows()):
                fused = float(fused_values[offset])
                actual = float(row.actual_return)
                analog_prediction = float(row.forecast_median_return)
                predictions.append(
                    [
                        run_id,
                        secid,
                        int(horizon),
                        row.cutoff,
                        analog_prediction,
                        fused,
                        actual,
                        abs(actual - analog_prediction),
                        abs(actual - fused),
                        bool(np.sign(fused) == np.sign(actual)),
                        train.cutoff.max(),
                        row.market_feature,
                        row.sector_feature,
                        False,
                    ]
                )
        current = [row for row in predictions if row[1] == secid and row[2] == horizon]
        if current:
            analog_mae = float(np.mean([row[7] for row in current]))
            fused_mae = float(np.mean([row[8] for row in current]))
            accuracy = float(np.mean([row[9] for row in current]))
            improvement = analog_mae - fused_mae
            status = "experimental" if improvement > 0 else "rejected"
            cards.append(
                [
                    run_id,
                    secid,
                    int(horizon),
                    len(current),
                    analog_mae,
                    fused_mae,
                    improvement,
                    accuracy,
                    status,
                ]
            )
    con.executemany(
        """INSERT INTO market_analog_fusion_oos
        (run_id,secid,horizon,cutoff,analog_prediction,fused_prediction,actual_return,
        analog_error,fused_error,direction_correct,train_end,market_feature,sector_feature,
        event_context_available) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        predictions,
    )
    con.executemany(
        """INSERT INTO market_analog_fusion_scorecards
        (run_id,secid,horizon,observations,analog_mae,fused_mae,improvement,
        direction_accuracy,status) VALUES (?,?,?,?,?,?,?,?,?)""",
        cards,
    )
    cutoff = data.cutoff.max()
    con.execute(
        """INSERT INTO market_analog_fusion_runs
        (run_id,created_at,cutoff,source_analog_run,observations,instruments,methodology_version,
        immutable,production_unchanged,status,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            analog_run,
            len(predictions),
            data.secid.nunique(),
            VERSION,
            True,
            True,
            "completed",
            json.dumps({"expanding_train_only": True, "event_context": "informational"}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, object]:
    row = con.execute(
        """SELECT count(*),sum(status='experimental'),sum(status='rejected'),
    avg(improvement),avg(direction_accuracy) FROM market_analog_fusion_scorecards WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "scorecards": row[0],
        "improved": row[1],
        "rejected": row[2],
        "mean_mae_improvement": row[3],
        "direction_accuracy": row[4],
        "status": "completed",
    }
