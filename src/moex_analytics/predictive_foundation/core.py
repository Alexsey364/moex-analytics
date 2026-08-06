"""Official-source predictive data catalog, ingestion and compact PIT features."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import date, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from moex_analytics.moex_client import MoexClient

from .schema import DDL

VERSION = "predictive-foundation-v1"
MOSCOW = ZoneInfo("Europe/Moscow")
ELIGIBILITY = {
    "production_candidate",
    "experimental",
    "informational_only",
    "rejected",
    "unavailable",
    "requires_paid_source",
}

SOURCE_MATRIX = (
    (
        "moex_equities",
        "market",
        "shares_eod",
        "MOEX shares EOD",
        "Cross-sectional market state",
        "MOEX ISS",
        "engines/stock/markets/shares/boards/TQBR/securities.json",
        "all shares",
        "daily",
        "production_candidate",
        "Current board list is not historical membership",
    ),
    (
        "moex_history",
        "market",
        "shares_history",
        "MOEX share history",
        "Historical prices and liquidity",
        "MOEX ISS",
        "history/engines/stock/markets/shares",
        "all discovered shares",
        "daily",
        "production_candidate",
        "Board changes remain explicit",
    ),
    (
        "moex_indices",
        "indices",
        "index_values",
        "MOEX official indices",
        "Market and sector benchmarks",
        "MOEX ISS",
        "history/engines/stock/markets/index",
        "IMOEX/RUBMI/MOEXFN/sector indices",
        "daily",
        "production_candidate",
        "Index methodology changes must be documented",
    ),
    (
        "moex_index_weights",
        "indices",
        "index_membership",
        "Historical index weights",
        "Point-in-time investable universe",
        "MOEX Index Data",
        "subscription index reports",
        "MOEX indices",
        "quarterly",
        "requires_paid_source",
        "CSV/XML constituents and weights require registration/subscription",
    ),
    (
        "moex_futures",
        "derivatives",
        "futures_eod",
        "MOEX futures EOD",
        "Basis, OI and expectations",
        "MOEX ISS",
        "engines/futures/markets/forts/securities.json",
        "SBER/IMOEX/RTS/rates/FX",
        "daily",
        "experimental",
        "Contract rolls and liquidity filtering required",
    ),
    (
        "moex_options",
        "derivatives",
        "options_eod",
        "MOEX options EOD",
        "Implied volatility and skew",
        "MOEX ISS",
        "engines/futures/markets/options/securities.json",
        "SBER and indices",
        "daily",
        "experimental",
        "Sparse quotes and arbitrage bounds",
    ),
    (
        "moex_ticks",
        "microstructure",
        "ticks_orderbook",
        "Historical ticks and order book",
        "Execution liquidity",
        "MOEX market-data products",
        "historical order log products",
        "SBER",
        "tick",
        "requires_paid_source",
        "Not reconstructed from candles",
    ),
    (
        "moex_candles",
        "microstructure",
        "intraday_candles",
        "MOEX ISS candles",
        "Intraday volatility and gaps",
        "MOEX ISS",
        "engines/stock/markets/shares/securities/SBER/candles",
        "SBER",
        "intraday",
        "experimental",
        "Available depth and session coverage require audit",
    ),
    (
        "cbr_curve",
        "rates",
        "zcyc",
        "CBR zero-coupon yield curve",
        "Rate level, slope and curvature",
        "Bank of Russia",
        "https://www.cbr.ru/hd_base/zcyc_params/",
        "OFZ curve",
        "daily",
        "production_candidate",
        "Known only after official publication",
    ),
    (
        "cbr_ruonia",
        "rates",
        "ruonia",
        "RUONIA",
        "Realized overnight funding rate",
        "Bank of Russia",
        "official RUONIA history",
        "RUONIA",
        "daily",
        "production_candidate",
        "Publication lag must be applied",
    ),
    (
        "moex_rusfar",
        "rates",
        "rusfar",
        "RUSFAR",
        "Market funding conditions",
        "MOEX ISS",
        "stock/index RUSFAR history",
        "RUSFAR",
        "daily",
        "production_candidate",
        "Index methodology epochs",
    ),
    (
        "moex_ofz",
        "rates",
        "ofz_indices",
        "MOEX government bond indices",
        "Duration and bond risk appetite",
        "MOEX ISS",
        "history stock/index",
        "RGBI and maturity indices",
        "daily",
        "production_candidate",
        "Index values, not constituent reconstruction",
    ),
    (
        "cbr_fx",
        "fx",
        "official_fx",
        "CBR official exchange rates",
        "Official currency reference",
        "Bank of Russia",
        "XML_dynamic",
        "USD/EUR/CNY",
        "daily",
        "production_candidate",
        "Not merged with traded spot",
    ),
    (
        "moex_fx",
        "fx",
        "traded_fx",
        "MOEX traded FX",
        "Ruble market regime",
        "MOEX ISS",
        "history/engines/currency",
        "CNYRUB and historical USDRUB",
        "daily",
        "production_candidate",
        "Instrument disappearance is a structural break",
    ),
    (
        "moex_fx_futures",
        "fx",
        "fx_futures",
        "MOEX FX futures",
        "Currency expectations and basis",
        "MOEX ISS",
        "futures FORTS",
        "currency contracts",
        "daily",
        "experimental",
        "Explicit roll required",
    ),
    (
        "moex_commodities",
        "commodities",
        "commodity_futures",
        "MOEX commodity futures",
        "Commodity proxies",
        "MOEX ISS",
        "futures FORTS",
        "oil/gas/gold/silver",
        "daily",
        "experimental",
        "Proxy differs from physical benchmark",
    ),
    (
        "global_indices",
        "global",
        "global_risk",
        "Professional global market history",
        "External risk regime",
        "licensed vendor",
        "vendor API",
        "global indices/rates/volatility",
        "daily",
        "requires_paid_source",
        "No unlicensed aggregator substitution",
    ),
    (
        "cbr_banks",
        "banking",
        "sector_stats",
        "Russian banking-sector statistics",
        "Sector balance sheet and risk",
        "Bank of Russia",
        "banking_sector statistics",
        "banking sector",
        "monthly",
        "production_candidate",
        "Publication lag and revisions",
    ),
    (
        "sber_ir",
        "corporate",
        "sber_reports",
        "Sber official reports",
        "Corporate fundamentals and guidance",
        "Sber IR",
        "reports-and-publications",
        "SBER",
        "monthly/quarterly",
        "production_candidate",
        "IFRS requires document/table validation",
    ),
    (
        "moex_turnover",
        "liquidity",
        "market_turnover",
        "MOEX market turnover",
        "Equity/bond/money-market rotation",
        "MOEX",
        "official market statistics",
        "markets",
        "monthly",
        "production_candidate",
        "Frequency kept monthly",
    ),
    (
        "cbr_retail",
        "liquidity",
        "retail_activity",
        "CBR household market participation",
        "Retail activity",
        "Bank of Russia",
        "official reviews",
        "households",
        "monthly/quarterly",
        "informational_only",
        "Publication lag and changing definitions",
    ),
    (
        "fund_flows",
        "liquidity",
        "professional_flows",
        "Historical fund flows",
        "Capital allocation",
        "licensed vendor",
        "vendor API",
        "funds",
        "daily/monthly",
        "requires_paid_source",
        "Price change is not a flow proxy",
    ),
    (
        "consensus",
        "expectations",
        "historical_consensus",
        "Historical analyst consensus",
        "Expected earnings/dividends",
        "licensed vendor",
        "vendor API",
        "SBER",
        "event-time",
        "requires_paid_source",
        "Current consensus cannot backfill history",
    ),
    (
        "news",
        "expectations",
        "timestamped_news",
        "Licensed timestamped news",
        "Information arrival",
        "licensed vendor",
        "vendor API",
        "SBER/market",
        "event-time",
        "requires_paid_source",
        "Not used as a fact without licensed archive",
    ),
)


def ensure_schema(con):
    con.execute(DDL)


def build_catalog(con):
    ensure_schema(con)
    now = datetime.now()
    for (
        dataset,
        category,
        series,
        name,
        meaning,
        source,
        endpoint,
        instrument,
        frequency,
        eligibility,
        limitation,
    ) in SOURCE_MATRIX:
        if eligibility not in ELIGIBILITY:
            raise ValueError(eligibility)
        pit = "safe_with_release_cutoff" if eligibility == "production_candidate" else "requires_validation"
        ingestion = "catalogued" if eligibility != "requires_paid_source" else "blocked_by_license"
        con.execute(
            """INSERT OR REPLACE INTO predictive_data_catalog VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                dataset,
                category,
                series,
                name,
                meaning,
                source,
                endpoint,
                instrument,
                frequency,
                "Europe/Moscow",
                None,
                None,
                "source-specific",
                "official publication or EOD cutoff",
                category in {"rates", "banking", "corporate"},
                "official_or_documented",
                pit,
                ingestion,
                eligibility,
                limitation,
                now,
            ],
        )
    return {
        "datasets": len(SOURCE_MATRIX),
        "paid_or_blocked": sum(row[9] == "requires_paid_source" for row in SOURCE_MATRIX),
    }


