"""Leakage-safe, family-balanced conditional similarity (Stage 95)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

from moex_analytics.conditioned_stock_forecasting.core import SECIDS

from .schema import ensure_schema

FEATURE_VERSION = "pit-state-families-v1"
SIMILARITY_VERSION = "conditional-similarity-v2"
FAMILY_COLUMNS = {
    "price": (
        "return_1",
        "return_5",
        "return_10",
        "return_20",
        "return_40",
        "return_60",
        "return_120",
        "return_250",
        "distance_high_252",
        "distance_low_252",
        "drawdown",
        "recovery_60",
        "sma20_distance",
        "sma50_distance",
        "sma100_distance",
        "sma200_distance",
        "momentum_change",
    ),
    "volatility": ("volatility_5", "volatility_20", "volatility_60", "downside_volatility", "range_20"),
    "market": (
        "market_return_5",
        "market_return_20",
        "market_return_60",
        "market_drawdown",
        "market_volatility_20",
        "breadth_sma20",
        "breadth_sma50",
        "breadth_sma200",
        "breadth_dispersion",
    ),
    "rates": (),
    "fx_commodities": (),
    "sector": (
        "sector_return_20",
        "sector_return_60",
        "sector_volatility_60",
        "sector_drawdown",
        "relative_strength_20",
        "relative_strength_60",
    ),
    "fundamental": (),
}


def _config() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[3] / "config" / "conditional_forecasting.yaml"
    raw = path.read_bytes()
    return yaml.safe_load(raw)["conditional_similarity"], hashlib.sha256(raw).hexdigest()


def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _price_panel(con: duckdb.DuckDBPyConnection, secid: str, cutoff: Any) -> pd.DataFrame:
    frame = con.execute(
        """SELECT trade_date,close,high,low FROM canonical_daily_prices
        WHERE canonical_secid=? AND trade_date<=? AND close>0 ORDER BY trade_date""",
        [secid, cutoff],
    ).df()
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    frame = frame.set_index("trade_date")
    close = frame.close.astype(float)
    daily = close.pct_change(fill_method=None)
    for window in (1, 5, 10, 20, 40, 60, 120, 250):
        frame[f"return_{window}"] = close.pct_change(window, fill_method=None)
    high = close.rolling(252, min_periods=60).max()
    low = close.rolling(252, min_periods=60).min()
    frame["distance_high_252"] = close / high - 1
    frame["distance_low_252"] = close / low - 1
    frame["drawdown"] = close / close.cummax() - 1
    frame["recovery_60"] = close / close.rolling(60, min_periods=20).min() - 1
    for window in (20, 50, 100, 200):
        frame[f"sma{window}_distance"] = close / close.rolling(window, min_periods=window).mean() - 1
    frame["momentum_change"] = frame.return_20 - frame.return_20.shift(20)
    for window in (5, 20, 60):
        frame[f"volatility_{window}"] = daily.rolling(window, min_periods=window).std() * np.sqrt(252)
    frame["downside_volatility"] = daily.where(daily < 0).rolling(20, min_periods=8).std() * np.sqrt(252)
    if frame.high.notna().any() and frame.low.notna().any():
        frame["range_20"] = ((frame.high - frame.low) / close).rolling(20, min_periods=10).mean()
    else:
        frame["range_20"] = np.nan
    return frame


def _market_context(con: duckdb.DuckDBPyConnection, cutoff: Any) -> pd.DataFrame:
    tables = _tables(con)
    if "whole_market_state_daily" not in tables:
        return pd.DataFrame()
    frame = con.execute(
        """SELECT trade_date,return_5 market_return_5,return_20 market_return_20,
        return_60 market_return_60,drawdown market_drawdown,
        realized_vol20 market_volatility_20,market_state_label
        FROM whole_market_state_daily WHERE run_id=(SELECT run_id FROM whole_market_state_runs
        WHERE cutoff<=? AND status='completed' ORDER BY cutoff DESC LIMIT 1)
        AND trade_date<=? ORDER BY trade_date""",
        [cutoff, cutoff],
    ).df()
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    frame = frame.set_index("trade_date")
    if "market_breadth_daily" in tables:
        breadth = con.execute(
            """SELECT trade_date,above_sma20/tradable_count breadth_sma20,
            above_sma50/tradable_count breadth_sma50,above_sma200/tradable_count breadth_sma200,
            return_dispersion breadth_dispersion FROM market_breadth_daily
            WHERE trade_date<=? AND tradable_count>0 ORDER BY trade_date""",
            [cutoff],
        ).df()
        breadth["trade_date"] = pd.to_datetime(breadth.trade_date)
        frame = frame.join(breadth.set_index("trade_date"), how="left")
    return frame


def _macro_context(
    con: duckdb.DuckDBPyConnection, dates: pd.DatetimeIndex
) -> tuple[pd.DataFrame, dict[str, tuple[str, ...]]]:
    if "macro_observations" not in _tables(con) or dates.empty:
        return pd.DataFrame(index=dates), {"rates": (), "fx_commodities": ()}
    raw = con.execute(
        """SELECT series_id,available_from,value FROM macro_observations
        WHERE value IS NOT NULL AND available_from<=? ORDER BY available_from""",
        [dates.max().date()],
    ).df()
    if raw.empty:
        return pd.DataFrame(index=dates), {"rates": (), "fx_commodities": ()}
    raw["available_from"] = pd.to_datetime(raw.available_from, utc=True).astype(
        "datetime64[ns, UTC]"
    )
    calendar = pd.DataFrame({"trade_date": dates})
    calendar["available_at"] = (
        calendar.trade_date.dt.tz_localize("Europe/Moscow").dt.tz_convert("UTC")
        + pd.Timedelta(hours=23)
    ).astype("datetime64[ns, UTC]")
    output = pd.DataFrame(index=dates)
    groups: dict[str, list[str]] = {"rates": [], "fx_commodities": []}
    for series, rows in raw.groupby("series_id"):
        upper = str(series).upper()
        family = None
        if any(key in upper for key in ("RATE", "RUONIA", "RUSFAR", "RGBI", "ZCYC", "OFZ")):
            family = "rates"
        elif any(key in upper for key in ("USD", "CNY", "EUR", "BRENT", "OIL", "GOLD", "URAL")):
            family = "fx_commodities"
        if family is None:
            continue
        name = f"macro_{hashlib.sha1(str(series).encode()).hexdigest()[:10]}"
        aligned = pd.merge_asof(
            calendar.sort_values("available_at"),
            rows[["available_from", "value"]].sort_values("available_from"),
            left_on="available_at",
            right_on="available_from",
            direction="backward",
        )
        output[name] = aligned.value.to_numpy()
        groups[family].append(name)
    return output, {key: tuple(value) for key, value in groups.items()}


def _sector_context(con: duckdb.DuckDBPyConnection, secid: str, cutoff: Any) -> pd.DataFrame:
    if "issuer_sector_context_daily" not in _tables(con):
        return pd.DataFrame()
    frame = con.execute(
        """SELECT trade_date,sector_return_20,sector_return_60,sector_volatility_60,
        sector_drawdown,relative_strength_20,relative_strength_60
        FROM issuer_sector_context_daily WHERE secid=? AND trade_date<=?
        AND pit_status IN ('point_in_time','validated','ready') ORDER BY trade_date""",
        [secid, cutoff],
    ).df()
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    return frame.drop_duplicates("trade_date", keep="last").set_index("trade_date")


def _fundamental_context(
    con: duckdb.DuckDBPyConnection, secid: str, dates: pd.DatetimeIndex
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    if "issuer_pit_fundamental_states" not in _tables(con) or dates.empty:
        return pd.DataFrame(index=dates), ()
    raw = con.execute(
        """SELECT metric,available_from,value FROM issuer_pit_fundamental_states
        WHERE secid=? AND value IS NOT NULL AND available_from<=?
        AND validation_status IN ('validated','confirmed','ready') ORDER BY available_from""",
        [secid, dates.max().date()],
    ).df()
    if raw.empty:
        return pd.DataFrame(index=dates), ()
    raw["available_from"] = pd.to_datetime(raw.available_from)
    output = pd.DataFrame(index=dates)
    calendar = pd.DataFrame({"trade_date": dates})
    names = []
    for metric, rows in raw.groupby("metric"):
        name = f"fund_{hashlib.sha1(str(metric).encode()).hexdigest()[:10]}"
        aligned = pd.merge_asof(
            calendar, rows[["available_from", "value"]].sort_values("available_from"),
            left_on="trade_date", right_on="available_from", direction="backward"
        )
        output[name] = aligned.value.to_numpy()
        names.append(name)
    return output, tuple(names)


def build_state_panel(
    con: duckdb.DuckDBPyConnection, secid: str, cutoff: Any
) -> tuple[pd.DataFrame, dict[str, tuple[str, ...]]]:
    """Build state using only records available at or before cutoff."""
    frame = _price_panel(con, secid, cutoff)
    if frame.empty:
        return frame, dict(FAMILY_COLUMNS)
    frame = frame.join(_market_context(con, cutoff), how="left")
    frame = frame.join(_sector_context(con, secid, cutoff), how="left")
    macro, macro_groups = _macro_context(con, frame.index)
    frame = frame.join(macro, how="left")
    fundamentals, fundamental_names = _fundamental_context(con, secid, frame.index)
    frame = frame.join(fundamentals, how="left")
    families = dict(FAMILY_COLUMNS)
    families["rates"] = macro_groups["rates"]
    families["fx_commodities"] = macro_groups["fx_commodities"]
    families["fundamental"] = fundamental_names
    return frame, families


def family_distances(
    history: pd.DataFrame, current: pd.Series, families: dict[str, tuple[str, ...]]
) -> pd.DataFrame:
    """Robust train-only distances; missing families remain missing."""
    result = pd.DataFrame(index=history.index)
    for family, configured in families.items():
        columns = [
            name
            for name in configured
            if name in history and name in current.index and pd.notna(current[name])
        ]
        columns = [name for name in columns if history[name].notna().sum() >= 20]
        if not columns:
            result[family] = np.nan
            continue
        train = history[columns].astype(float)
        median = train.median()
        scale = (train.quantile(0.75) - train.quantile(0.25)).replace(0, np.nan)
        usable = scale.dropna().index.tolist()
        if not usable:
            result[family] = np.nan
            continue
        normalized = (train[usable] - median[usable]) / scale[usable]
        point = (current[usable].astype(float) - median[usable]) / scale[usable]
        result[family] = np.sqrt(((normalized - point) ** 2).mean(axis=1, skipna=True))
    return result


def similarity_score(distance: Any) -> Any:
    """Monotonic 0..100 mapping; supports scalar and pandas objects."""
    return 100.0 * np.exp(-np.maximum(distance, 0))


def _total_distance(distances: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    numerator = pd.Series(0.0, index=distances.index)
    denominator = pd.Series(0.0, index=distances.index)
    for family, weight in weights.items():
        if family not in distances:
            continue
        present = distances[family].notna()
        numerator.loc[present] += distances.loc[present, family] * float(weight)
        denominator.loc[present] += float(weight)
    return numerator.div(denominator.replace(0, np.nan))


def _compatibility(current: str | None, historical: str | None) -> float:
    if not current or not historical:
        return 0.75
    if current == historical:
        return 1.0
    stress = {"stress", "crisis"}
    if (current in stress) != (historical in stress):
        return 0.25
    return 0.70


def _episode_representatives(ranked: pd.DataFrame, separation: int) -> dict[pd.Timestamp, pd.Timestamp]:
    representatives: list[pd.Timestamp] = []
    mapping: dict[pd.Timestamp, pd.Timestamp] = {}
    positions = {date: position for position, date in enumerate(ranked.sort_index().index)}
    for date in ranked.sort_values(["total_distance", "date_tiebreak"], kind="mergesort").index:
        nearby = [rep for rep in representatives if abs(positions[date] - positions[rep]) < separation]
        if nearby:
            mapping[date] = min(nearby, key=lambda rep: (abs(positions[date] - positions[rep]), rep))
        else:
            representatives.append(date)
            mapping[date] = date
    return mapping


def _eligibility(score: float, compatibility: float, thresholds: dict[str, float]) -> tuple[str, str | None]:
    if compatibility <= 0.25:
        return "STRESS_ONLY", "incompatible stress regime retained outside central forecast"
    if score >= thresholds["strong"]:
        return "STRONG", None
    if score >= thresholds["medium"]:
        return "MEDIUM", None
    if score >= thresholds["weak"]:
        return "WEAK", None
    return "REJECTED", "conditional similarity below frozen research threshold"


def build_conditional_similarity(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    config, config_signature = _config()
    cutoff = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    fingerprint = con.execute(
        "SELECT count(*),sum(close) FROM canonical_daily_prices WHERE trade_date<=?", [cutoff]
    ).fetchone()
    run_id = hashlib.sha256(
        f"{SIMILARITY_VERSION}|{FEATURE_VERSION}|{cutoff}|{fingerprint}|{config_signature}".encode()
    ).hexdigest()[:20]
    existing = con.execute(
        "SELECT candidates,accepted,status FROM conditional_similarity_runs WHERE run_id=?", [run_id]
    ).fetchone()
    if existing:
        return {
            "run_id": run_id,
            "candidates": existing[0],
            "accepted": existing[1],
            "status": existing[2],
            "idempotent": True,
        }
    diagnostics: list[list[Any]] = []
    coverage_rows: list[list[Any]] = []
    minimum_history = int(config["minimum_history"])
    for secid in SECIDS:
        frame, families = build_state_panel(con, secid, cutoff)
        if len(frame) < minimum_history + 250:
            continue
        current = frame.iloc[-1]
        history = frame.iloc[:-250].copy()
        distances = family_distances(history, current, families)
        total = _total_distance(distances, config["family_weights"])
        ranked = distances.copy()
        ranked["total_distance"] = total
        ranked = ranked.dropna(subset=["total_distance"])
        ranked["date_tiebreak"] = ranked.index
        episodes = _episode_representatives(ranked, int(config["episode_separation_sessions"]))
        current_regime = current.get("market_state_label")
        for family, columns in families.items():
            columns = [name for name in columns if name in frame]
            valid = frame[columns].notna().any(axis=1) if columns else pd.Series(False, index=frame.index)
            dates = frame.index[valid]
            coverage_rows.append([
                run_id, secid, family, len(columns), float(valid.mean()), int((~valid).sum()),
                dates.min().date() if len(dates) else None, dates.max().date() if len(dates) else None, True,
            ])
        for analog_date, row in ranked.iterrows():
            representative = episodes[analog_date]
            score = float(similarity_score(row.total_distance))
            compatibility = _compatibility(current_regime, history.loc[analog_date].get("market_state_label"))
            eligibility, reason = _eligibility(score, compatibility, config["thresholds"])
            if representative != analog_date and eligibility not in {"REJECTED", "STRESS_ONLY"}:
                eligibility, reason = "REJECTED", "neighboring date belongs to the same historical episode"
            available = [family for family in families if pd.notna(row.get(family))]
            missing = [family for family in families if family not in available]
            family_scores = {family: float(similarity_score(row[family])) for family in available}
            diagnostics.append([
                run_id, secid, analog_date.date(), representative.date(),
                hashlib.sha1(f"{secid}|{representative.date()}".encode()).hexdigest()[:16],
                float(row.total_distance), score,
                *[
                    family_scores.get(name)
                    for name in (
                        "price", "volatility", "market", "rates", "fx_commodities", "sector",
                        "fundamental",
                    )
                ],
                compatibility, eligibility, reason, json.dumps(available), json.dumps(missing),
                json.dumps(family_scores, sort_keys=True), history.index[-1].date(), True,
            ])
    columns = (
        "run_id,secid,analog_date,representative_date,episode_id,total_distance,total_similarity,"
        "price_similarity,volatility_similarity,market_similarity,rates_similarity,"
        "fx_commodities_similarity,sector_similarity,fundamental_similarity,regime_compatibility,"
        "eligibility,rejection_reason,available_families_json,missing_families_json,"
        "family_breakdown_json,history_end,immutable"
    )
    placeholders = ",".join("?" for _ in columns.split(","))
    if diagnostics:
        con.executemany(
            f"INSERT INTO conditional_analog_diagnostics ({columns}) VALUES ({placeholders})",
            diagnostics,
        )
    if coverage_rows:
        con.executemany(
            """INSERT INTO conditional_state_coverage
            (run_id,secid,family,feature_count,coverage,missing_count,first_valid_date,last_valid_date,immutable)
            VALUES (?,?,?,?,?,?,?,?,?)""", coverage_rows,
        )
    accepted = sum(row[15] in {"STRONG", "MEDIUM", "WEAK"} for row in diagnostics)
    details = {
        "thresholds": config["thresholds"],
        "family_weights": config["family_weights"],
        "top_n_forced": False,
    }
    con.execute(
        """INSERT INTO conditional_similarity_runs
        (run_id,created_at,cutoff,feature_version,similarity_version,config_signature,instruments,
        candidates,accepted,immutable,production_unchanged,probability_gate_unchanged,status,details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, TRUE, TRUE, 'completed', ?)""",
        [run_id, datetime.now(UTC), cutoff, FEATURE_VERSION, SIMILARITY_VERSION, config_signature,
         len(SECIDS), len(diagnostics), accepted, json.dumps(details, sort_keys=True)],
    )
    return {
        "run_id": run_id,
        "candidates": len(diagnostics),
        "accepted": accepted,
        "status": "completed",
        "idempotent": False,
    }
