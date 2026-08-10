"""PIT-safe sector, commodity and macro context expansion (Stage 38)."""

from __future__ import annotations

import hashlib
import io
import json
import time
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.macro.models import Observation, SeriesDefinition
from moex_analytics.macro.repository import upsert_observations, upsert_series
from moex_analytics.macro.sources import moex

from .issuer_context import ISSUERS
from .issuer_evidence import HORIZONS, SECIDS, _evaluate
from .schema import DDL

FRED = {
    "fred_brent": ("DCOILBRENTEU", "Brent Europe spot price", "USD/barrel"),
    "fred_henry_hub_gas": ("DHHNGSP", "Henry Hub natural gas spot price", "USD/MMBtu"),
}
CONTEXT_EXPERIMENTS = {
    "market_only": ("asset_return_20", "asset_return_60", "market_return_20", "market_return_60"),
    "market_sector": ("sector_return_20", "sector_return_60", "issuer_relative_sector_60"),
    "market_fx": ("fx_return_20", "fx_volatility_60"),
    "market_rates": ("key_rate", "ruonia", "rusfar", "zcyc_level", "zcyc_slope", "rate_shock_20"),
    "market_commodity": ("brent_return_20", "brent_volatility_60", "gas_return_20"),
    "all_context": ("sector_return_20", "sector_return_60", "issuer_relative_sector_60",
                    "fx_return_20", "fx_volatility_60", "key_rate", "ruonia", "rusfar",
                    "zcyc_level", "zcyc_slope", "rate_shock_20", "brent_return_20",
                    "brent_volatility_60", "gas_return_20"),
}
BASE_FEATURES = CONTEXT_EXPERIMENTS["market_only"]


def _fred_download(series_id: str, session=requests) -> tuple[list[Observation], bytes]:
    fred_id, _, _ = FRED[series_id]
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}"
    response = session.get(url, timeout=20)
    response.raise_for_status()
    frame = pd.read_csv(io.BytesIO(response.content))
    value_column = next(name for name in frame.columns if name != "observation_date")
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    rows = []
    for row in frame.dropna(subset=[value_column]).itertuples(index=False):
        observed = pd.Timestamp(row[0]).date()
        available = datetime.combine(observed + timedelta(days=1), datetime.min.time(), UTC)
        rows.append(Observation(series_id, observed, observed, available, float(row[1]),
                                "fred-current-vintage", url))
    return rows, response.content


def _load_new_series(con, session=requests) -> tuple[int, int, dict[str, str]]:
    requests_count = rows_count = 0
    errors = {}
    new_sector_ids = ("moex_telecom", "moex_chemicals")
    definitions = [item for item in moex.definitions() if item.series_id in new_sector_ids]
    definitions.extend(SeriesDefinition(
        sid, name, unit, "business daily", "FRED", f"https://fred.stlouisfed.org/series/{fid}",
        None, "Conservatively available next calendar day", "current-vintage price series",
        True, "Public Federal Reserve redistribution; not an Urals/fertilizer substitute",
    ) for sid, (fid, name, unit) in FRED.items())
    upsert_series(con, definitions)
    for series_id in new_sector_ids:
        if con.execute(
            "SELECT count(*) FROM macro_observations WHERE series_id=?", [series_id]
        ).fetchone()[0]:
            continue
        try:
            rows = moex.download(series_id, "2004-01-01", str(date.today()))
            rows_count += len(rows)
            upsert_observations(con, rows)
        except requests.RequestException as exc:
            errors[series_id] = str(exc)
        requests_count += 1
    for series_id in FRED:
        try:
            rows, raw = _fred_download(series_id, session)
            digest = hashlib.sha256(raw).hexdigest()
            path = PROJECT_ROOT / "data" / "raw" / "context_expansion"
            path.mkdir(parents=True, exist_ok=True)
            (path / f"{series_id}_{digest}.csv").write_bytes(raw)
            rows_count += len(rows)
            upsert_observations(con, rows)
        except requests.RequestException as exc:
            errors[series_id] = str(exc)
        requests_count += 1
    return requests_count, rows_count, errors