def _rows(block):
    return [dict(zip(block["columns"], row, strict=True)) for row in block["data"]]


def discover_market_universe(con, client=None):
    ensure_schema(con)
    client = client or MoexClient()
    payload = client.get_json(
        "engines/stock/markets/shares/boards/TQBR/securities.json",
        {"iss.meta": "off", "iss.only": "securities,marketdata"},
    )
    securities = _rows(payload["securities"])
    market = {row["SECID"]: row for row in _rows(payload["marketdata"])}
    source = f"{client.base_url}/engines/stock/markets/shares/boards/TQBR/securities.json"
    now = datetime.now(MOSCOW)
    for row in securities:
        secid = row["SECID"]
        quote = market.get(secid, {})
        con.execute(
            """INSERT OR REPLACE INTO predictive_market_universe VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                secid,
                secid,
                row.get("BOARDID", "TQBR"),
                None,
                None,
                row.get("ISSUESIZEPLACEDATE"),
                None,
                bool(row.get("STATUS") == "A"),
                quote.get("VALTODAY") or quote.get("VALUE"),
                quote.get("ISSUECAPITALIZATION"),
                "current_universe_only",
                source,
                now,
            ],
        )
    return {
        "instruments": len(securities),
        "source": source,
        "survivorship_control": "current universe marked; inactive archive remains required",
    }


def select_liquid_universe(con, limit=40):
    return [
        row[0]
        for row in con.execute(
            """SELECT source_secid FROM predictive_market_universe
        WHERE is_traded ORDER BY coalesce(liquidity_value,0) DESC LIMIT ?""",
            [limit],
        ).fetchall()
    ]


def ingest_history(con, secids, client=None, date_from="2011-01-01", date_to=None):
    ensure_schema(con)
    client = client or MoexClient()
    date_to = date_to or date.today().isoformat()
    before = con.execute("SELECT count(*) FROM predictive_market_prices").fetchone()[0]
    failures = {}
    for secid in secids:
        instrument = {
            "source_secid": secid,
            "engine": "stock",
            "market": "shares",
            "board": "TQBR",
        }
        try:
            incoming = []
            for payload, _, source in client.history_pages(instrument, date_from, date_to):
                for row in _rows(payload["history"]):
                    if row.get("TRADEDATE") and row.get("CLOSE") is not None:
                        available = datetime.combine(
                            date.fromisoformat(row["TRADEDATE"]), clock_time(19, 0), MOSCOW
                        )
                        incoming.append(
                            [
                                row["TRADEDATE"],
                                secid,
                                "TQBR",
                                row.get("OPEN"),
                                row.get("HIGH"),
                                row.get("LOW"),
                                row.get("CLOSE"),
                                row.get("VOLUME"),
                                row.get("VALUE"),
                                row.get("NUMTRADES"),
                                available,
                                source,
                            ]
                        )
            if incoming:
                con.executemany(
                    """INSERT OR REPLACE INTO predictive_market_prices VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    incoming,
                )
        except Exception as exc:
            failures[secid] = str(exc)
    after = con.execute("SELECT count(*) FROM predictive_market_prices").fetchone()[0]
    return {"requested": len(secids), "rows_written": after - before, "failures": failures}


