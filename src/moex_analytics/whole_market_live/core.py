"""Immutable live evidence streams for market, sector ranks, and stock ranks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb

from .schema import ensure_schema

VERSION = "stage78-v2"
MARKET_HORIZONS = (1, 5, 20, 60, 120)
RANK_HORIZONS = (5, 20, 60, 120, 250)


def _id(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]


def create_live_forecasts(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    state_run = con.execute(
        "SELECT run_id FROM whole_market_state_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    state = con.execute(
        """SELECT trade_date,imoex_close,market_state_label,return_20,realized_vol20
        FROM whole_market_state_daily WHERE run_id=? ORDER BY trade_date""",
        [state_run],
    ).df()
    cutoff = state.trade_date.max()
    run_id = _id(VERSION, cutoff, state_run)
    if con.execute("SELECT 1 FROM whole_market_live_runs WHERE run_id=?", [run_id]).fetchone():
        return live_forecast_status(con, run_id) | {"idempotent": True}
    current = state.iloc[-1]
    same_state = state[state.market_state_label == current.market_state_label]
    market_rows = []
    now = datetime.now(UTC)
    for horizon in MARKET_HORIZONS:
        outcomes = same_state.imoex_close.shift(-horizon) / same_state.imoex_close - 1
        clean = outcomes.dropna()
        median = float(clean.median()) if len(clean) >= 30 else None
        downside = float(clean.quantile(0.1)) if len(clean) >= 30 else None
        upside = float(clean.quantile(0.9)) if len(clean) >= 30 else None
        state_name = (
            "up" if median and median > 0.005 else "down" if median and median < -0.005 else "neutral"
        )
        forecast_id = _id("market", cutoff, horizon, VERSION)
        digest = _id(state_run, current.market_state_label, len(clean))
        market_rows.append(
            [
                forecast_id,
                run_id,
                now,
                cutoff,
                "IMOEX",
                horizon,
                state_name,
                median,
                downside,
                upside,
                current.market_state_label,
                "state-conditioned-baseline-v1",
                True,
                False,
                "pending",
                digest,
            ]
        )
    series = (
        con.execute(
            """SELECT observation_date,series_id,value FROM macro_observations
        WHERE series_id LIKE 'moex_%' AND series_id IN
        ('moex_chemicals','moex_consumer','moex_finance','moex_metals','moex_oil_gas',
         'moex_power','moex_telecom','moex_transport')
        QUALIFY row_number() OVER(PARTITION BY observation_date,series_id ORDER BY available_from)=1"""
        )
        .df()
        .pivot(index="observation_date", columns="series_id", values="value")
        .sort_index()
    )
    sector_cutoff = series.index.max()
    sector_rows = []
    for horizon in RANK_HORIZONS:
        momentum = (
            sum(series.pct_change(window, fill_method=None).iloc[-1].rank(pct=True) for window in (5, 20, 60))
            / 3
        )
        momentum = momentum.dropna()
        ranks = momentum.rank(ascending=False, method="min")
        for sector in series.columns:
            available = sector in momentum.index
            sector_rows.append(
                [
                    _id("sector", sector_cutoff, sector, horizon, VERSION),
                    run_id,
                    now,
                    sector_cutoff,
                    sector,
                    horizon,
                    int(ranks[sector]) if available else None,
                    float(momentum[sector]) if available else None,
                    "stage73-momentum-rank-v1",
                    True,
                    "pending" if available else "insufficient_data",
                    _id(series.iloc[-60:].to_json(), sector, horizon),
                ]
            )
    fusion_run = con.execute(
        "SELECT run_id FROM predictive_fusion_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    fusion = con.execute(
        """SELECT secid,horizon,cutoff,signal,predicted_return,status FROM current_fusion_research
        WHERE run_id=? AND horizon IN (5,20,60,120,250)""",
        [fusion_run],
    ).df()
    stock_rows = []
    for horizon, group in fusion.groupby("horizon"):
        ranks = group.predicted_return.rank(ascending=False, method="min", na_option="bottom")
        for position, row in group.iterrows():
            stock_rows.append(
                [
                    _id("stock", row.cutoff, row.secid, horizon, VERSION),
                    run_id,
                    now,
                    row.cutoff,
                    row.secid,
                    int(horizon),
                    int(ranks[position]),
                    row.signal,
                    row.predicted_return,
                    "stage47-frozen-fusion-shadow",
                    True,
                    False,
                    "pending",
                    _id(fusion_run, row.secid, horizon, row.predicted_return),
                ]
            )
    con.executemany(
        """INSERT INTO live_market_forecasts
    (forecast_id,run_id,created_at,cutoff,instrument,horizon,qualitative_state,median_return,
    downside_range,upside_range,regime,model_version,immutable,probability_allowed,status,input_hash)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        market_rows,
    )
    con.executemany(
        """INSERT INTO live_sector_rank_forecasts
    (forecast_id,run_id,created_at,cutoff,sector,horizon,predicted_rank,score,model_version,
    immutable,status,input_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        sector_rows,
    )
    con.executemany(
        """INSERT INTO live_stock_rank_forecasts
    (forecast_id,run_id,created_at,cutoff,secid,horizon,predicted_rank,qualitative_state,
    predicted_return,model_version,immutable,probability_allowed,status,input_hash)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        stock_rows,
    )
    con.execute(
        """INSERT INTO whole_market_live_runs
    (run_id,created_at,cutoff,market_rows,sector_rows,stock_rows,methodology_version,immutable,
    probability_allowed,status,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            now,
            cutoff,
            len(market_rows),
            len(sector_rows),
            len(stock_rows),
            VERSION,
            True,
            False,
            "completed",
            json.dumps({"created_before_outcome": True, "production_changes": 0}),
        ],
    )
    return live_forecast_status(con, run_id) | {"idempotent": False}


def evaluate_live_forecasts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Mature only pre-existing forecasts after a real later EOD row exists."""
    ensure_schema(con)
    created = 0
    for table, instrument_column in (
        ("live_market_forecasts", "instrument"),
        ("live_stock_rank_forecasts", "secid"),
    ):
        value_column = "median_return" if table == "live_market_forecasts" else "predicted_return"
        forecasts = con.execute(f"""SELECT forecast_id,cutoff,{instrument_column},horizon,
        {value_column},qualitative_state FROM {table} WHERE status='pending'""").fetchall()
        for forecast_id, cutoff, instrument, horizon, predicted, direction in forecasts:
            prices = con.execute(
                """SELECT trade_date,close FROM canonical_daily_prices
            WHERE canonical_secid=? AND trade_date>=? ORDER BY trade_date LIMIT ?""",
                [instrument, cutoff, horizon + 1],
            ).fetchall()
            if len(prices) <= horizon or prices[0][0] != cutoff:
                continue
            actual = prices[horizon][1] / prices[0][1] - 1
            correct = (
                (direction == "up" and actual > 0)
                or (direction == "down" and actual < 0)
                or (direction == "neutral" and abs(actual) <= 0.005)
            )
            if con.execute(
                "SELECT 1 FROM whole_market_live_outcomes WHERE forecast_id=?", [forecast_id]
            ).fetchone():
                continue
            con.execute(
                """INSERT INTO whole_market_live_outcomes
            (forecast_id,matured_at,actual_return,direction_correct,absolute_error,evaluated_at,status,immutable)
            VALUES (?,?,?,?,?,?,'matured',TRUE) ON CONFLICT DO NOTHING""",
                [
                    forecast_id,
                    prices[horizon][0],
                    actual,
                    correct,
                    abs(actual - predicted) if predicted is not None else None,
                    datetime.now(UTC),
                ],
            )
            con.execute(f"UPDATE {table} SET status='matured' WHERE forecast_id=?", [forecast_id])
            created += 1
    return {"newly_matured": created}


def live_forecast_status(con: duckdb.DuckDBPyConnection, run_id: str | None = None) -> dict[str, Any]:
    ensure_schema(con)
    if run_id is None:
        row = con.execute(
            "SELECT run_id FROM whole_market_live_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"status": "not_run"}
        run_id = row[0]
    counts = []
    for table in ("live_market_forecasts", "live_sector_rank_forecasts", "live_stock_rank_forecasts"):
        counts.append(con.execute(f"SELECT count(*) FROM {table} WHERE run_id=?", [run_id]).fetchone()[0])
    pending = con.execute(
        """SELECT count(*) FROM
    (SELECT forecast_id FROM live_market_forecasts WHERE run_id=? AND status='pending' UNION ALL
     SELECT forecast_id FROM live_stock_rank_forecasts WHERE run_id=? AND status='pending')""",
        [run_id, run_id],
    ).fetchone()[0]
    return {
        "run_id": run_id,
        "market": counts[0],
        "sectors": counts[1],
        "stocks": counts[2],
        "pending": pending,
        "probability_allowed": False,
        "status": "completed",
    }
