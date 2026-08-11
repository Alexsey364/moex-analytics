"""Build the point-in-time, explainable whole-market state passport."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .schema import ensure_schema

VERSION = "stage71-v2"
RETURN_WINDOWS = (1, 5, 20, 60, 120, 250)
SMA_WINDOWS = (20, 50, 100, 200)


def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }


def _json(values: dict[str, Any]) -> str:
    clean = {key: (None if pd.isna(value) else value) for key, value in values.items()}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)


def _market_prices(con: duckdb.DuckDBPyConnection, secid: str) -> pd.DataFrame:
    return con.execute(
        """SELECT trade_date, close, high, low, value, number_of_trades
        FROM canonical_daily_prices WHERE canonical_secid = ? AND close > 0
        ORDER BY trade_date""",
        [secid],
    ).df()


def _macro_daily(con: duckdb.DuckDBPyConnection, dates: pd.Series) -> dict[date, dict[str, float]]:
    if "macro_observations" not in _tables(con):
        return {}
    rows = con.execute(
        """SELECT series_id, observation_date, release_date, available_from, value
        FROM macro_observations WHERE value IS NOT NULL ORDER BY available_from"""
    ).df()
    if rows.empty:
        return {}
    calendar = pd.DataFrame({"trade_date": pd.to_datetime(dates).sort_values().unique()})
    calendar["cutoff"] = calendar.trade_date.dt.tz_localize("Europe/Moscow").dt.tz_convert(
        "UTC"
    ) + pd.Timedelta(hours=23, minutes=59)
    result: dict[date, dict[str, float]] = {value.date(): {} for value in calendar.trade_date}
    rows["available_from"] = pd.to_datetime(rows["available_from"], utc=True).astype("datetime64[ns, UTC]")
    for series_id, observations in rows.groupby("series_id"):
        right = observations.sort_values("available_from")[["available_from", "value"]]
        aligned = pd.merge_asof(
            calendar, right, left_on="cutoff", right_on="available_from", direction="backward"
        )
        for item in aligned.dropna(subset=["value"]).itertuples():
            result[item.trade_date.date()][str(series_id)] = float(item.value)
    return result


def _classify(row: pd.Series) -> str:
    vol = row.get("realized_vol20")
    ret = row.get("return_20")
    drawdown = row.get("drawdown")
    if pd.notna(vol) and pd.notna(drawdown) and (vol > 0.35 or drawdown < -0.2):
        return "stress"
    if pd.notna(ret) and ret > 0.05:
        return "trend_up"
    if pd.notna(ret) and ret < -0.05:
        return "trend_down"
    return "transition_or_range"


def _prepare(con: duckdb.DuckDBPyConnection, cutoff: date | None) -> pd.DataFrame:
    frame = _market_prices(con, "IMOEX")
    if frame.empty:
        raise ValueError("IMOEX canonical history is required")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    if cutoff:
        frame = frame[frame.trade_date.dt.date <= cutoff].copy()
    close = frame["close"].astype(float)
    returns = close.pct_change()
    for window in RETURN_WINDOWS:
        frame[f"return_{window}"] = close.pct_change(window)
    for window in SMA_WINDOWS:
        frame[f"distance_sma{window}"] = close / close.rolling(window, min_periods=window).mean() - 1
    frame["drawdown"] = close / close.cummax() - 1
    frame["realized_vol20"] = returns.rolling(20, min_periods=20).std() * np.sqrt(252)
    frame["realized_vol60"] = returns.rolling(60, min_periods=60).std() * np.sqrt(252)
    daily_range = (frame["high"] - frame["low"]) / close
    frame["range_expansion"] = daily_range / daily_range.rolling(20, min_periods=20).mean()
    rtsi = _market_prices(con, "RTSI")
    if not rtsi.empty:
        rtsi["trade_date"] = pd.to_datetime(rtsi["trade_date"])
        rtsi["rtsi_return_20"] = rtsi.close.pct_change(20)
        frame = frame.merge(rtsi[["trade_date", "rtsi_return_20"]], on="trade_date", how="left")
    else:
        frame["rtsi_return_20"] = np.nan
    return frame


def build_whole_market_state(con: duckdb.DuckDBPyConnection, cutoff: date | None = None) -> dict[str, Any]:
    """Persist an immutable PIT snapshot; an identical rerun is idempotent."""
    ensure_schema(con)
    frame = _prepare(con, cutoff)
    tables = _tables(con)
    end_date = frame.trade_date.max().date()
    fingerprint = con.execute(
        """SELECT count(*), min(trade_date), max(trade_date), sum(coalesce(close, 0))
        FROM canonical_daily_prices WHERE trade_date <= ?""",
        [end_date],
    ).fetchone()
    input_hash = hashlib.sha256(repr(fingerprint).encode()).hexdigest()
    run_id = hashlib.sha256(f"{VERSION}|{end_date}|{input_hash}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT observations FROM whole_market_state_runs WHERE run_id = ?", [run_id]
    ).fetchone()
    if existing:
        return whole_market_state_status(con, run_id) | {"idempotent": True}

    breadth: dict[date, dict[str, Any]] = {}
    if "market_breadth_daily" in tables:
        for row in (
            con.execute(
                "SELECT * FROM market_breadth_daily WHERE trade_date <= ? ORDER BY trade_date", [end_date]
            )
            .fetchdf()
            .to_dict("records")
        ):
            key = pd.Timestamp(row.pop("trade_date")).date()
            row.pop("calculated_at", None)
            count = row.get("tradable_count") or 0
            for name in ("above_sma20", "above_sma50", "above_sma200", "positive_mom20", "positive_mom60"):
                row[f"{name}_share"] = row.get(name) / count if count else None
            breadth[key] = row
    liquidity: dict[date, dict[str, Any]] = {}
    if "equity_liquidity_daily" in tables:
        query = """SELECT trade_date, sum(turnover) total_turnover, sum(num_trades) trades,
        avg(average_trade_value) average_trade_value, avg(amihud) amihud,
        avg(liquidity_percentile) liquidity_percentile,
        avg(CASE WHEN zero_volume THEN 1.0 ELSE 0.0 END) zero_volume_share
        FROM equity_liquidity_daily WHERE trade_date <= ? GROUP BY trade_date"""
        for row in con.execute(query, [end_date]).fetchdf().to_dict("records"):
            liquidity[pd.Timestamp(row.pop("trade_date")).date()] = row
    macro = _macro_daily(con, frame.trade_date)
    news: dict[date, dict[str, Any]] = {}
    if "news_items" in tables:
        query = """SELECT CAST(published_at AT TIME ZONE 'Europe/Moscow' AS DATE) trade_date,
        count(*) item_count, count(DISTINCT story_id) story_count,
        sum(CASE WHEN tone='negative' THEN 1 ELSE 0 END) negative_count,
        sum(CASE WHEN tone='positive' THEN 1 ELSE 0 END) positive_count
        FROM news_items WHERE immutable AND available_from <= ? GROUP BY 1"""
        limit = datetime.combine(end_date, datetime.max.time(), tzinfo=UTC)
        for row in con.execute(query, [limit]).fetchdf().to_dict("records"):
            row["predictive_weight"] = 0.0
            row["status"] = "context_only_unvalidated"
            news[pd.Timestamp(row.pop("trade_date")).date()] = row
    regimes: dict[date, dict[str, Any]] = {}
    if "regime_timeline_v2" in tables:
        query = """SELECT trade_date, algorithm, k, regime, regime_duration, novelty_status
        FROM regime_timeline_v2 WHERE selected AND trade_date <= ? QUALIFY row_number() OVER
        (PARTITION BY trade_date ORDER BY run_id DESC, algorithm, k) = 1 ORDER BY trade_date"""
        regime_rows = con.execute(query, [end_date]).fetchdf().to_dict("records")
        position = 0
        last: dict[str, Any] | None = None
        for trade_day in frame.trade_date:
            day = pd.Timestamp(trade_day).date()
            while position < len(regime_rows):
                candidate_date = pd.Timestamp(regime_rows[position]["trade_date"]).date()
                if candidate_date > day:
                    break
                last = dict(regime_rows[position])
                position += 1
            if last:
                source_date = pd.Timestamp(last["trade_date"]).date()
                regimes[day] = {key: value for key, value in last.items() if key != "trade_date"} | {
                    "source_date": str(source_date),
                    "age_calendar_days": (day - source_date).days,
                }

    columns = [
        "run_id",
        "trade_date",
        "available_from",
        "imoex_close",
        "return_1",
        "return_5",
        "return_20",
        "return_60",
        "return_120",
        "return_250",
        "drawdown",
        "distance_sma20",
        "distance_sma50",
        "distance_sma100",
        "distance_sma200",
        "realized_vol20",
        "realized_vol60",
        "range_expansion",
        "rtsi_return_20",
        "breadth_json",
        "liquidity_json",
        "volatility_json",
        "rates_json",
        "fx_json",
        "commodities_json",
        "sectors_json",
        "futures_json",
        "options_json",
        "news_json",
        "regime_json",
        "market_state_label",
        "methodology_version",
        "immutable",
    ]
    placeholders = ",".join("?" for _ in columns)
    insert = f"INSERT INTO whole_market_state_daily ({','.join(columns)}) VALUES ({placeholders})"
    rows = []
    for record in frame.to_dict("records"):
        day = pd.Timestamp(record["trade_date"]).date()
        macro_day = macro.get(day, {})
        rates = {
            k: v
            for k, v in macro_day.items()
            if any(x in k.upper() for x in ("RATE", "RUONIA", "RUSFAR", "RGBI", "ZCYC", "OFZ"))
        }
        fx = {k: v for k, v in macro_day.items() if any(x in k.upper() for x in ("USD", "CNY", "EUR", "FX"))}
        commodities = {
            k: v
            for k, v in macro_day.items()
            if any(x in k.upper() for x in ("BRENT", "GOLD", "GAS", "URAL"))
        }
        sectors = {
            k: v for k, v in macro_day.items() if k.startswith("moex_") and k not in rates and k not in fx
        }
        rvi = macro_day.get("moex_rvi")
        regime = regimes.get(day, {})
        row_series = pd.Series(record)
        label = _classify(row_series)
        rows.append(
            [
                run_id,
                day,
                datetime.combine(day, datetime.max.time(), tzinfo=UTC),
                record["close"],
                *[record.get(f"return_{w}") for w in RETURN_WINDOWS],
                record.get("drawdown"),
                *[record.get(f"distance_sma{w}") for w in SMA_WINDOWS],
                record.get("realized_vol20"),
                record.get("realized_vol60"),
                record.get("range_expansion"),
                record.get("rtsi_return_20"),
                _json(breadth.get(day, {})),
                _json(liquidity.get(day, {})),
                _json(
                    {
                        "realized_vol20": record.get("realized_vol20"),
                        "realized_vol60": record.get("realized_vol60"),
                        "rvi": rvi,
                    }
                ),
                _json(rates),
                _json(fx),
                _json(commodities),
                _json(sectors | {"status": "pit_macro_index_series"}),
                _json({"status": "validated_only", "included": False}),
                _json({"status": "rvi_context_only", "rvi": rvi}),
                _json(news.get(day, {"predictive_weight": 0.0, "status": "no_items"})),
                _json(regime),
                label,
                VERSION,
                True,
            ]
        )
    con.executemany(insert, rows)
    feature_count = len(columns) - 5
    details = {"legacy_market_state_untouched": True, "news_predictive_weight": 0, "pit_safe": True}
    con.execute(
        """INSERT INTO whole_market_state_runs
        (run_id,created_at,cutoff,date_from,date_to,observations,feature_count,input_hash,methodology_version,immutable,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            end_date,
            frame.trade_date.min().date(),
            end_date,
            len(rows),
            feature_count,
            input_hash,
            VERSION,
            True,
            "completed",
            _json(details),
        ],
    )
    return whole_market_state_status(con, run_id) | {"idempotent": False}


def whole_market_state_status(con: duckdb.DuckDBPyConnection, run_id: str | None = None) -> dict[str, Any]:
    """Return compact Stage 71 evidence."""
    ensure_schema(con)
    if run_id is None:
        value = con.execute(
            "SELECT run_id FROM whole_market_state_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not value:
            return {"status": "not_run", "observations": 0}
        run_id = value[0]
    row = con.execute(
        """SELECT run_id,date_from,date_to,observations,feature_count,status
        FROM whole_market_state_runs WHERE run_id=?""",
        [run_id],
    ).fetchone()
    current = con.execute(
        """SELECT trade_date,market_state_label,regime_json FROM whole_market_state_daily
        WHERE run_id=? ORDER BY trade_date DESC LIMIT 1""",
        [run_id],
    ).fetchone()
    return {
        "run_id": row[0],
        "date_from": str(row[1]),
        "date_to": str(row[2]),
        "observations": row[3],
        "feature_count": row[4],
        "status": row[5],
        "current_date": str(current[0]),
        "current_state": current[1],
        "current_regime": json.loads(current[2]),
    }