def breadth_frame(prices, sectors=None):
    frame = prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["secid", "trade_date"])
    frame["return"] = frame.groupby("secid")["close"].pct_change(fill_method=None)
    for window in (20, 50, 200, 250, 60):
        rolling = frame.groupby("secid")["close"].transform(
            lambda value, w=window: value.rolling(w, min_periods=w).mean()
        )
        if window in (20, 50, 200):
            frame[f"above_{window}"] = frame["close"] > rolling
        high = frame.groupby("secid")["close"].transform(
            lambda value, w=window: value.rolling(w, min_periods=w).max()
        )
        low = frame.groupby("secid")["close"].transform(
            lambda value, w=window: value.rolling(w, min_periods=w).min()
        )
        if window in (20, 60, 250):
            frame[f"high_{window}"] = frame["close"] >= high
            frame[f"low_{window}"] = frame["close"] <= low
    frame["drawdown"] = frame["close"] / frame.groupby("secid")["close"].cummax() - 1
    if sectors is not None:
        frame = frame.merge(sectors, on="secid", how="left")
    records, ad_line = [], 0.0
    for trade_date, group in frame.groupby("trade_date"):
        valid = group.dropna(subset=["return"])
        advancing, declining = int((valid["return"] > 0).sum()), int((valid["return"] < 0).sum())
        ad_line += advancing - declining
        weights = valid["value"].fillna(0).clip(lower=0)
        cap_weight = float(np.average(valid["return"], weights=weights)) if weights.sum() else None
        records.append(
            {
                "trade_date": trade_date.date(),
                "universe_size": len(valid),
                "advancing": advancing,
                "declining": declining,
                "advance_decline_ratio": advancing / declining if declining else None,
                "advance_decline_line": ad_line,
                "above_sma20": float(group["above_20"].mean()),
                "above_sma50": float(group["above_50"].mean()),
                "above_sma200": float(group["above_200"].mean()),
                "new_high_20": int(group["high_20"].sum()),
                "new_low_20": int(group["low_20"].sum()),
                "new_high_60": int(group["high_60"].sum()),
                "new_low_60": int(group["low_60"].sum()),
                "new_high_250": int(group["high_250"].sum()),
                "new_low_250": int(group["low_250"].sum()),
                "median_return": float(valid["return"].median()) if len(valid) else None,
                "equal_weight_return": float(valid["return"].mean()) if len(valid) else None,
                "cap_weight_return": cap_weight,
                "cross_sectional_volatility": float(valid["return"].std()) if len(valid) > 1 else None,
                "strong_drawdown_share": float((group["drawdown"] <= -0.20).mean()),
                "financial_breadth": float(
                    group.loc[group.get("sector", "") == "financial", "above_50"].mean()
                )
                if "sector" in group
                else None,
            }
        )
    return pd.DataFrame(records)


