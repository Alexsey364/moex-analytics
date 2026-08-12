"""Compact feature matrix whose every value is observable at the evaluation cutoff."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "predictive-features-v1"
FAMILIES = ("price", "volatility", "liquidity", "market", "sector", "rates",
            "fx_commodities", "fundamentals", "valuation", "cross_sectional")
KEYS = {"run_id", "trade_date", "secid", "available_at", "feature_version", "history_end",
        "immutable"}


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _price_features(source: pd.DataFrame) -> pd.DataFrame:
    outputs = []
    for _secid, frame in source.groupby("secid", sort=True):
        frame = frame.sort_values("trade_date").copy()
        price = frame.total_return_index.astype(float)
        daily = price.pct_change()
        out = frame[["trade_date", "secid"]].copy()
        out["price"] = price
        for horizon in (1, 5, 10, 20, 40, 60, 120, 250):
            out[f"return_{horizon}"] = price.pct_change(horizon)
        out["ma_distance_20"] = price / price.rolling(20, min_periods=20).mean() - 1
        out["ma_distance_60"] = price / price.rolling(60, min_periods=60).mean() - 1
        out["drawdown_250"] = price / price.rolling(250, min_periods=60).max() - 1
        out["trend_slope_20"] = np.log(price).diff(20) / 20
        out["trend_consistency_20"] = (daily > 0).rolling(20, min_periods=20).mean()
        for window in (5, 20, 60):
            out[f"realized_vol_{window}"] = daily.rolling(window, min_periods=window).std() * np.sqrt(252)
        out["downside_vol_20"] = daily.where(daily < 0, 0).rolling(20, min_periods=20).std() * np.sqrt(252)
        out["volatility_ratio"] = out.realized_vol_5 / out.realized_vol_60
        outputs.append(out)
    return pd.concat(outputs, ignore_index=True)


def _optional_context(con: Any) -> pd.DataFrame:
    exists = con.execute("SELECT count(*) FROM information_schema.tables "
        "WHERE table_name='synchronized_predictive_context'").fetchone()[0]
    if not exists:
        return pd.DataFrame(columns=["trade_date", "secid"])
    return con.execute("SELECT trade_date,secid,sector_return_20,issuer_relative_sector_20 "
        "AS relative_sector_20,key_rate,ruonia,rgbi,cbr_usd_rub AS usd_rub,fx_return_20,brent,"
        "brent_return_20,breadth_balance,liquidity_percentile AS liquidity_rank "
        "FROM synchronized_predictive_context QUALIFY row_number() OVER "
        "(PARTITION BY trade_date,secid ORDER BY run_id DESC)=1").df()


def _build_frame(con: Any, source: pd.DataFrame) -> pd.DataFrame:
    frame = _price_features(source)
    market = frame[frame.secid == "IMOEX"][["trade_date", "return_20", "drawdown_250",
        "realized_vol_20"]].rename(columns={"return_20": "market_return_20",
        "drawdown_250": "market_drawdown_250", "realized_vol_20": "market_vol_20"})
    frame = frame.merge(market, on="trade_date", how="left")
    frame = frame.merge(_optional_context(con), on=["trade_date", "secid"], how="left")
    optional = ("breadth_balance", "sector_return_20", "relative_sector_20", "key_rate",
                "ruonia", "rgbi", "usd_rub", "fx_return_20", "brent", "brent_return_20")
    for column in optional:
        if column not in frame:
            frame[column] = np.nan
    frame["turnover_20"] = np.nan
    frame["liquidity_spike"] = np.nan
    frame["dividend_yield"] = np.nan
    frame["growth_score"] = np.nan
    frame["valuation_history_score"] = np.nan
    equities = frame.secid != "IMOEX"
    grouped = frame.loc[equities].groupby("trade_date")
    frame.loc[equities, "momentum_rank"] = grouped.return_60.rank(pct=True)
    frame.loc[equities, "volatility_rank"] = grouped.realized_vol_20.rank(pct=True, ascending=False)
    if "liquidity_rank" not in frame:
        frame["liquidity_rank"] = np.nan
    frame["available_at"] = pd.to_datetime(frame.trade_date.astype(str)) + pd.Timedelta(hours=18, minutes=50)
    frame["history_end"] = frame.trade_date
    frame["feature_version"] = VERSION
    frame["immutable"] = True
    return frame


def _diagnostics(frame: pd.DataFrame, run_id: str) -> pd.DataFrame:
    features = [column for column in frame.select_dtypes(include=np.number).columns if column not in KEYS]
    rows = []
    for feature in features:
        count = int(frame[feature].notna().sum())
        missingness = float(frame[feature].isna().mean())
        rows.append([run_id, "missingness", feature, "", missingness, count,
                     "available" if count else "unavailable", "{}"])
    correlations = frame[features].corr()
    for index, left in enumerate(features):
        for right in features[index + 1:]:
            value = correlations.loc[left, right]
            if pd.notna(value) and abs(value) >= .90:
                rows.append([run_id, "high_correlation", left, right, float(value),
                    int(frame[[left, right]].dropna().shape[0]), "review", "{}"])
    complete = frame[features].dropna(axis=1, how="all").dropna()
    condition = float(np.linalg.cond(complete.to_numpy())) if len(complete) and complete.shape[1] else None
    rows.append([run_id, "condition_number", "all_complete", "", condition,
                 len(complete), "diagnostic_only", json.dumps({"standardized": False})])
    return pd.DataFrame(rows, columns=("run_id", "diagnostic_type", "feature_a", "feature_b",
        "value", "observations", "status", "details_json"))


def build_feature_store(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    versions = con.execute("SELECT DISTINCT calculation_version FROM daily_returns").fetchall()
    if len(versions) != 1:
        raise ValueError("one frozen daily_returns version is required")
    source_version = str(versions[0][0])
    source = con.execute("SELECT trade_date,canonical_secid AS secid,total_return_index "
        "FROM daily_returns WHERE calculation_version=? ORDER BY secid,trade_date", [source_version]).df()
    signature = hashlib.sha256(pd.util.hash_pandas_object(source, index=False).values.tobytes()).hexdigest()
    feature_columns = [row[0] for row in con.execute("DESCRIBE predictive_feature_store").fetchall()]
    feature_signature = hashlib.sha256(repr(feature_columns).encode()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{signature}|{feature_signature}".encode()).hexdigest()[:20]
    existing = con.execute("SELECT status,rows,features,families FROM predictive_feature_runs "
        "WHERE run_id=?", [run_id]).fetchone()
    if existing and existing[0] == "completed":
        return {"run_id": run_id, "status": existing[0], "rows": existing[1],
                "features": existing[2], "families": existing[3], "cached": True}
    frame = _build_frame(con, source)
    frame.insert(0, "run_id", run_id)
    feature_count = len([column for column in frame.columns if column not in KEYS])
    diagnostics = _diagnostics(frame, run_id)
    columns = [row[0] for row in con.execute("DESCRIBE predictive_feature_store").fetchall()]
    frame = frame[columns]
    con.execute("BEGIN")
    try:
        con.execute("INSERT OR REPLACE INTO predictive_feature_runs "
            "(run_id,version,source_version,cutoff,data_signature,feature_signature,started_at,status,"
            "rows,features,families,details_json,immutable) VALUES (?,?,?,?,?,?,current_timestamp,"
            "'running',0,?,?,?,true)", [run_id, VERSION, source_version, frame.trade_date.max(),
            signature, feature_signature, feature_count, len(FAMILIES),
            json.dumps({"families": FAMILIES, "analog_features": False, "production_changes": 0})])
        for table, data in (("predictive_feature_store", frame),
                            ("predictive_feature_diagnostics", diagnostics)):
            con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
            con.register("_incoming", data)
            names = ",".join(data.columns)
            con.execute(f"INSERT INTO {table} ({names}) SELECT {names} FROM _incoming")
            con.unregister("_incoming")
        con.execute("UPDATE predictive_feature_runs SET finished_at=current_timestamp,status='completed',"
            "rows=? WHERE run_id=?", [len(frame), run_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"run_id": run_id, "status": "completed", "rows": len(frame),
            "features": feature_count, "families": len(FAMILIES), "cached": False}
