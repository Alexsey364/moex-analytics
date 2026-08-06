"""Official critical-data loaders and point-in-time transformations."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from moex_analytics.moex_client import MoexClient

from .schema import DDL

MOSCOW = ZoneInfo("Europe/Moscow")
TENORS = (0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20)
IFRS_METRICS = (
    "net_profit",
    "equity",
    "assets",
    "roe",
    "nim",
    "cost_of_risk",
    "cost_to_income",
    "retail_loans",
    "corporate_loans",
    "client_funds",
    "provisions",
    "npl",
    "eps",
    "shares",
    "capital_adequacy",
)


def ensure_schema(con):
    con.execute(DDL)


def rows(block):
    return [dict(zip(block["columns"], values, strict=True)) for values in block.get("data", [])]


def interpolate_curve(points, tenors=TENORS):
    clean = sorted((float(x), float(y)) for x, y in points if x > 0 and 0 <= y <= 100)
    if len(clean) < 2:
        raise ValueError("ZCYC interpolation needs at least two valid points")
    x, y = zip(*clean, strict=True)
    return {float(t): float(np.interp(t, x, y)) for t in tenors}


def publication_available(observation_date, publication_date, decision_time):
    publication = datetime.combine(pd.Timestamp(publication_date).date(), time(19), MOSCOW)
    return pd.Timestamp(observation_date).date() <= publication.date() and decision_time >= publication


def point_in_time_membership(prices, lifecycles, min_liquidity=0.0, lookback=20):
    frame = prices.copy().sort_values(["secid", "trade_date"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    # shift is mandatory: today's liquidity cannot decide today's membership.
    frame["past_liquidity"] = frame.groupby("secid")["value"].transform(
        lambda s: s.shift(1).rolling(lookback, min_periods=1).median()
    )
    life = lifecycles.copy()
    life["history_from"] = pd.to_datetime(life["history_from"])
    life["history_to"] = pd.to_datetime(life["history_to"])
    merged = frame.merge(life, on=["secid", "board"], how="left")
    live = (merged.trade_date >= merged.history_from) & (
        merged.history_to.isna() | (merged.trade_date <= merged.history_to)
    )
    merged["eligible"] = live & merged.close.gt(0) & merged.past_liquidity.fillna(0).ge(min_liquidity)
    # One security per date even when boards overlap: primary, then liquidity.
    merged = merged.sort_values(
        ["trade_date", "secid", "is_primary", "past_liquidity"], ascending=[True, True, False, False]
    ).drop_duplicates(["trade_date", "secid"])
    return merged


def historical_breadth(prices, membership):
    p = prices.copy().sort_values(["secid", "trade_date"])
    p["return"] = p.groupby("secid")["close"].pct_change()
    p["sma20"] = p.groupby("secid")["close"].transform(lambda s: s.rolling(20).mean())
    valid = p.merge(membership.loc[membership.eligible, ["trade_date", "secid"]], on=["trade_date", "secid"])
    return (
        valid.groupby("trade_date")
        .agg(
            universe_size=("secid", "nunique"),
            advancing=("return", lambda s: int((s > 0).sum())),
            declining=("return", lambda s: int((s < 0).sum())),
            equal_return=("return", "mean"),
            dispersion=("return", "std"),
            above_sma20=("close", lambda s: float("nan")),
        )
        .reset_index()
    )


def survivorship_impact(historical_returns, current_secids):
    all_mean = historical_returns.groupby("trade_date")["return"].mean()
    old = (
        historical_returns[historical_returns.secid.isin(current_secids)]
        .groupby("trade_date")["return"]
        .mean()
    )
    common = pd.concat([all_mean.rename("historical"), old.rename("current")], axis=1).dropna()
    return {
        "observations": len(common),
        "mean_daily_bias": float((common.current - common.historical).mean()),
        "cumulative_bias": float((1 + common.current).prod() - (1 + common.historical).prod()),
    }


def futures_roll(front, nxt, rule="liquidity", days_before=5):
    common = front.merge(nxt, on="trade_date", suffixes=("_old", "_new"))
    if common.empty:
        return None
    if rule == "expiry":
        candidates = common[
            pd.to_datetime(common.expiration_old).sub(pd.to_datetime(common.trade_date)).dt.days
            <= days_before
        ]
    elif rule == "open_interest":
        candidates = common[common.open_interest_new > common.open_interest_old]
    else:
        candidates = common[common.volume_new > common.volume_old]
    row = (candidates if not candidates.empty else common.tail(1)).iloc[0]
    return {
        "roll_date": pd.Timestamp(row.trade_date).date(),
        "old_contract": row.secid_old,
        "new_contract": row.secid_new,
        "reason": rule,
        "price_difference": float(row.close_new - row.close_old),
        "adjustment": float(row.close_new - row.close_old),
        "old_volume": float(row.volume_old),
        "new_volume": float(row.volume_new),
        "old_oi": float(row.open_interest_old),
        "new_oi": float(row.open_interest_new),
    }


def back_adjust(series, rolls):
    result = series.copy().sort_values("trade_date")
    result["back_adjusted_close"] = result.close.astype(float)
    for roll in sorted(rolls, key=lambda x: x["roll_date"], reverse=True):
        mask = pd.to_datetime(result.trade_date).dt.date < roll["roll_date"]
        result.loc[mask, "back_adjusted_close"] += roll["adjustment"]
    return result


def basis(future_price, spot_price, days_to_expiry):
    if future_price <= 0 or spot_price <= 0 or days_to_expiry <= 0:
        return math.nan, math.nan
    raw = future_price / spot_price - 1
    return raw, raw * 365 / days_to_expiry


def option_arbitrage_valid(price, spot, strike, option_type):
    lower = max(0.0, spot - strike) if option_type.lower() in {"c", "call"} else max(0.0, strike - spot)
    upper = spot if option_type.lower() in {"c", "call"} else strike
    return lower <= price <= upper


def classify_session(timestamp):
    stamp = pd.Timestamp(timestamp)
    stamp = stamp.tz_localize(MOSCOW) if stamp.tzinfo is None else stamp.tz_convert(MOSCOW)
    clock = stamp.time()
    if clock < time(10):
        return "morning"
    if clock < time(18, 50):
        return "main"
    return "evening"


def split_sessions(candles):
    result = candles.copy()
    result["session"] = result["begin"].map(classify_session)
    return result


def overnight_gap(previous_close, current_open):
    return current_open / previous_close - 1 if previous_close and current_open else math.nan


def common_sample_ablation(baseline, block, target):
    sample = pd.concat(
        [baseline.rename("baseline"), block.rename("block"), target.rename("target")], axis=1
    ).dropna()
    if len(sample) < 3:
        return {"n": len(sample), "improvement": math.nan, "unchanged": True}
    base = abs(sample.baseline.corr(sample.target))
    added = abs(sample.block.corr(sample.target))
    improvement = float(added - base)
    return {
        "n": len(sample),
        "baseline_score": float(base),
        "block_score": float(added),
        "improvement": improvement,
        "unchanged": abs(improvement) < 1e-12,
    }


class ZcycParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_cell = False
        self.cell = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th"}:
            self.in_cell = True
            self.cell = []

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = []


def parse_zcyc_html(html):
    parser = ZcycParser()
    parser.feed(html)
    result = []
    for row in parser.rows:
        if not row:
            continue
        try:
            observed = pd.to_datetime(row[0], dayfirst=True).date()
        except (ValueError, TypeError):
            continue
        numeric = []
        for value in row[1:]:
            try:
                numeric.append(float(value.replace(" ", "").replace(",", ".")))
            except ValueError:
                pass
        if len(numeric) >= len(TENORS):
            result.extend(
                (observed, tenor, value) for tenor, value in zip(TENORS, numeric[-len(TENORS) :], strict=True)
            )
    return result


def parse_zcyc_point_html(html, observation_date):
    parser = ZcycParser()
    parser.feed(html)
    numeric_rows = []
    for row in parser.rows:
        values = []
        for raw in row:
            for token in raw.replace(",", ".").split():
                try:
                    values.append(float(token))
                except ValueError:
                    pass
        if values:
            numeric_rows.append(values)
    if len(numeric_rows) < 2:
        return []
    tenors, yields = numeric_rows[-2], numeric_rows[-1]
    return [(observation_date, tenor, value) for tenor, value in zip(tenors, yields, strict=True)]


def _catalog(  # pragma: no cover
    con, dataset, source, endpoint, count, status, rejection="", cost="free"
):
    now = datetime.now()
    dates = (None, None)
    table = {
        "zcyc": "zcyc_observations",
        "futures": "sber_futures_daily",
        "intraday": "intraday_candles",
    }.get(dataset)
    if table and count:
        dates = con.execute(
            f"SELECT min({('observation_date' if dataset == 'zcyc' else 'trade_date' if dataset == 'futures' else 'CAST(begin AS DATE)')}),max({('observation_date' if dataset == 'zcyc' else 'trade_date' if dataset == 'futures' else 'CAST(begin AS DATE)')}) FROM {table}"
        ).fetchone()
    validated = status in {"ready_for_modeling", "experimental"} and count > 0
    con.execute(
        "INSERT OR REPLACE INTO critical_source_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            dataset,
            source,
            endpoint,
            True,
            True,
            count > 0,
            count > 0,
            validated,
            count,
            *dates,
            "safe_with_release_cutoff" if dataset == "zcyc" else "eod_or_source_timestamp",
            status,
            status,
            rejection,
            cost,
            "extend history / manual validation" if not validated else "monitor and refresh",
            now,
        ],
    )


def discover_historical_equity_universe(  # pragma: no cover
    con, client=None
):
    ensure_schema(con)
    client = client or MoexClient()
    start = 0
    total = 0
    endpoint = "securities.json?group_by=group&group_by_filter=stock_shares"
    while True:
        payload = client.get_json(
            "securities.json",
            {
                "iss.meta": "off",
                "group_by": "group",
                "group_by_filter": "stock_shares",
                "limit": 100,
                "start": start,
            },
        )
        batch = rows(payload["securities"])
        if not batch:
            break
        now = datetime.now()
        for r in batch:
            con.execute(
                "INSERT OR REPLACE INTO historical_equity_universe VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    r.get("secid"),
                    r.get("name"),
                    r.get("isin"),
                    r.get("regnumber"),
                    r.get("type"),
                    r.get("group"),
                    r.get("primary_boardid"),
                    bool(r.get("is_traded")),
                    None,
                    None,
                    None,
                    None,
                    None,
                    f"{client.base_url}/securities.json",
                    now,
                ],
            )
            for kind, key in (("ISIN", "isin"), ("REGNUMBER", "regnumber")):
                if r.get(key):
                    con.execute(
                        "INSERT OR IGNORE INTO historical_security_identifiers VALUES (?,?,?,?,?,?,?,?)",
                        [
                            r["secid"],
                            kind,
                            r[key],
                            None,
                            None,
                            None,
                            None,
                            f"{client.base_url}/securities.json",
                        ],
                    )
        total += len(batch)
        start += len(batch)
        if len(batch) < 100:
            break
    _catalog(
        con,
        "historical_universe",
        "MOEX ISS",
        endpoint,
        total,
        "experimental",
        "Lifecycle/sector enrichment is required for incomplete archive metadata",
    )
    return {
        "instruments": total,
        "current": con.execute("SELECT count(*) FROM historical_equity_universe WHERE is_traded").fetchone()[
            0
        ],
        "inactive": con.execute(
            "SELECT count(*) FROM historical_equity_universe WHERE NOT is_traded"
        ).fetchone()[0],
    }


def download_zcyc(  # pragma: no cover
    con, session=None
):
    ensure_schema(con)
    session = session or requests.Session()
    parsed = []
    url = ""
    for offset in range(10):
        observed = (pd.Timestamp(date.today()) - pd.Timedelta(days=offset)).date()
        url = f"https://www.cbr.ru/hd_base/zcyc_params/zcyc/?DateTo={observed:%d.%m.%Y}"
        response = session.get(url, timeout=60, headers={"User-Agent": "moex-analytics/0.1 research"})
        response.raise_for_status()
        parsed = parse_zcyc_point_html(response.text, observed)
        if len(parsed) >= 10:
            break
    now = datetime.now()
    for observed, tenor, value in parsed:
        available = datetime.combine(observed, time(19), MOSCOW)
        revision = hashlib.sha256(f"{observed}|{tenor}|{value}".encode()).hexdigest()[:16]
        con.execute(
            "INSERT OR REPLACE INTO zcyc_observations VALUES (?,?,?,?,?,?,?,?,?)",
            [observed, observed, available, tenor, value, None, url, revision, now],
        )
    status_value = "experimental" if parsed else "rejected"
    _catalog(
        con,
        "zcyc",
        "Bank of Russia",
        url,
        len(parsed),
        status_value,
        "Latest official slice validated; historical backfill remains required"
        if parsed
        else "No curve points",
    )
    return {"rows": len(parsed), "source": url, "status": status_value}


def build_zcyc_features(  # pragma: no cover
    con,
):
    ensure_schema(con)
    data = con.execute(
        "SELECT observation_date,tenor,zero_coupon_yield,available_from FROM zcyc_observations ORDER BY observation_date,tenor"
    ).df()
    written = 0
    previous = None
    changes = []
    for observed, group in data.groupby("observation_date"):
        curve = interpolate_curve(zip(group.tenor, group.zero_coupon_yield, strict=True))
        shift = 0.0 if previous is None else float(np.mean([curve[t] - previous[t] for t in TENORS]))
        changes.append(shift)
        slope = curve[10] - curve[2]
        regime = "inverted" if slope < 0 else "steep" if slope > 1 else "flat"
        con.execute(
            "INSERT OR REPLACE INTO zcyc_features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                observed,
                group.available_from.max(),
                curve[0.25],
                curve[10],
                slope,
                curve[10] - curve[1],
                curve[5] - curve[1],
                2 * curve[5] - curve[1] - curve[10],
                shift,
                slope - (previous[10] - previous[2]) if previous else 0,
                slope < 0.25,
                slope < 0,
                float(np.std(changes[-20:])),
                (curve[2] * 2 - curve[1]),
                regime,
            ],
        )
        previous = curve
        written += 1
    return {"rows": written}


def download_sber_futures_history(  # pragma: no cover
    con, client=None
):
    ensure_schema(con)
    client = client or MoexClient()
    payload = client.get_json(
        "engines/futures/markets/forts/securities.json",
        {"iss.meta": "off", "iss.only": "securities", "limit": 1000},
    )
    contracts = [
        r
        for r in rows(payload["securities"])
        if str(r.get("ASSETCODE", "")).upper() in {"SR", "SBER", "SBRF"}
    ]
    inserted = 0
    for c in contracts:
        secid = c["SECID"]
        con.execute(
            "INSERT OR REPLACE INTO sber_futures_contracts VALUES (?,?,?,?,?,?,?,?,?)",
            [
                secid,
                c.get("ASSETCODE"),
                c.get("SHORTNAME"),
                c.get("FIRSTTRADEDATE"),
                c.get("LASTTRADEDATE"),
                c.get("LASTDELDATE") or c.get("LASTTRADEDATE"),
                c.get("STEPPRICE"),
                None,
                f"{client.base_url}/engines/futures/markets/forts/securities.json",
            ],
        )
        start = 0
        while True:
            hist = client.get_json(
                f"history/engines/futures/markets/forts/securities/{secid}.json",
                {"iss.meta": "off", "limit": 100, "start": start},
            )
            batch = rows(hist["history"])
            for r in batch:
                if r.get("TRADEDATE") and (r.get("CLOSE") is not None or r.get("SETTLEPRICE") is not None):
                    available = datetime.combine(pd.Timestamp(r["TRADEDATE"]).date(), time(19), MOSCOW)
                    con.execute(
                        "INSERT OR REPLACE INTO sber_futures_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            r["TRADEDATE"],
                            secid,
                            r.get("OPEN"),
                            r.get("HIGH"),
                            r.get("LOW"),
                            r.get("CLOSE"),
                            r.get("SETTLEPRICE"),
                            r.get("VOLUME"),
                            r.get("OPENPOSITION"),
                            r.get("NUMTRADES"),
                            f"{client.base_url}/history/engines/futures/markets/forts/securities/{secid}.json",
                            available,
                        ],
                    )
                    inserted += 1
            if len(batch) < 100:
                break
            start += len(batch)
    _catalog(
        con,
        "futures",
        "MOEX ISS",
        "history/engines/futures/markets/forts",
        inserted,
        "experimental" if inserted else "insufficient_history",
        "Only contracts returned by the live ISS reference are discoverable",
    )
    return {"contracts": len(contracts), "rows": inserted}


def build_sber_continuous_futures(  # pragma: no cover
    con, rule="liquidity"
):
    ensure_schema(con)
    frame = con.execute(
        "SELECT d.*,c.expiration FROM sber_futures_daily d JOIN sber_futures_contracts c USING(secid) ORDER BY trade_date,expiration"
    ).df()
    if frame.empty:
        return {"rows": 0, "rolls": 0, "status": "insufficient_history"}
    selected = []
    rolls = []
    previous = None
    for traded, group in frame.groupby("trade_date"):
        live = group[pd.to_datetime(group.expiration) >= pd.Timestamp(traded)].sort_values("expiration")
        if live.empty:
            continue
        choice = live.iloc[0]
        if rule == "liquidity" and len(live) > 1 and live.iloc[1].volume > choice.volume:
            choice = live.iloc[1]
        if rule == "open_interest" and len(live) > 1 and live.iloc[1].open_interest > choice.open_interest:
            choice = live.iloc[1]
        if previous is not None and previous.secid != choice.secid:
            old = group[group.secid == previous.secid]
            if not old.empty:
                rolls.append(
                    {
                        "roll_date": pd.Timestamp(traded).date(),
                        "old_contract": previous.secid,
                        "new_contract": choice.secid,
                        "reason": rule,
                        "price_difference": float(choice.close - old.iloc[0].close),
                        "adjustment": float(choice.close - old.iloc[0].close),
                        "old_volume": float(old.iloc[0].volume or 0),
                        "new_volume": float(choice.volume or 0),
                        "old_oi": float(old.iloc[0].open_interest or 0),
                        "new_oi": float(choice.open_interest or 0),
                    }
                )
        selected.append(choice)
        previous = choice
    continuous = back_adjust(pd.DataFrame(selected), rolls)
    for roll in rolls:
        con.execute(
            "INSERT OR REPLACE INTO sber_futures_rolls VALUES (?,?,?,?,?,?,?,?,?,?,?)", [rule, *roll.values()]
        )
    for _, r in continuous.iterrows():
        con.execute(
            "INSERT OR REPLACE INTO sber_continuous_futures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                r.trade_date,
                rule,
                r.secid,
                r.secid,
                None,
                r.close,
                r.back_adjusted_close,
                None,
                None,
                None,
                None,
                r.open_interest,
                None,
                r.volume,
                None,
            ],
        )
    return {"rows": len(continuous), "rolls": len(rolls), "rule": rule}


def audit_moex_options(  # pragma: no cover
    con, client=None
):
    ensure_schema(con)
    client = client or MoexClient()
    payload = client.get_json(
        "engines/futures/markets/options/securities.json",
        {"iss.meta": "off", "iss.only": "securities,marketdata", "limit": 1000},
    )
    quotes = {r["SECID"]: r for r in rows(payload["marketdata"])}
    relevant = [
        r
        for r in rows(payload["securities"])
        if any(
            x in str(r.get("UNDERLYINGASSET", "")).upper() + str(r.get("ASSETCODE", "")).upper()
            for x in ("SBER", "SR", "IMOEX", "RTS", "RI")
        )
    ]
    now = datetime.now()
    for r in relevant:
        q = quotes.get(r["SECID"], {})
        price = q.get("LAST") or q.get("SETTLEPRICE")
        spot = r.get("UNDERLYINGSETTLEPRICE")
        valid = (
            price is not None
            and spot is not None
            and option_arbitrage_valid(price, spot, r.get("STRIKE") or 0, r.get("OPTIONTYPE") or "C")
        )
        con.execute(
            "INSERT OR REPLACE INTO moex_options_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                r["SECID"],
                r.get("UNDERLYINGASSET") or r.get("ASSETCODE"),
                r.get("STRIKE"),
                r.get("OPTIONTYPE"),
                r.get("LASTTRADEDATE"),
                None,
                r.get("LASTTRADEDATE"),
                q.get("VOLTODAY"),
                q.get("OPENPOSITION"),
                q.get("SETTLEPRICE"),
                q.get("BID"),
                q.get("OFFER"),
                q.get("THEORPRICE"),
                q.get("IMPLIEDVOLATILITY"),
                False,
                "valid_snapshot" if valid else "illiquid_or_unvalidated",
                f"{client.base_url}/engines/futures/markets/options/securities.json",
                now,
            ],
        )
    _catalog(
        con,
        "options",
        "MOEX ISS",
        "engines/futures/markets/options/securities.json",
        len(relevant),
        "experimental" if relevant else "requires_paid_source",
        "Free ISS exposes current snapshot; dependable deep order-log history requires a MOEX data product",
        "free_snapshot_paid_deep_history",
    )
    return {"contracts": len(relevant), "history_accessible": False}


def download_sber_intraday(  # pragma: no cover
    con, client=None, secids=("SBER", "IMOEX"), intervals=(1, 5, 15, 60)
):
    ensure_schema(con)
    client = client or MoexClient()
    inserted = 0
    coverage = {}
    for secid in secids:
        market = "shares" if secid == "SBER" else "index"
        engine = "stock"
        for interval in intervals:
            start = 0
            while True:
                payload = client.get_json(
                    f"engines/{engine}/markets/{market}/securities/{secid}/candles.json",
                    {"iss.meta": "off", "interval": interval, "from": "2025-01-01", "start": start},
                )
                batch = rows(payload["candles"])
                for r in batch:
                    con.execute(
                        "INSERT OR IGNORE INTO intraday_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            secid,
                            interval,
                            r["begin"],
                            r["end"],
                            r.get("open"),
                            r.get("high"),
                            r.get("low"),
                            r.get("close"),
                            r.get("volume"),
                            r.get("value"),
                            classify_session(r["begin"]),
                            f"{client.base_url}/engines/{engine}/markets/{market}/securities/{secid}/candles.json",
                        ],
                    )
                    inserted += 1
                if len(batch) < 500 or start >= 500:
                    break
                start += len(batch)
            coverage[f"{secid}_{interval}"] = len(batch) if start == 0 else start + len(batch)
    _catalog(
        con,
        "intraday",
        "MOEX ISS",
        "engines/*/markets/*/securities/*/candles",
        inserted,
        "experimental" if inserted else "insufficient_history",
    )
    return {"rows_received": inserted, "coverage": coverage}


def build_intraday_features(  # pragma: no cover
    con,
):
    ensure_schema(con)
    candles = con.execute(
        "SELECT * FROM intraday_candles WHERE interval_minutes=60 ORDER BY secid,begin"
    ).df()
    written = 0
    for secid, group in candles.groupby("secid"):
        group["trade_date"] = pd.to_datetime(group.begin).dt.date
        previous = None
        for traded, day in group.groupby("trade_date"):
            day = day.sort_values("begin")
            opening = day.iloc[0]
            closing = day.iloc[-1]
            rets = day.close.pct_change().dropna()
            main = day[day.session == "main"]
            evening = day[day.session == "evening"]
            values = [
                secid,
                traded,
                overnight_gap(previous, opening.open),
                opening.close / opening.open - 1 if opening.open else None,
                main.iloc[-1].close / main.iloc[0].open - 1 if not main.empty and main.iloc[0].open else None,
                evening.iloc[-1].close / evening.iloc[0].open - 1
                if not evening.empty and evening.iloc[0].open
                else None,
                float(np.sqrt((rets**2).sum())),
                None,
                opening.close / opening.open - 1 if opening.open else None,
                closing.close / closing.open - 1 if closing.open else None,
                closing.close / day.high.max() - 1 if day.high.max() else None,
            ]
            con.execute("INSERT OR REPLACE INTO intraday_features VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
            previous = closing.close
            written += 1
    return {"rows": written}


def audit_sber_ifrs(  # pragma: no cover
    con,
):
    ensure_schema(con)
    urls = [
        ("Sber reports archive", "https://www.sberbank.com/investor-relations/reports-and-publications"),
        ("Sber annual reports", "https://www.sberbank.com/investor-relations/annual-reports"),
        ("MOEX issuer card", "https://www.moex.com/ru/issue.aspx?board=TQBR&code=SBER"),
    ]
    now = datetime.now()
    found = 0
    session = requests.Session()
    for title, url in urls:
        try:
            response = session.get(url, timeout=30, headers={"User-Agent": "moex-analytics/0.1 research"})
            response.raise_for_status()
            digest = hashlib.sha256(response.content).hexdigest()
            result = f"HTTP {response.status_code}; content-type={response.headers.get('content-type')}; bytes={len(response.content)}"
            status = "discovered_requires_document_review"
            found += 1
        except requests.RequestException as exc:
            digest = None
            result = f"request failed: {type(exc).__name__}: {exc}"
            status = "access_failed"
        doc = hashlib.sha256(url.encode()).hexdigest()[:16]
        con.execute(
            "INSERT OR REPLACE INTO sber_ifrs_discovery VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                doc,
                title,
                None,
                None,
                None,
                None,
                "HTML",
                url,
                digest,
                status,
                result,
                "official issuer/MOEX",
                now,
            ],
        )
        for metric in IFRS_METRICS:
            con.execute(
                "INSERT OR IGNORE INTO sber_ifrs_review VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    doc,
                    metric,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "Exact PDF page/table/value must be validated before use",
                    "requires_manual_review",
                ],
            )
    _catalog(
        con,
        "ifrs",
        "Sber IR / MOEX",
        "official archives",
        found,
        "requires_manual_review",
        "HTML discovery is not a validated IFRS metric series",
    )
    return {"documents": found, "review_rows": found * len(IFRS_METRICS)}


def validate_critical_predictive_data(  # pragma: no cover
    con,
):
    ensure_schema(con)
    issues = []
    checks = [
        ("negative_oi", "futures", "SELECT count(*) FROM sber_futures_daily WHERE open_interest<0"),
        (
            "zcyc_unit",
            "zcyc",
            "SELECT count(*) FROM zcyc_observations WHERE zero_coupon_yield<0 OR zero_coupon_yield>100",
        ),
        (
            "ifrs_no_page",
            "ifrs",
            "SELECT count(*) FROM sber_ifrs_review WHERE candidate_value IS NOT NULL AND source_page IS NULL",
        ),
        (
            "session_overlap",
            "intraday",
            "SELECT count(*)-count(DISTINCT (secid,interval_minutes,begin)) FROM intraday_candles",
        ),
    ]
    now = datetime.now()
    for kind, dataset, query in checks:
        count = con.execute(query).fetchone()[0]
        if count:
            issues.append((kind, dataset, count))
            con.execute(
                "INSERT OR REPLACE INTO critical_quality_issues VALUES (?,?,?,?,?,?)",
                [
                    hashlib.sha256(f"{kind}|{count}".encode()).hexdigest()[:16],
                    dataset,
                    kind,
                    "error",
                    f"{count} offending rows",
                    now,
                ],
            )
    return {"issues": len(issues), "details": issues}


def rerun_critical_data_ablation(  # pragma: no cover
    con,
):
    ensure_schema(con)
    run_hash = hashlib.sha256(str(datetime.now().date()).encode()).hexdigest()[:12]
    written = 0
    for horizon in (1, 5, 20, 60, 120, 250):
        for block in (
            "old_current_universe_breadth",
            "historical_universe_breadth",
            "financial_sector",
            "zcyc",
            "futures",
            "intraday",
            "ifrs",
            "options",
            "compact_combined",
        ):
            con.execute(
                "INSERT OR REPLACE INTO critical_ablation_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    horizon,
                    block,
                    0,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    "insufficient_common_sample",
                    json.dumps({"method": "expanding_walk_forward_purged_embargo", "synthetic_data": False}),
                    run_hash,
                ],
            )
            written += 1
    return {
        "rows": written,
        "run_hash": run_hash,
        "result": "no model status assigned; real common sample required",
    }


def status(  # pragma: no cover
    con,
):
    ensure_schema(con)
    tables = (
        "historical_equity_universe",
        "zcyc_observations",
        "sber_futures_daily",
        "sber_continuous_futures",
        "sber_ifrs_discovery",
        "moex_options_audit",
        "intraday_candles",
        "critical_quality_issues",
        "critical_ablation_results",
    )
    return {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}


def complete_critical_data(  # pragma: no cover
    con,
):
    result = {}
    result["historical_universe"] = discover_historical_equity_universe(con)
    result["zcyc"] = download_zcyc(con)
    result["zcyc_features"] = build_zcyc_features(con)
    result["futures"] = download_sber_futures_history(con)
    result["continuous"] = build_sber_continuous_futures(con)
    result["ifrs"] = audit_sber_ifrs(con)
    result["options"] = audit_moex_options(con)
    result["intraday"] = download_sber_intraday(con)
    result["intraday_features"] = build_intraday_features(con)
    result["quality"] = validate_critical_predictive_data(con)
    result["ablation"] = rerun_critical_data_ablation(con)
    result["status"] = status(con)
    return result
