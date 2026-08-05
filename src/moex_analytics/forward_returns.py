"""Future outcome labels kept separate from point-in-time features."""

from __future__ import annotations

from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from .config import load_settings

HORIZONS = (5, 20, 60, 120, 250)


def calculate_forward_frame(frame: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    frame = frame.sort_values("trade_date").reset_index(drop=True).copy()
    rows = []
    for horizon in horizons:
        close = frame["close"].astype(float)
        exit_price = close.shift(-horizon)
        price_return = exit_price / close - 1
        total_index = frame.get("total_return_index", pd.Series(np.nan, index=frame.index))
        total_return = total_index.shift(-horizon) / total_index - 1
        for index in frame.index:
            known = index + horizon < len(frame)
            path = close.iloc[index : index + horizon + 1]
            rows.append(
                {
                    "condition_date": frame.at[index, "trade_date"],
                    "exit_date": frame.at[index + horizon, "trade_date"] if known else None,
                    "horizon": horizon,
                    "price_return": price_return.at[index] if known else None,
                    "total_return": total_return.at[index] if known else None,
                    "max_drawdown": float(np.min(path / np.maximum.accumulate(path) - 1)) if known else None,
                    "max_gain": float(path.max() / close.at[index] - 1) if known else None,
                }
            )
    return pd.DataFrame(rows)


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["analytics"]
    version = cfg["calculation_version"]
    con.execute("DELETE FROM forward_returns WHERE calculation_version=?", [version])
    now, total = datetime.now(), 0
    secids = [
        row[0]
        for row in con.execute("SELECT DISTINCT canonical_secid FROM canonical_daily_prices").fetchall()
    ]
    for secid in secids:
        frame = con.execute(
            """SELECT p.trade_date,p.close,r.total_return_index FROM canonical_daily_prices p
            LEFT JOIN daily_returns r ON r.trade_date=p.trade_date
              AND r.canonical_secid=p.canonical_secid
            WHERE p.canonical_secid=? ORDER BY p.trade_date""",
            [secid],
        ).fetchdf()
        result = calculate_forward_frame(frame)
        result.insert(2, "canonical_secid", secid)
        result["calculation_version"] = version
        result["calculated_at"] = now
        result["source"] = cfg["source"]
        result["minimum_history"] = cfg["minimum_history"]
        result = result[
            [
                "condition_date",
                "exit_date",
                "canonical_secid",
                "horizon",
                "price_return",
                "total_return",
                "max_drawdown",
                "max_gain",
                "calculation_version",
                "calculated_at",
                "source",
                "minimum_history",
            ]
        ]
        con.register("incoming_forward", result)
        con.execute("INSERT INTO forward_returns SELECT * FROM incoming_forward")
        con.unregister("incoming_forward")
        total += len(result)
    return total