def build_breadth(con):
    ensure_schema(con)
    prices = con.execute(
        "SELECT trade_date,secid,close,value FROM predictive_market_prices ORDER BY 1,2"
    ).fetchdf()
    if prices.empty:
        return {"status": "insufficient_data", "rows": 0}
    output = breadth_frame(prices)
    con.execute("DELETE FROM predictive_market_breadth WHERE calculation_version=?", [VERSION])
    for row in output.itertuples(index=False):
        con.execute(
            """INSERT INTO predictive_market_breadth VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
            [*row, VERSION],
        )
    return {
        "status": "success",
        "rows": len(output),
        "first_date": str(output.trade_date.min()),
        "last_date": str(output.trade_date.max()),
        "maximum_universe": int(output.universe_size.max()),
    }


def build_relative_state(con):
    ensure_schema(con)
    prices = con.execute(
        """SELECT trade_date,canonical_secid,close FROM canonical_daily_prices
        WHERE canonical_secid IN ('SBER','IMOEX','MOEXFN') ORDER BY 1"""
    ).fetchdf()
    if prices.empty:
        return {"status": "insufficient_data", "rows": 0}
    wide = prices.pivot(index="trade_date", columns="canonical_secid", values="close").sort_index()
    returns = wide.pct_change(fill_method=None)
    breadth = con.execute("SELECT trade_date,equal_weight_return FROM predictive_market_breadth").fetchdf()
    equal = (
        breadth.set_index("trade_date")["equal_weight_return"]
        if not breadth.empty
        else pd.Series(dtype=float)
    )
    con.execute("DELETE FROM sber_relative_market_state WHERE calculation_version=?", [VERSION])
    rows = 0
    for index in range(len(wide)):
        day = wide.index[index]
        sber = returns["SBER"].iloc[index] if "SBER" in returns else np.nan
        imoex = returns["IMOEX"].iloc[index] if "IMOEX" in returns else np.nan
        finance = returns["MOEXFN"].iloc[index] if "MOEXFN" in returns else np.nan
        market = equal.get(day, np.nan)
        window = returns.iloc[max(0, index - 59) : index + 1]
        beta = alpha = residual = idio = np.nan
        if {"SBER", "IMOEX"}.issubset(window.columns):
            pair = window[["SBER", "IMOEX"]].dropna()
            if len(pair) >= 20 and pair["IMOEX"].var() > 0:
                beta = pair["SBER"].cov(pair["IMOEX"]) / pair["IMOEX"].var()
                alpha = pair["SBER"].mean() - beta * pair["IMOEX"].mean()
                residuals = pair["SBER"] - alpha - beta * pair["IMOEX"]
                residual, idio = residuals.iloc[-1], residuals.std() * np.sqrt(252)
        con.execute(
            "INSERT INTO sber_relative_market_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                day,
                _number(sber),
                _number(imoex),
                _number(finance),
                _number(market),
                _difference(sber, imoex),
                _difference(sber, finance),
                _difference(sber, market),
                _relative_momentum(wide, index),
                _relative_volatility(returns, index),
                _number(beta),
                _number(alpha),
                _number(residual),
                _number(idio),
                VERSION,
            ],
        )
        rows += 1
    return {"status": "success", "rows": rows}


def _number(value):
    return float(value) if value is not None and np.isfinite(value) else None


def _difference(left, right):
    return _number(left - right) if np.isfinite(left) and np.isfinite(right) else None


def _relative_momentum(wide, index):
    if index < 20 or not {"SBER", "IMOEX"}.issubset(wide.columns):
        return None
    return _number(
        wide["SBER"].iloc[index] / wide["SBER"].iloc[index - 20]
        - wide["IMOEX"].iloc[index] / wide["IMOEX"].iloc[index - 20]
    )


def _relative_volatility(returns, index):
    if not {"SBER", "IMOEX"}.issubset(returns.columns):
        return None
    window = returns.iloc[max(0, index - 59) : index + 1]
    return _number(window["SBER"].std() / window["IMOEX"].std()) if window["IMOEX"].std() else None


def discover_derivatives(con, client=None):
    ensure_schema(con)
    client = client or MoexClient()
    payload = client.get_json(
        "engines/futures/markets/forts/securities.json",
        {"iss.meta": "off", "iss.only": "securities"},
    )
    rows = _rows(payload["securities"])
    selected = []
    for row in rows:
        text = " ".join(str(row.get(key, "")) for key in ("SECID", "SHORTNAME", "ASSETCODE"))
        if any(token in text.upper() for token in ("SBER", "IMOEX", "RTS", "RUSFAR")):
            selected.append(row)
            con.execute(
                """INSERT OR REPLACE INTO predictive_derivative_instruments VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    row.get("SECID"),
                    row.get("SHORTNAME"),
                    row.get("ASSETCODE"),
                    "futures",
                    row.get("LASTTRADEDATE"),
                    row.get("ASSETCODE"),
                    row.get("BOARDID"),
                    bool(row.get("IS_TRADED", row.get("STATUS") == "A")),
                    "MOEX ISS",
                    datetime.now(),
                    "experimental",
                    "History and roll validation required",
                ],
            )
    return {"discovered": len(selected), "all_forts_instruments": len(rows)}


