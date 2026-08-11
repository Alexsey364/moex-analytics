"""Cross-sectional sector ranking with a frozen chronological holdout."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import duckdb
import numpy as np
import pandas as pd

from .schema import ensure_schema

VERSION = "stage73-v1"
HORIZONS = (5, 20, 60, 120, 250)
SECTORS = (
    "moex_chemicals",
    "moex_consumer",
    "moex_finance",
    "moex_metals",
    "moex_oil_gas",
    "moex_power",
    "moex_telecom",
    "moex_transport",
)


def _load(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    names = (*SECTORS, "moex_imoex")
    placeholders = ",".join("?" for _ in names)
    raw = con.execute(
        f"""SELECT series_id,observation_date,value,available_from FROM macro_observations
        WHERE series_id IN ({placeholders}) AND value IS NOT NULL
        QUALIFY row_number() OVER(PARTITION BY series_id,observation_date ORDER BY available_from)=1
        ORDER BY observation_date""",
        list(names),
    ).df()
    # Same-day index closes are usable only after that session; targets begin on the next row.
    return raw.pivot(index="observation_date", columns="series_id", values="value").sort_index()


def run_sector_rotation_research(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    ensure_schema(con)
    prices = _load(con)
    available = [name for name in SECTORS if name in prices and prices[name].notna().sum() >= 500]
    prices = prices[["moex_imoex", *available]]
    signature = hashlib.sha256(pd.util.hash_pandas_object(prices, index=True).values.tobytes()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{signature}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM sector_rotation_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    # A killed process can only leave rows before the immutable run marker is written.
    con.execute("DELETE FROM sector_rotation_scores WHERE run_id=?", [run_id])
    con.execute("DELETE FROM sector_rotation_scorecards WHERE run_id=?", [run_id])
    holdout_position = int(len(prices) * 0.8)
    holdout_from = prices.index[holdout_position]
    rows: list[list[object]] = []
    cards: list[list[object]] = []
    for horizon in HORIZONS:
        momentum = (
            sum(
                prices[available].pct_change(window, fill_method=None).rank(axis=1, pct=True)
                for window in (5, 20, 60)
            )
            / 3
        )
        future = prices[available].shift(-horizon) / prices[available] - 1
        market_future = prices.moex_imoex.shift(-horizon) / prices.moex_imoex - 1
        excess = future.sub(market_future, axis=0)
        predicted_rank = momentum.rank(axis=1, ascending=False, method="min")
        actual_rank = excess.rank(axis=1, ascending=False, method="min")
        for sample, mask in (
            ("validation", prices.index < holdout_from),
            ("frozen_holdout", prices.index >= holdout_from),
        ):
            correlations: list[float] = []
            spreads: list[float] = []
            observations = 0
            for trade_date in prices.index[mask]:
                valid = momentum.loc[trade_date].notna() & excess.loc[trade_date].notna()
                if valid.sum() < 4:
                    continue
                correlations.append(
                    float(
                        momentum.loc[trade_date, valid].corr(excess.loc[trade_date, valid], method="spearman")
                    )
                )
                ordered = momentum.loc[trade_date, valid].sort_values()
                spreads.append(
                    float(
                        excess.loc[trade_date, ordered.index[-1]] - excess.loc[trade_date, ordered.index[0]]
                    )
                )
                observations += 1
                for sector in ordered.index:
                    rows.append(
                        [
                            run_id,
                            trade_date,
                            sector,
                            horizon,
                            float(momentum.at[trade_date, sector]),
                            int(predicted_rank.at[trade_date, sector]),
                            float(excess.at[trade_date, sector]),
                            int(actual_rank.at[trade_date, sector]),
                            sample,
                        ]
                    )
            rank_ic = float(np.nanmean(correlations)) if correlations else None
            spread = float(np.nanmean(spreads)) if spreads else None
            status = (
                "experimental"
                if sample == "frozen_holdout" and rank_ic and rank_ic > 0.03
                else "weak_or_rejected"
            )
            cards.append([run_id, horizon, sample, observations, rank_ic, spread, 0.0, status])
    score_frame = pd.DataFrame(
        rows,
        columns=[
            "run_id",
            "trade_date",
            "sector",
            "horizon",
            "momentum_score",
            "predicted_rank",
            "actual_excess_return",
            "actual_rank",
            "sample",
        ],
    )
    con.register("stage73_scores", score_frame)
    con.execute(
        """INSERT INTO sector_rotation_scores
        (run_id,trade_date,sector,horizon,momentum_score,predicted_rank,actual_excess_return,actual_rank,sample)
        SELECT run_id,trade_date,sector,horizon,momentum_score,predicted_rank,
        actual_excess_return,actual_rank,sample FROM stage73_scores""",
    )
    con.executemany(
        """INSERT INTO sector_rotation_scorecards
        (run_id,horizon,sample,observations,rank_ic,top_bottom_spread,baseline_rank_ic,status)
        VALUES (?,?,?,?,?,?,?,?)""",
        cards,
    )
    con.execute(
        """INSERT INTO sector_rotation_runs
        (run_id,created_at,date_from,date_to,holdout_from,sectors,observations,methodology_version,immutable,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            prices.index.min(),
            prices.index.max(),
            holdout_from,
            len(available),
            len(rows),
            VERSION,
            True,
            "completed",
            json.dumps({"oil_not_hardcoded": True, "same_day_close_used_next_session": True}),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, object]:
    row = con.execute(
        """SELECT count(*),avg(rank_ic),max(rank_ic),avg(top_bottom_spread)
        FROM sector_rotation_scorecards WHERE run_id=? AND sample='frozen_holdout'""",
        [run_id],
    ).fetchone()
    sectors = con.execute("SELECT sectors FROM sector_rotation_runs WHERE run_id=?", [run_id]).fetchone()[0]
    return {
        "run_id": run_id,
        "sectors": sectors,
        "horizons": row[0],
        "mean_holdout_rank_ic": row[1],
        "best_holdout_rank_ic": row[2],
        "mean_top_bottom_spread": row[3],
        "status": "completed",
    }
