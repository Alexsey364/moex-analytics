"""Stage 74 market- and sector-conditioned portfolio-stock ablation."""

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

from .schema import ensure_schema

VERSION = "stage74-v1"
SECIDS = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
HORIZONS = (5, 20, 60, 120, 250)
SECTOR_MAP = {
    "SBERP": "moex_finance",
    "MOEX": "moex_finance",
    "LKOH": "moex_oil_gas",
    "TRNFP": "moex_oil_gas",
    "TATNP": "moex_oil_gas",
    "PHOR": "moex_chemicals",
    "MTSS": "moex_telecom",
    "LSNGP": "moex_power",
    "X5": "moex_consumer",
}


def _prices(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    marks = ",".join("?" for _ in SECIDS)
    raw = con.execute(
        f"""SELECT trade_date,canonical_secid,close FROM canonical_daily_prices
        WHERE canonical_secid IN ({marks}) AND close>0 ORDER BY trade_date""",
        list(SECIDS),
    ).df()
    return raw.pivot(index="trade_date", columns="canonical_secid", values="close").sort_index()


def _context(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    state_run = con.execute(
        "SELECT run_id FROM whole_market_state_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    market = (
        con.execute(
            """SELECT trade_date,return_5 market_5,return_20 market_20,return_60 market_60,
        realized_vol20 market_vol,drawdown market_drawdown FROM whole_market_state_daily
        WHERE run_id=? ORDER BY trade_date""",
            [state_run],
        )
        .df()
        .set_index("trade_date")
    )
    sector_names = tuple(set(SECTOR_MAP.values()))
    marks = ",".join("?" for _ in sector_names)
    sector = (
        con.execute(
            f"""SELECT observation_date,series_id,value FROM macro_observations
        WHERE series_id IN ({marks}) QUALIFY row_number() OVER
        (PARTITION BY observation_date,series_id ORDER BY available_from)=1""",
            list(sector_names),
        )
        .df()
        .pivot(index="observation_date", columns="series_id", values="value")
        .sort_index()
    )
    return market.join(sector, how="outer").sort_index()


def run_conditioned_stock_research(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    ensure_schema(con)
    prices = _prices(con)
    context = _context(con)
    signature = hashlib.sha256(pd.util.hash_pandas_object(prices, index=True).values.tobytes()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{signature}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM conditioned_stock_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    cards: list[list[object]] = []
    dates: list[pd.Timestamp] = []
    for secid in SECIDS:
        if secid not in prices:
            continue
        close = prices[secid].dropna()
        data = pd.DataFrame(index=close.index)
        for window in (1, 5, 20, 60, 120):
            data[f"issuer_{window}"] = close.pct_change(window)
        data["issuer_vol20"] = close.pct_change().rolling(20).std() * np.sqrt(252)
        data = data.join(context, how="left")
        sector = SECTOR_MAP[secid]
        data["sector_5"] = data[sector].pct_change(5, fill_method=None)
        data["sector_20"] = data[sector].pct_change(20, fill_method=None)
        dates.extend(data.index)
        issuer = [name for name in data if name.startswith("issuer_")]
        market = [name for name in data if name.startswith("market_")]
        sector_features = ["sector_5", "sector_20"]
        blocks = {
            "issuer_only": issuer,
            "market_only": market,
            "sector_only": sector_features,
            "issuer_market": issuer + market,
            "issuer_sector": issuer + sector_features,
            "full_conditioned": issuer + market + sector_features,
        }
        for horizon in HORIZONS:
            sample = data.copy()
            sample["target"] = close.shift(-horizon) / close - 1
            sample = sample.dropna(subset=["target"])
            split = int(len(sample) * 0.8)
            train, holdout = sample.iloc[:split], sample.iloc[split:]
            baseline = float(train.target.median())
            baseline_mae = float(np.mean(np.abs(holdout.target - baseline)))
            for block, features in blocks.items():
                model = make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=10.0))
                model.fit(train[features], train.target)
                prediction = model.predict(holdout[features])
                mae = float(np.mean(np.abs(holdout.target - prediction)))
                corr = float(pd.Series(prediction).corr(pd.Series(holdout.target.to_numpy())))
                improvement = baseline_mae - mae
                status = "experimental" if improvement > 0 and corr > 0.02 else "weak_or_rejected"
                cards.append(
                    [
                        run_id,
                        secid,
                        horizon,
                        block,
                        len(holdout),
                        baseline_mae,
                        mae,
                        improvement,
                        corr,
                        status,
                        json.dumps({"frozen_holdout": True, "stage52_comparison": "same baseline semantics"}),
                    ]
                )
    con.executemany(
        """INSERT INTO conditioned_stock_scorecards
        (run_id,secid,horizon,feature_block,observations,baseline_mae,model_mae,improvement,
        return_correlation,status,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        cards,
    )
    unique_dates = sorted(set(dates))
    holdout_from = unique_dates[int(len(unique_dates) * 0.8)]
    con.execute(
        """INSERT INTO conditioned_stock_runs
        (run_id,created_at,date_from,date_to,holdout_from,instruments,methodology_version,
        production_unchanged,immutable,status,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            unique_dates[0],
            unique_dates[-1],
            holdout_from,
            len(set(c[1] for c in cards)),
            VERSION,
            True,
            True,
            "completed",
            json.dumps({"ablation_blocks": 6, "no_auto_promotion": True}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, object]:
    row = con.execute(
        """SELECT count(*),count(DISTINCT secid),avg(improvement),max(improvement)
    FROM conditioned_stock_scorecards WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "scorecards": row[0],
        "instruments": row[1],
        "mean_improvement": row[2],
        "best_improvement": row[3],
        "status": "completed",
        "production_unchanged": True,
    }
