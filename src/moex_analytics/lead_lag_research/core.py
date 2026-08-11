"""Temporal association tests; deliberately makes no causality claims."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import duckdb
import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_regression
from sklearn.linear_model import Ridge

from .schema import ensure_schema

VERSION = "stage75-v1"
SECIDS = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
SIGNALS = (
    "cbr_usd_rub",
    "cbr_cny_rub",
    "fred_brent",
    "moex_rgbi",
    "moex_rvi",
    "moex_finance",
    "moex_oil_gas",
    "moex_consumer",
    "moex_power",
    "moex_telecom",
)
LAGS = (1, 5, 20)


def run_lead_lag_research(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    ensure_schema(con)
    marks = ",".join("?" for _ in SECIDS)
    equities = (
        con.execute(
            f"""SELECT trade_date,canonical_secid,close FROM canonical_daily_prices
            WHERE canonical_secid IN ({marks})""",
            list(SECIDS),
        )
        .df()
        .pivot(index="trade_date", columns="canonical_secid", values="close")
        .sort_index()
    )
    marks = ",".join("?" for _ in SIGNALS)
    macro = (
        con.execute(
            f"""SELECT observation_date,series_id,value FROM macro_observations WHERE series_id IN ({marks})
        QUALIFY row_number() OVER(PARTITION BY observation_date,series_id ORDER BY available_from)=1""",
            list(SIGNALS),
        )
        .df()
        .pivot(index="observation_date", columns="series_id", values="value")
        .sort_index()
    )
    data = equities.join(macro, how="inner")
    signature = hashlib.sha256(pd.util.hash_pandas_object(data, index=True).values.tobytes()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{signature}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM lead_lag_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    rows: list[list[object]] = []
    for secid in SECIDS:
        target = data[secid].pct_change(5, fill_method=None).shift(-5)
        for signal in SIGNALS:
            source_return = data[signal].pct_change(fill_method=None)
            for lag in LAGS:
                sample = pd.DataFrame({"x": source_return.shift(lag), "y": target}).dropna()
                if len(sample) < 200:
                    continue
                split = int(len(sample) * 0.8)
                train, holdout = sample.iloc[:split], sample.iloc[split:]
                train_corr = float(train.x.corr(train.y))
                holdout_corr = float(holdout.x.corr(holdout.y))
                mi = float(mutual_info_regression(train[["x"]], train.y, random_state=75)[0])
                model = Ridge(alpha=10).fit(train[["x"]], train.y)
                coefficient = float(model.coef_[0])
                stable = np.sign(train_corr) == np.sign(holdout_corr)
                strength = abs(holdout_corr)
                status = "useful_association" if stable and strength >= 0.05 else "weak"
                if not stable or strength < 0.02:
                    status = "no_evidence"
                rows.append(
                    [
                        run_id,
                        secid,
                        signal,
                        lag,
                        5,
                        train_corr,
                        holdout_corr,
                        mi,
                        coefficient,
                        len(holdout),
                        status,
                    ]
                )
    con.executemany(
        """INSERT INTO lead_lag_scorecards
        (run_id,secid,signal,lag,horizon,train_correlation,holdout_correlation,mutual_information,
        coefficient,observations,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    con.execute(
        """INSERT INTO lead_lag_runs
        (run_id,created_at,date_from,date_to,instruments,signals,methodology_version,causality_claimed,
        immutable,status,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            data.index.min(),
            data.index.max(),
            len(SECIDS),
            len(SIGNALS),
            VERSION,
            False,
            True,
            "completed",
            json.dumps({"alignment": "signal shifted before target", "claim": "association only"}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, object]:
    row = con.execute(
        """SELECT count(*),sum(status='useful_association'),sum(status='weak'),
    sum(status='no_evidence'),max(abs(holdout_correlation)) FROM lead_lag_scorecards WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "tests": row[0],
        "useful": row[1],
        "weak": row[2],
        "no_evidence": row[3],
        "max_abs_holdout_correlation": row[4],
        "causality_claimed": False,
        "status": "completed",
    }