def futures_basis(future, spot, days):
    if spot <= 0 or days <= 0:
        raise ValueError("Positive spot and days to expiry required")
    basis = future / spot - 1
    return basis, basis * 365 / days


def option_arbitrage_valid(price, spot, strike, option_type):
    if min(price, spot, strike) < 0:
        return False
    lower = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    upper = spot if option_type == "call" else strike
    return lower <= price <= upper


def implied_volatility(price, spot, strike, years, rate, option_type="call"):
    if not option_arbitrage_valid(price, spot, strike, option_type) or years <= 0:
        return None
    low, high = 1e-6, 5.0
    for _ in range(100):
        sigma = (low + high) / 2
        estimate = _black_scholes(spot, strike, years, rate, sigma, option_type)
        if estimate > price:
            high = sigma
        else:
            low = sigma
    return (low + high) / 2


def _black_scholes(spot, strike, years, rate, sigma, option_type):
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)

    def normal(value):
        return 0.5 * (1 + math.erf(value / math.sqrt(2)))

    call = spot * normal(d1) - strike * math.exp(-rate * years) * normal(d2)
    return call if option_type == "call" else call - spot + strike * math.exp(-rate * years)


def yield_curve_features(tenors, yields):
    points = dict(zip(tenors, yields, strict=True))
    short = min(points, key=lambda value: abs(value - 1))
    medium = min(points, key=lambda value: abs(value - 5))
    long = min(points, key=lambda value: abs(value - 10))
    slope = points[long] - points[short]
    curvature = 2 * points[medium] - points[short] - points[long]
    return {
        "level": statistics.mean(points.values()),
        "slope": slope,
        "curvature": curvature,
        "inverted": slope < 0,
    }


def split_session(timestamp):
    local = timestamp.astimezone(MOSCOW)
    value = local.time()
    if value < clock_time(9, 50):
        return "pre_market"
    if value <= clock_time(18, 50):
        return "main"
    if value <= clock_time(23, 50):
        return "evening"
    return "overnight"