def _series(con, series_id: str, prefix: str) -> pd.DataFrame:
    frame = con.execute(
        """SELECT observation_date,CAST(available_from AS DATE) available_date,value
        FROM macro_observations WHERE series_id=? ORDER BY observation_date""",
        [series_id],
    ).df()
    if frame.empty:
        columns = ["available_date", prefix]
        columns.extend(f"{prefix}_return_{horizon}" for horizon in (1, 5, 20, 60, 120, 250))
        columns.extend((f"{prefix}_volatility_60", f"{prefix}_drawdown"))
        return pd.DataFrame(columns=columns)
    frame[prefix] = frame.value
    for horizon in (1, 5, 20, 60, 120, 250):
        frame[f"{prefix}_return_{horizon}"] = frame.value.pct_change(horizon)
    frame[f"{prefix}_volatility_60"] = frame.value.pct_change().rolling(60).std()
    frame[f"{prefix}_drawdown"] = frame.value / frame.value.cummax() - 1
    return frame.drop(columns=["observation_date", "value"]).sort_values("available_date")


def _merge(frame: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    if other.empty:
        return frame
    frame = frame.drop(columns=["available_date"], errors="ignore")
    merged = pd.merge_asof(frame.sort_values("trade_date"), other,
                           left_on="trade_date", right_on="available_date", direction="backward")
    return merged.drop(columns=["available_date"], errors="ignore")


def _asset(con, secid: str) -> pd.DataFrame:
    frame = con.execute(
        """SELECT trade_date,close FROM moex_equity_eod WHERE secid=? AND close>0
        QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY value DESC NULLS LAST)=1
        ORDER BY trade_date""", [secid]
    ).df()
    for horizon in (20, 60):
        frame[f"asset_return_{horizon}"] = frame.close.pct_change(horizon)
    return frame


def _context_frame(con, secid: str) -> pd.DataFrame:
    issuer = next((group for group, (items, _) in ISSUERS.items() if secid in items), secid)
    sector_id = ISSUERS[issuer][1]
    frame = _asset(con, secid)
    frame = _merge(frame, _series(con, "moex_imoex", "market"))
    frame = _merge(frame, _series(con, sector_id, "sector"))
    frame = _merge(frame, _series(con, "cbr_usd_rub", "fx"))
    frame = _merge(frame, _series(con, "cbr_key_rate", "key_rate"))
    frame = _merge(frame, _series(con, "cbr_ruonia", "ruonia"))
    frame = _merge(frame, _series(con, "moex_rusfar", "rusfar"))
    frame = _merge(frame, _series(con, "fred_brent", "brent"))
    frame = _merge(frame, _series(con, "fred_henry_hub_gas", "gas"))
    frame = _merge(frame, _series(con, "moex_rvi", "rvi"))
    frame = _merge(frame, _series(con, "moex_rgbi", "rgbi"))
    zcyc = con.execute(
        """SELECT CAST(available_from AS DATE) available_date,
        (short_rate+long_rate)/2 zcyc_level,slope_10y_2y zcyc_slope,
        parallel_shift rate_shock_20 FROM zcyc_features ORDER BY available_date"""
    ).df()
    frame = _merge(frame, zcyc)
    frame["issuer_group"] = issuer
    frame["sector_series"] = sector_id
    frame["sector_relative_market_60"] = frame.sector_return_60 - frame.market_return_60
    frame["issuer_relative_sector_20"] = frame.asset_return_20 - frame.sector_return_20
    frame["issuer_relative_sector_60"] = frame.asset_return_60 - frame.sector_return_60
    return frame


def _store_context(con, run_id: str) -> tuple[int, int]:
    con.execute("DELETE FROM synchronized_predictive_context WHERE run_id=?", [run_id])
    frames = []
    for secid in SECIDS:
        frame = _context_frame(con, secid)
        frame["secid"] = secid
        frames.append(frame)
    all_rows = pd.concat(frames, ignore_index=True)
    output = pd.DataFrame({
        "run_id": run_id, "trade_date": all_rows.trade_date, "secid": all_rows.secid,
        "issuer_group": all_rows.issuer_group, "close": all_rows.close,
        "asset_return_20": all_rows.asset_return_20, "asset_return_60": all_rows.asset_return_60,
        "market_return_20": all_rows.market_return_20, "market_return_60": all_rows.market_return_60,
        "sector_series": all_rows.sector_series, "sector_return_20": all_rows.sector_return_20,
        "sector_return_60": all_rows.sector_return_60, "sector_return_120": all_rows.sector_return_120,
        "sector_return_250": all_rows.sector_return_250,
        "sector_volatility_60": all_rows.sector_volatility_60,
        "sector_drawdown": all_rows.sector_drawdown,
        "sector_relative_market_60": all_rows.sector_relative_market_60,
        "issuer_relative_sector_20": all_rows.issuer_relative_sector_20,
        "issuer_relative_sector_60": all_rows.issuer_relative_sector_60,
        "cbr_usd_rub": all_rows.fx, "fx_return_20": all_rows.fx_return_20,
        "fx_volatility_60": all_rows.fx_volatility_60, "key_rate": all_rows.key_rate,
        "ruonia": all_rows.ruonia, "rusfar": all_rows.rusfar,
        "zcyc_level": all_rows.zcyc_level, "zcyc_slope": all_rows.zcyc_slope,
        "rate_shock_20": all_rows.rate_shock_20, "brent": all_rows.brent,
        "brent_return_20": all_rows.brent_return_20,
        "brent_volatility_60": all_rows.brent_volatility_60, "gas": all_rows.gas,
        "gas_return_20": all_rows.gas_return_20, "rvi": all_rows.rvi, "rgbi": all_rows.rgbi,
        "breadth_balance": np.nan, "liquidity_percentile": np.nan,
        "context_available_from": pd.to_datetime(all_rows.trade_date) + pd.Timedelta(hours=23),
    })
    con.register("stage38_context_frame", output)
    con.execute("INSERT INTO synchronized_predictive_context SELECT * FROM stage38_context_frame")
    con.unregister("stage38_context_frame")
    exposures = 0
    for secid, frame in output.groupby("secid"):
        work = frame.sort_values("trade_date").copy()
        asset_return = work.close.pct_change()
        factors = {
            "market_beta": work.market_return_20.diff(),
            "sector_beta": work.sector_return_20.diff(),
            "fx_beta": work.fx_return_20.diff(),
            "rate_sensitivity": work.key_rate.diff(),
            "commodity_beta": work.brent_return_20.diff(),
        }
        values = {}
        for name, factor in factors.items():
            values[name] = asset_return.rolling(120).cov(factor) / factor.rolling(120).var()
        exp = pd.DataFrame({"run_id": run_id, "trade_date": work.trade_date, "secid": secid,
                            "rolling_window": 120, **values, "observations": 120,
                            "structural_break_score": values["market_beta"].diff(60).abs(),
                            "quality_status": "rolling_pit"})
        con.register("stage38_exposure_frame", exp)
        con.execute("INSERT INTO predictive_factor_exposures SELECT * FROM stage38_exposure_frame")
        con.unregister("stage38_exposure_frame")
        exposures += len(exp)
    return len(output), exposures


def _ablation(con, run_id: str) -> int:
    rows = 0
    for secid in SECIDS:
        frame = con.execute(
            "SELECT * FROM synchronized_predictive_context WHERE run_id=? AND secid=? ORDER BY trade_date",
            [run_id, secid],
        ).df()
        for horizon in HORIZONS:
            sample = frame.copy()
            sample["target"] = sample.close.shift(-horizon) / sample.close - 1
            baseline = _evaluate(sample, BASE_FEATURES)
            for experiment, extra in CONTEXT_EXPERIMENTS.items():
                features = BASE_FEATURES if experiment == "market_only" else BASE_FEATURES + extra
                result = _evaluate(sample, features)
                improvement = (
                    result.get("ba", 0) - baseline.get("ba", 0)
                    if result.get("ba") is not None and baseline.get("ba") is not None else None
                )
                status = "WEAK_EVIDENCE" if improvement is not None and improvement > 0 else "NO_EVIDENCE"
                con.execute(
                    """INSERT INTO predictive_context_ablation VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [run_id, secid, horizon, experiment, result["rows"], result["folds"],
                     result.get("ba"), improvement, result.get("low"), result.get("high"), status,
                     json.dumps({"features": result.get("features", []), "time_oos": True})],
                )
                rows += 1
    return rows


def _coverage(con, run_id: str) -> None:
    mappings = [
        ("sector", sid, "MOEX ISS", "official/free", None)
        for sid in ("moex_finance", "moex_oil_gas", "moex_telecom", "moex_consumer",
                    "moex_chemicals", "moex_power", "moex_metals", "moex_transport")
    ] + [
        ("commodity", "fred_brent", "FRED", "public", "not Urals"),
        ("commodity", "fred_henry_hub_gas", "FRED", "public", "not fertilizer price"),
        ("commodity", "urals", "not loaded", "requires_paid_data", "no reproducible free history"),
        ("commodity", "fertilizer_prices", "not loaded", "requires_paid_data", "no reliable free history"),
    ]
    for family, series, source, license_name, limitation in mappings:
        stats = con.execute(
            """SELECT count(*),min(observation_date),max(observation_date)
            FROM macro_observations WHERE series_id=?""",
            [series],
        ).fetchone()
        con.execute(
            """INSERT INTO predictive_context_coverage VALUES
            (?,?,?,?,?,?,?,?,?,?,?)""",
            [run_id, family, series, stats[0], stats[1], stats[2], source, license_name,
             "next-session_or_source_timestamp", "available" if stats[0] else "missing", limitation],
        )


def expand_predictive_context(con, *, session=requests) -> dict:
    con.execute(DDL)
    started = datetime.now(UTC)
    clock = time.perf_counter()
    run_id = hashlib.sha256(f"stage38:{started.isoformat()}".encode()).hexdigest()[:20]
    requests_count, downloaded_rows, source_errors = _load_new_series(con, session)
    synchronized, exposures = _store_context(con, run_id)
    ablations = _ablation(con, run_id)
    _coverage(con, run_id)
    counts = dict(con.execute(
        """SELECT CASE WHEN series_id LIKE 'cbr_%' OR series_id='moex_rusfar' THEN 'rates_fx'
        WHEN series_id LIKE 'fred_%' THEN 'commodity' ELSE 'sector' END context_family,count(*)
        FROM macro_observations GROUP BY 1"""
    ).fetchall())
    runtime = time.perf_counter() - clock
    details = {"raw_downloaded_rows": downloaded_rows, "future_information": False,
               "urals_status": "requires_paid_data", "fertilizer_status": "requires_paid_data",
               "cbr_and_moex_fx_spliced": False, "new_model_families": 0}
    details["source_errors"] = source_errors
    con.execute(
        """INSERT INTO predictive_context_runs VALUES
        (?,?,current_timestamp,'completed',?,?,?,?,?,?,?,?,?,0,?)""",
        [run_id, started, counts.get("sector", 0),
         con.execute("SELECT count(*) FROM macro_observations WHERE series_id LIKE '%rub'").fetchone()[0],
         counts.get("rates_fx", 0), counts.get("commodity", 0), synchronized, exposures,
         ablations, requests_count, runtime, json.dumps(details)],
    )
    return {"run_id": run_id, "synchronized_rows": synchronized,
            "factor_exposure_rows": exposures, "ablation_rows": ablations,
            "requests": requests_count, "runtime_seconds": runtime,
            "production_changes": 0, **details}


def predictive_context_status(con) -> dict:
    con.execute(DDL)
    return {"latest": con.execute(
        "SELECT * FROM predictive_context_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone(), "coverage": con.execute(
        """SELECT dataset_family,series_id,rows,earliest,latest,quality_status,limitation
        FROM predictive_context_coverage WHERE run_id=(SELECT run_id FROM predictive_context_runs
        ORDER BY started_at DESC LIMIT 1) ORDER BY 1,2"""
    ).fetchall()}
