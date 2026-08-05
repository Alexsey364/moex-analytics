"""Build point-in-time macro feature snapshots for every market session."""

from __future__ import annotations

import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from ..config import load_settings
from .transformations import market_transform


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["macro"]
    version = cfg["calculation_version"]
    safe = con.execute("SELECT series_id FROM macro_series WHERE is_point_in_time_safe").fetchall()
    safe_ids = [row[0] for row in safe]
    sessions = con.execute("""SELECT DISTINCT trade_date FROM canonical_daily_prices
        WHERE canonical_secid='IMOEX' ORDER BY 1""").fetchdf()
    if sessions.empty:
        return 0
    sessions["available_at"] = pd.to_datetime(sessions["trade_date"]).map(
        lambda d: datetime.combine(
            d.date(), time.fromisoformat(cfg["cutoff"]["next_close"]), ZoneInfo(cfg["cutoff"]["timezone"])
        )
    )
    features = pd.DataFrame(index=pd.to_datetime(sessions["trade_date"]))
    source_dates: dict[str, pd.Series] = {}
    for series_id in safe_ids:
        obs = con.execute(
            """SELECT observation_date,available_from,value
            FROM macro_observations WHERE series_id=? ORDER BY available_from""",
            [series_id],
        ).fetchdf()
        if obs.empty:
            continue
        left = sessions[["trade_date", "available_at"]].sort_values("available_at")
        obs["available_from"] = pd.to_datetime(obs["available_from"], utc=True).astype("datetime64[ns, UTC]")
        left["available_at"] = pd.to_datetime(left["available_at"], utc=True).astype("datetime64[ns, UTC]")
        aligned = pd.merge_asof(
            left, obs, left_on="available_at", right_on="available_from", direction="backward"
        )
        series = pd.Series(aligned["value"].to_numpy(), index=features.index)
        source_dates[series_id] = pd.Series(aligned["observation_date"].to_numpy(), index=features.index)
        features[series_id] = series
        features = features.join(market_transform(series, series_id))
    con.execute("DELETE FROM macro_features WHERE calculation_version=?", [version])
    secids = [row[0] for row in con.execute("SELECT secid FROM instruments ORDER BY secid").fetchall()]
    now = datetime.now()
    output = []
    available_by_date = {
        pd.Timestamp(key).date(): value
        for key, value in zip(sessions["trade_date"], sessions["available_at"], strict=True)
    }
    for trade_date, row in features.iterrows():
        payload = {key: (None if pd.isna(value) else float(value)) for key, value in row.items()}
        dates = {
            key: (None if pd.isna(values.loc[trade_date]) else str(values.loc[trade_date]))
            for key, values in source_dates.items()
        }
        for secid in secids:
            for horizon in cfg["horizons"]:
                output.append(
                    [
                        trade_date.date(),
                        secid,
                        horizon,
                        json.dumps(payload),
                        json.dumps(dates),
                        available_by_date[trade_date.date()],
                        version,
                        now,
                    ]
                )
    if output:
        frame = pd.DataFrame(
            output,
            columns=[
                "trade_date",
                "canonical_secid",
                "horizon",
                "features_json",
                "source_dates_json",
                "available_at",
                "calculation_version",
                "calculated_at",
            ],
        )
        con.register("incoming_macro_features", frame)
        try:
            con.execute("INSERT INTO macro_features SELECT * FROM incoming_macro_features")
        finally:
            con.unregister("incoming_macro_features")
    return len(output)