def align_publication(observation_date, publication_timestamp, trade_cutoff):
    del observation_date
    return publication_timestamp <= trade_cutoff


def detect_structural_regimes(con):
    ensure_schema(con)
    rows = con.execute(
        """SELECT trade_date,close FROM canonical_daily_prices
        WHERE canonical_secid='IMOEX' ORDER BY trade_date"""
    ).fetchall()
    if len(rows) < 250:
        return {"status": "insufficient_data", "rows": 0}
    close = pd.Series([row[1] for row in rows], index=[row[0] for row in rows], dtype=float)
    volatility = close.pct_change(fill_method=None).rolling(60).std() * np.sqrt(252)
    drawdown = close / close.cummax() - 1
    regimes = np.where(
        (drawdown < -0.20) & (volatility > volatility.expanding().median()),
        "high-correlation selloff",
        np.where(close > close.rolling(200).mean(), "broad risk-on", "indeterminate"),
    )
    con.execute("DELETE FROM structural_regimes WHERE calculation_version=?", [VERSION])
    changes = pd.Series(regimes, index=close.index)
    groups = changes.ne(changes.shift()).cumsum()
    count = 0
    for _, group in changes.groupby(groups):
        regime = group.iloc[0]
        evidence = {
            "imoex_drawdown": _number(drawdown.loc[group.index[-1]]),
            "imoex_volatility60": _number(volatility.loc[group.index[-1]]),
        }
        rid = hashlib.sha256(f"{group.index[0]}|{regime}".encode()).hexdigest()[:20]
        con.execute(
            "INSERT INTO structural_regimes VALUES (?,?,?,?,?,?,?,?)",
            [
                rid,
                group.index[0],
                group.index[-1],
                regime,
                json.dumps(evidence),
                True,
                "data_detected_market_regime",
                VERSION,
            ],
        )
        count += 1
    return {"status": "success", "rows": count}


def build_feature_families(con):
    ensure_schema(con)
    families = {
        "market_trend": (
            ["IMOEX", "RUBMI"],
            ["returns", "SMA"],
            "Broad market direction",
            "production_candidate",
        ),
        "market_breadth": (
            ["predictive_market_prices"],
            ["advance_decline", "above_SMA"],
            "Participation",
            "experimental",
        ),
        "sector_relative": (
            ["MOEXFN", "SBER"],
            ["relative_return", "rolling_beta"],
            "Bank-specific movement",
            "production_candidate",
        ),
        "futures_expectations": (
            ["SBER futures"],
            ["basis", "open_interest"],
            "Derivative expectations",
            "experimental",
        ),
        "options_expectations": (["SBER options"], ["ATM_IV", "skew"], "Option-implied risk", "experimental"),
        "rates": (
            ["CBR ZCYC", "RUSFAR", "RGBI"],
            ["level", "slope", "shock"],
            "Bank rate environment",
            "production_candidate",
        ),
        "fx": (["CNYRUB", "official FX"], ["return", "volatility"], "Ruble stress", "production_candidate"),
        "liquidity_flows": (
            ["MOEX turnover"],
            ["rotation", "concentration"],
            "Risk appetite",
            "experimental",
        ),
        "sber_corporate": (
            ["validated Sber reports"],
            ["valuation", "operating"],
            "Corporate state",
            "experimental",
        ),
        "calendar": (["trading calendar"], ["month_end", "expiry"], "Scheduled effects", "experimental"),
        "structural_regime": (
            ["structural_regimes"],
            ["regime_one_hot"],
            "Comparability epochs",
            "production_candidate",
        ),
    }
    for family, (raw, transforms, meaning, eligibility) in families.items():
        con.execute(
            "INSERT OR REPLACE INTO predictive_feature_families VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                family,
                json.dumps(raw),
                json.dumps(transforms),
                meaning,
                json.dumps([1, 5, 20, 60, 120, 250]),
                "point_in_time_required",
                None,
                "not_yet_validated",
                "no_assumed_sign",
                eligibility,
            ],
        )
    return {"families": len(families)}


def audit_coverage(con):
    ensure_schema(con)
    con.execute("DELETE FROM predictive_coverage_audit")
    sources = [
        ("market_prices", row[0], row[1], row[2], row[3])
        for row in con.execute(
            """SELECT secid,min(trade_date),max(trade_date),count(*)
            FROM predictive_market_prices GROUP BY secid"""
        ).fetchall()
    ]
    for dataset, series, start, end, count in sources:
        expected = max(1, (end - start).days * 5 / 7)
        missingness = max(0.0, 1 - count / expected)
        con.execute(
            """INSERT INTO predictive_coverage_audit VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
            [
                dataset,
                series,
                start,
                end,
                count,
                "daily",
                missingness,
                0,
                "EOD",
                json.dumps([]),
                0,
                0,
                "Europe/Moscow",
                "safe_after_close",
                json.dumps([1, 5, 20, 60, 120, 250]),
            ],
        )
    return {"series": len(sources)}


def common_sample_ablation(base, blocks, target, horizons=(1, 5, 20, 60, 120, 250)):
    results = []
    frame = base.join([value.add_prefix(f"{name}_") for name, value in blocks.items()], how="inner")
    common = frame.join(target.rename("target"), how="inner").dropna()
    baseline = float((common["target"] > 0).mean())
    baseline_score = max(baseline, 1 - baseline)
    for horizon in horizons:
        for name, _block in blocks.items():
            columns = [column for column in common if column.startswith(f"{name}_")]
            signal = common[columns].mean(axis=1)
            prediction = signal.shift(horizon) > 0
            valid = prediction.notna()
            score = float((prediction[valid] == (common.loc[valid, "target"] > 0)).mean())
            improvement = score - baseline_score
            results.append(
                {
                    "horizon": horizon,
                    "block": name,
                    "common_sample": int(valid.sum()),
                    "baseline": baseline_score,
                    "score": score,
                    "improvement": improvement,
                    "status": "experimental" if improvement > 0 else "rejected",
                }
            )
    return results


def lead_lag(left, right, max_lag=5):
    aligned = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
    return {
        lag: float(aligned["left"].shift(lag).corr(aligned["right"])) for lag in range(-max_lag, max_lag + 1)
    }


def status(con):
    ensure_schema(con)
    return {
        "catalog": con.execute("SELECT count(*) FROM predictive_data_catalog").fetchone()[0],
        "universe": con.execute("SELECT count(*) FROM predictive_market_universe").fetchone()[0],
        "prices": con.execute("SELECT count(*) FROM predictive_market_prices").fetchone()[0],
        "breadth": con.execute("SELECT count(*) FROM predictive_market_breadth").fetchone()[0],
        "derivatives": con.execute("SELECT count(*) FROM predictive_derivative_instruments").fetchone()[0],
        "coverage": con.execute("SELECT count(*) FROM predictive_coverage_audit").fetchone()[0],
        "ablation": con.execute("SELECT count(*) FROM predictive_ablation_results").fetchone()[0],
    }


def update(con, client=None, universe_limit=40):
    started = time.perf_counter()
    catalog = build_catalog(con)
    universe = discover_market_universe(con, client)
    selected = select_liquid_universe(con, universe_limit)
    history = ingest_history(con, selected, client)
    breadth = build_breadth(con)
    relative = build_relative_state(con)
    derivatives = discover_derivatives(con, client)
    families = build_feature_families(con)
    regimes = detect_structural_regimes(con)
    coverage = audit_coverage(con)
    return {
        "catalog": catalog,
        "universe": universe,
        "selected": len(selected),
        "history": history,
        "breadth": breadth,
        "relative": relative,
        "derivatives": derivatives,
        "families": families,
        "regimes": regimes,
        "coverage": coverage,
        "duration_seconds": time.perf_counter() - started,
    }


def download_market_universe(con, client=None, limit=40):
    universe = discover_market_universe(con, client)
    selected = select_liquid_universe(con, limit)
    return {"universe": universe, "history": ingest_history(con, selected, client), "selected": len(selected)}


def index_history_status(con):
    rows = con.execute(
        """SELECT canonical_secid,min(trade_date),max(trade_date),count(*)
        FROM canonical_daily_prices WHERE canonical_secid IN
        ('IMOEX','RUBMI','MOEXFN','MOEXBC','RGBI') GROUP BY canonical_secid"""
    ).fetchall()
    return {
        "official_index_series": rows,
        "historical_membership": "requires_paid_source",
        "silent_reconstruction": False,
    }


def rates_market_status(con):
    macro = con.execute(
        """SELECT series_id,min(observation_date),max(observation_date),count(*)
        FROM macro_observations WHERE series_id LIKE '%rate%' OR series_id LIKE '%ruonia%'
        OR series_id LIKE '%rgbi%' OR series_id LIKE '%ofz%' GROUP BY series_id"""
    ).fetchall()
    curve = con.execute(
        "SELECT min(observation_date),max(observation_date),count(*) FROM predictive_yield_curve"
    ).fetchone()
    return {
        "existing_official_series": macro,
        "zcyc": curve,
        "zcyc_official_history_from": "2003-01-10",
        "publication_cutoff_required": True,
    }


def cross_market_status(con):
    rows = con.execute(
        """SELECT series_id,min(observation_date),max(observation_date),count(*)
        FROM macro_observations GROUP BY series_id ORDER BY series_id"""
    ).fetchall()
    return {"series": rows, "external_unlicensed_substitution": False}


def derivative_features_status(con):
    return {
        "instruments": con.execute("SELECT count(*) FROM predictive_derivative_instruments").fetchone()[0],
        "daily_rows": con.execute("SELECT count(*) FROM predictive_derivative_daily").fetchone()[0],
        "continuous_series": "not_built_without_validated_contract_history",
        "synthetic_roll": False,
    }


def ablate_blocks(con):
    ensure_schema(con)
    sber = con.execute(
        """SELECT trade_date,close FROM canonical_daily_prices
        WHERE canonical_secid='SBER' ORDER BY trade_date"""
    ).fetchdf()
    breadth = con.execute(
        """SELECT trade_date,equal_weight_return,above_sma50,cross_sectional_volatility
        FROM predictive_market_breadth ORDER BY trade_date"""
    ).fetchdf()
    relative = con.execute(
        """SELECT trade_date,sber_vs_imoex,relative_momentum_20,rolling_beta_60
        FROM sber_relative_market_state ORDER BY trade_date"""
    ).fetchdf()
    if sber.empty or breadth.empty:
        return {"status": "insufficient_data", "rows": 0}
    for frame in (sber, breadth, relative):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        frame.set_index("trade_date", inplace=True)
    target = sber["close"].pct_change(fill_method=None)
    base = sber[["close"]].pct_change(fill_method=None).rename(columns={"close": "technical"})
    blocks = {"breadth": breadth}
    if not relative.empty:
        blocks["sector_relative"] = relative
    results = common_sample_ablation(base, blocks, target)
    con.execute("DELETE FROM predictive_ablation_results WHERE calculation_version=?", [VERSION])
    for row in results:
        effective = row["common_sample"] / max(1, row["horizon"])
        con.execute(
            """INSERT INTO predictive_ablation_results VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                row["horizon"],
                row["block"],
                None,
                None,
                row["common_sample"],
                effective,
                row["baseline"],
                row["score"],
                row["improvement"],
                None,
                None,
                None,
                row["status"],
                json.dumps({"common_sample": True, "preliminary": True}),
                VERSION,
            ],
        )
    return {"status": "success", "rows": len(results), "results": results}


def build_lead_lag_diagnostics(con):
    relative = con.execute(
        """SELECT trade_date,sber_return,imoex_return,equal_market_return
        FROM sber_relative_market_state ORDER BY trade_date"""
    ).fetchdf()
    if relative.empty:
        return {"status": "insufficient_data", "rows": 0}
    con.execute("DELETE FROM predictive_lead_lag WHERE calculation_version=?", [VERSION])
    relative.set_index("trade_date", inplace=True)
    written = 0
    for name in ("imoex_return", "equal_market_return"):
        for lag, correlation in lead_lag(relative[name], relative["sber_return"]).items():
            con.execute(
                "INSERT INTO predictive_lead_lag VALUES (?,?,?,?,?,?)",
                [
                    f"{name}_to_sber",
                    lag,
                    correlation,
                    len(relative.dropna()),
                    "predictive diagnostic; not causality",
                    VERSION,
                ],
            )
            written += 1
    return {"status": "success", "rows": written, "causality_claimed": False}


def index_members_as_of(membership, as_of):
    rows = membership.copy()
    mask = (
        (pd.to_datetime(rows["effective_from"]).dt.date <= as_of)
        & (rows["effective_to"].isna() | (pd.to_datetime(rows["effective_to"]).dt.date >= as_of))
        & (pd.to_datetime(rows["available_from"]).dt.date <= as_of)
    )
    return rows.loc[mask].copy()


def choose_front_contract(contracts, as_of, roll_days=5):
    eligible = contracts[
        (pd.to_datetime(contracts["expiration_date"]).dt.date > as_of) & (contracts["is_traded"])
    ].sort_values("expiration_date")
    if eligible.empty:
        return None
    front = eligible.iloc[0]
    days = (pd.Timestamp(front["expiration_date"]).date() - as_of).days
    if days <= roll_days and len(eligible) > 1:
        front = eligible.iloc[1]
    return str(front["secid"])
