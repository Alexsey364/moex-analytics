"""Deep official backfills, PIT universe and common-sample diagnostics."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from datetime import date, datetime, time
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from moex_analytics.critical_data.core import back_adjust, rows
from moex_analytics.moex_client import MoexClient, MoexError

from .schema import DDL

MOSCOW = ZoneInfo("Europe/Moscow")
PARSER_VERSION = "cbr-zcyc-table-v2"
CURVE_TENORS = (0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0)
HORIZONS = (1, 5, 20, 60, 120, 250)
VERSION = "deep-backfill-v1"


def ensure_schema(con):
    con.execute(DDL)


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.cell = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th"}:
            self.active = True
            self.cell = []

    def handle_data(self, data):
        if self.active:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.active:
            self.row.append(" ".join("".join(self.cell).split()))
            self.active = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = []


def parse_zcyc_archive(html):
    parser = TableParser()
    parser.feed(html)
    result = []
    for row in parser.rows:
        if len(row) < 13:
            continue
        try:
            observed = pd.to_datetime(row[0], dayfirst=True).date()
        except (ValueError, TypeError):
            continue
        values = []
        for raw in row[1:13]:
            try:
                values.append(float(raw.replace(" ", "").replace(",", ".")))
            except ValueError:
                values = []
                break
        if len(values) == 12 and all(0 <= value <= 100 for value in values):
            result.extend((observed, tenor, value) for tenor, value in zip(CURVE_TENORS, values, strict=True))
    return result


def validate_curve_dates(curves):
    frame = pd.DataFrame(curves, columns=["date", "tenor", "yield"])
    if frame.empty:
        return {"dates": 0, "valid_dates": 0, "invalid_dates": 0}
    counts = frame.groupby("date").tenor.nunique()
    invalid = int((counts != len(CURVE_TENORS)).sum())
    return {
        "dates": len(counts),
        "valid_dates": int((counts == len(CURVE_TENORS)).sum()),
        "invalid_dates": invalid,
    }


def dynamic_liquidity_selection(prices, min_history=20, max_size=150, min_size=1):
    frame = prices.copy().sort_values(["secid", "trade_date"])
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    grouped = frame.groupby("secid", group_keys=False)
    frame["trailing_turnover"] = grouped.value.transform(
        lambda s: s.shift(1).rolling(60, min_periods=min_history).median()
    )
    frame["trailing_trade_days"] = grouped.close.transform(
        lambda s: s.shift(1).rolling(60, min_periods=1).count()
    )
    frame["eligible"] = (
        (frame.close > 0) & (frame.trailing_trade_days >= min_history) & frame.trailing_turnover.notna()
    )
    frame["rank"] = (
        frame.loc[frame.eligible]
        .groupby("trade_date")
        .trailing_turnover.rank(method="first", ascending=False)
    )
    frame["eligible"] &= frame["rank"].fillna(max_size + 1) <= max_size
    sizes = frame[frame.eligible].groupby("trade_date").secid.transform("count")
    frame.loc[frame.eligible, "market_has_minimum"] = (sizes >= min_size).to_numpy()
    return frame


def survivorship_comparison(prices, dynamic, current_secids):
    frame = prices.copy().sort_values(["secid", "trade_date"])
    frame["return"] = frame.groupby("secid").close.pct_change()
    frame["advance"] = (frame["return"] > 0).astype(float)
    dynamic_keys = dynamic.loc[dynamic.eligible, ["trade_date", "secid"]].copy()
    dynamic_keys.trade_date = pd.to_datetime(dynamic_keys.trade_date)
    historical = (
        frame.merge(dynamic_keys, on=["trade_date", "secid"])
        .groupby("trade_date")
        .agg(
            dynamic_breadth=("advance", "mean"),
            dynamic_return=("return", "mean"),
            dynamic_size=("secid", "nunique"),
        )
    )
    current = (
        frame[frame.secid.isin(current_secids)]
        .groupby("trade_date")
        .agg(
            current40_breadth=("advance", "mean"),
            current40_return=("return", "mean"),
            current40_size=("secid", "nunique"),
        )
    )
    joined = current.join(historical, how="inner").dropna()
    joined["difference"] = joined.dynamic_breadth - joined.current40_breadth
    joined["return_difference"] = joined.dynamic_return - joined.current40_return
    return joined.reset_index()


def infer_quarterly_expiration(shortname):
    try:
        suffix = shortname.split("-")[-1]
        month_text, year_text = suffix.split(".")
        month = int(month_text)
        year = 2000 + int(year_text)
    except (AttributeError, ValueError):
        return None
    if month not in {3, 6, 9, 12}:
        return None
    month_calendar = calendar.monthcalendar(year, month)
    thursdays = [week[calendar.THURSDAY] for week in month_calendar if week[calendar.THURSDAY]]
    return date(year, month, thursdays[2])


def contract_multiplier_valid(multiplier, price_scale, underlying_units):
    return all(value is not None and value > 0 for value in (multiplier, price_scale, underlying_units))


def select_continuous_contracts(frame, rule="combined", days_before=5):
    data = frame.copy()
    data.trade_date = pd.to_datetime(data.trade_date)
    data.expiration = pd.to_datetime(data.expiration)
    selected = []
    for traded, group in data.groupby("trade_date"):
        live = group[group.expiration >= traded].sort_values("expiration")
        if live.empty:
            continue
        front = live.iloc[0]
        choice = front
        if len(live) > 1:
            nxt = live.iloc[1]
            days = (front.expiration - traded).days
            volume_cross = (nxt.volume or 0) > (front.volume or 0)
            oi_cross = (nxt.open_interest or 0) > (front.open_interest or 0)
            choose = (
                days <= days_before
                if rule == "expiry"
                else volume_cross
                if rule == "volume"
                else oi_cross
                if rule == "open_interest"
                else days <= days_before or (volume_cross and oi_cross)
            )
            if choose:
                choice = nxt
        selected.append(
            {
                **choice.to_dict(),
                "front_contract": front.secid,
                "next_contract": live.iloc[1].secid if len(live) > 1 else None,
            }
        )
    return pd.DataFrame(selected)


def derive_rolls(selected, rule):
    if selected.empty:
        return []
    data = selected.sort_values("trade_date")
    rolls = []
    for (_, old), (_, new) in zip(data.iloc[:-1].iterrows(), data.iloc[1:].iterrows(), strict=True):
        if old.secid == new.secid:
            continue
        difference = float(new.close - old.close)
        ratio = float(new.close / old.close) if old.close else math.nan
        rolls.append(
            {
                "rule": rule,
                "roll_date": pd.Timestamp(new.trade_date).date(),
                "old_contract": old.secid,
                "new_contract": new.secid,
                "reason": rule,
                "old_price": float(old.close),
                "new_price": float(new.close),
                "price_difference": difference,
                "ratio": ratio,
                "old_volume": float(old.volume or 0),
                "new_volume": float(new.volume or 0),
                "old_oi": float(old.open_interest or 0),
                "new_oi": float(new.open_interest or 0),
                "pit_safe": True,
            }
        )
    return rolls


def ratio_adjust(series, rolls):
    result = series.copy().sort_values("trade_date")
    result["ratio_adjusted_close"] = result.close.astype(float)
    for roll in sorted(rolls, key=lambda x: x["roll_date"], reverse=True):
        if not math.isfinite(roll["ratio"]) or roll["ratio"] <= 0:
            continue
        mask = pd.to_datetime(result.trade_date).dt.date < roll["roll_date"]
        result.loc[mask, "ratio_adjusted_close"] *= roll["ratio"]
    return result


def classify_multi_session(timestamp):
    stamp = pd.Timestamp(timestamp)
    stamp = stamp.tz_localize(MOSCOW) if stamp.tzinfo is None else stamp.tz_convert(MOSCOW)
    clock = stamp.time()
    if clock < time(10):
        return "morning"
    if clock < time(10, 10):
        return "opening_auction"
    if clock < time(18, 40):
        return "main"
    if clock < time(19):
        return "closing_auction"
    return "evening"


def validate_ifrs_review(record):
    required = (
        "document_id",
        "metric",
        "source_page",
        "source_table",
        "source_line",
        "raw_text_fragment",
        "unit",
    )
    missing = [field for field in required if not record.get(field)]
    if record.get("candidate_value") is None:
        missing.append("candidate_value")
    return {
        "valid": not missing,
        "missing": missing,
        "status": "validated"
        if not missing and record.get("confidence", 0) >= 0.9
        else "requires_manual_review",
    }


def distinguish_option_history(snapshot_rows, history_rows):
    if history_rows > 0:
        return "historical_pilot"
    if snapshot_rows > 0:
        return "snapshot_only"
    return "unavailable"


def effective_sample_size(values):
    series = pd.Series(values).dropna()
    if len(series) < 3:
        return float(len(series))
    rho = series.autocorr(1)
    if pd.isna(rho):
        return float(len(series))
    return float(max(1, len(series) * (1 - rho) / (1 + rho))) if rho > -0.999 else float(len(series))


def coverage_suitability(rows_count, effective, folds):
    if rows_count >= 1000 and effective >= 250 and folds >= 5:
        return "ready_for_direction_model"
    if rows_count >= 250 and effective >= 60 and folds >= 3:
        return "ready_for_experimental_model"
    return "insufficient_common_sample"


def _audit(con, dataset, endpoint, count, date_from, date_to, status, evidence, cost="free"):
    con.execute(
        "INSERT OR REPLACE INTO deep_backfill_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            dataset,
            endpoint,
            count,
            date_from,
            date_to,
            True,
            True,
            count > 0,
            count > 0,
            status in {"validated", "experimental"},
            "safe_with_release_cutoff",
            status,
            cost,
            evidence,
            "incremental refresh" if count else "manual/vendor decision",
            datetime.now(),
        ],
    )


def backfill_zcyc_history(con, session=None, date_from="2013-01-01", date_to=None):  # pragma: no cover
    ensure_schema(con)
    session = session or requests.Session()
    date_to = date_to or date.today().isoformat()
    start = pd.Timestamp(date_from)
    end = pd.Timestamp(date_to)
    inserted = 0
    dates = 0
    while start <= end:
        chunk_end = min(start + pd.DateOffset(years=2) - pd.Timedelta(days=1), end)
        url = f"https://www.cbr.ru/hd_base/zcyc_params/?UniDbQuery.Posted=True&UniDbQuery.From={start:%d.%m.%Y}&UniDbQuery.To={chunk_end:%d.%m.%Y}"
        response = session.get(url, timeout=120, headers={"User-Agent": "moex-analytics/0.1 research"})
        response.raise_for_status()
        parsed = parse_zcyc_archive(response.text)
        quality = validate_curve_dates(parsed)
        dates += quality["valid_dates"]
        for observed, tenor, value in parsed:
            revision = hashlib.sha256(f"{observed}|{tenor}|{value}".encode()).hexdigest()[:16]
            available = datetime.combine(observed, time(19), MOSCOW)
            con.execute(
                "INSERT OR REPLACE INTO deep_zcyc_archive VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    observed,
                    observed,
                    available,
                    tenor,
                    value,
                    None,
                    url,
                    PARSER_VERSION,
                    revision,
                    "validated",
                    datetime.now(),
                ],
            )
            inserted += 1
        start = chunk_end + pd.Timedelta(days=1)
    bounds = con.execute(
        "SELECT min(observation_date),max(observation_date) FROM deep_zcyc_archive"
    ).fetchone()
    _audit(
        con,
        "zcyc_history",
        "CBR zcyc_params",
        inserted,
        *bounds,
        "validated" if dates > 100 else "insufficient_history",
        f"{dates} complete daily curves",
    )
    return {"rows_received": inserted, "curve_dates": dates, "date_from": bounds[0], "date_to": bounds[1]}


def discover_expired_sber_futures(con, client=None):  # pragma: no cover
    ensure_schema(con)
    client = client or MoexClient()
    start = 0
    found = {}
    while True:
        payload = client.get_json(
            "securities.json", {"q": "SBRF-", "iss.meta": "off", "limit": 100, "start": start}
        )
        batch = rows(payload["securities"])
        if not batch:
            break
        for row in batch:
            if row.get("type") == "futures" and str(row.get("shortname", "")).upper().startswith("SBRF-"):
                found[row["secid"]] = row
        if len(batch) < 100:
            break
        start += len(batch)
        if start >= 5000:
            break
    for secid, row in found.items():
        try:
            detail = client.get_json(f"securities/{secid}.json", {"iss.meta": "off"})
            description = {r[0]: r[2] for r in detail.get("description", {}).get("data", [])}
            boards = rows(detail.get("boards", {}))
            forts = next((x for x in boards if x.get("market") == "forts"), {})
        except MoexError:
            description = {}
            forts = {}
        last = forts.get("history_to") or description.get("LASTTRADEDATE")
        first = forts.get("history_from")
        expiration = last or infer_quarterly_expiration(row.get("shortname"))
        multiplier = description.get("LOTVOLUME")
        tick = description.get("MINSTEP")
        scale = description.get("DECIMALS")
        underlying = description.get("LOTVOLUME")
        spec = (
            "validated"
            if contract_multiplier_valid(multiplier, 10 ** (scale or 0), underlying)
            else "requires_manual_review"
        )
        con.execute(
            "INSERT OR REPLACE INTO expired_sber_futures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                secid,
                row.get("shortname"),
                first,
                last,
                expiration,
                bool(row.get("is_traded")),
                "SBRF",
                multiplier,
                tick,
                10 ** (scale or 0) if scale is not None else None,
                underlying,
                spec,
                f"{client.base_url}/securities.json?q=SBRF-",
                datetime.now(),
            ],
        )
    inactive = sum(not bool(r.get("is_traded")) for r in found.values())
    _audit(
        con,
        "expired_sber_futures",
        "MOEX ISS securities?q=SBRF-",
        len(found),
        None,
        None,
        "experimental",
        f"{inactive} inactive futures discovered; specification must be verified",
    )
    return {"contracts": len(found), "expired_or_inactive": inactive}


def backfill_sber_futures(con, client=None):  # pragma: no cover
    ensure_schema(con)
    client = client or MoexClient()
    contracts = con.execute("SELECT secid FROM expired_sber_futures ORDER BY secid").fetchall()
    inserted = 0
    failures = {}
    for (secid,) in contracts:
        start = 0
        try:
            while True:
                payload = client.get_json(
                    f"history/engines/futures/markets/forts/securities/{secid}.json",
                    {"iss.meta": "off", "limit": 100, "start": start},
                )
                batch = rows(payload["history"])
                for row in batch:
                    if not row.get("TRADEDATE") or row.get("CLOSE") is None:
                        continue
                    available = datetime.combine(pd.Timestamp(row["TRADEDATE"]).date(), time(19), MOSCOW)
                    con.execute(
                        "INSERT OR IGNORE INTO deep_sber_futures_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            row["TRADEDATE"],
                            secid,
                            row.get("OPEN"),
                            row.get("HIGH"),
                            row.get("LOW"),
                            row.get("CLOSE"),
                            row.get("SETTLEPRICE"),
                            row.get("VOLUME"),
                            row.get("VALUE"),
                            row.get("OPENPOSITION"),
                            row.get("NUMTRADES"),
                            available,
                            f"{client.base_url}/history/engines/futures/markets/forts/securities/{secid}.json",
                        ],
                    )
                    inserted += 1
                if len(batch) < 100:
                    break
                start += len(batch)
        except MoexError as exc:
            failures[secid] = str(exc)
    bounds = con.execute("SELECT min(trade_date),max(trade_date) FROM deep_sber_futures_daily").fetchone()
    _audit(
        con,
        "sber_futures_archive",
        "MOEX ISS futures history",
        inserted,
        *bounds,
        "experimental" if inserted else "insufficient_history",
        f"failures={len(failures)}; raw source contract retained",
    )
    return {
        "rows_received": inserted,
        "contracts": len(contracts),
        "failures": failures,
        "date_from": bounds[0],
        "date_to": bounds[1],
    }


def rebuild_continuous_futures(con):  # pragma: no cover
    ensure_schema(con)
    frame = con.execute(
        "SELECT d.*,c.expiration,c.shortname,c.multiplier,c.price_scale,c.underlying_units FROM deep_sber_futures_daily d JOIN expired_sber_futures c USING(secid) ORDER BY trade_date,expiration"
    ).df()
    summary = {}
    if frame.empty:
        return {"rows": 0, "rolls": 0}
    frame["expiration"] = frame.apply(
        lambda row: infer_quarterly_expiration(row.shortname) if pd.isna(row.expiration) else row.expiration,
        axis=1,
    )
    frame = frame[frame.expiration.notna()]
    for rule in ("expiry", "volume", "open_interest", "combined"):
        selected = select_continuous_contracts(frame, rule)
        rolls = derive_rolls(selected, rule)
        adjusted = back_adjust(selected, [{**roll, "adjustment": roll["price_difference"]} for roll in rolls])
        adjusted = ratio_adjust(adjusted, rolls)
        con.execute("DELETE FROM deep_futures_rolls WHERE rule=?", [rule])
        con.execute("DELETE FROM deep_continuous_futures WHERE rule=?", [rule])
        for roll in rolls:
            con.execute(
                "INSERT INTO deep_futures_rolls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", list(roll.values())
            )
        for _, row in adjusted.iterrows():
            basis_status = (
                "requires_verified_multiplier"
                if not contract_multiplier_valid(row.multiplier, row.price_scale, row.underlying_units)
                else "spot_join_required"
            )
            con.execute(
                "INSERT INTO deep_continuous_futures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    row.trade_date,
                    rule,
                    row.secid,
                    row.front_contract,
                    row.next_contract,
                    row.close,
                    row.back_adjusted_close,
                    row.ratio_adjusted_close,
                    row.settlement,
                    row.volume,
                    row.open_interest,
                    None,
                    None,
                    basis_status,
                ],
            )
        summary[rule] = {"rows": len(selected), "rolls": len(rolls)}
    return summary


def backfill_historical_liquid_universe(con):  # pragma: no cover
    ensure_schema(con)
    prices = con.execute(
        "SELECT trade_date,secid,board,close,value FROM predictive_market_prices WHERE close>0 ORDER BY secid,trade_date"
    ).df()
    selection = dynamic_liquidity_selection(prices, min_history=20, max_size=150)
    con.execute("DELETE FROM dynamic_liquid_universe WHERE selection_version=?", [VERSION])
    for row in selection[selection.eligible].itertuples():
        con.execute(
            "INSERT INTO dynamic_liquid_universe VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                row.trade_date,
                row.secid,
                row.board,
                row.trailing_turnover,
                int(row.trailing_trade_days),
                int(row.rank),
                True,
                False,
                True,
                VERSION,
                datetime.combine(pd.Timestamp(row.trade_date).date(), time(19), MOSCOW),
            ],
        )
    current = [
        r[0]
        for r in con.execute(
            "SELECT source_secid FROM predictive_market_universe WHERE is_traded ORDER BY coalesce(liquidity_value,0) DESC LIMIT 40"
        ).fetchall()
    ]
    impact = survivorship_comparison(prices, selection, current)
    con.execute("DELETE FROM survivorship_impact_daily WHERE calculation_version=?", [VERSION])
    for row in impact.itertuples():
        con.execute(
            "INSERT INTO survivorship_impact_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                row.trade_date,
                row.current40_breadth,
                row.dynamic_breadth,
                row.difference,
                row.current40_return,
                row.dynamic_return,
                row.return_difference,
                int(row.current40_size),
                int(row.dynamic_size),
                VERSION,
            ],
        )
    historical_count = len(set(prices.secid) - set(current))
    status = "experimental" if historical_count else "blocked_by_data_quality"
    _audit(
        con,
        "dynamic_universe",
        "local MOEX ISS EOD backfill",
        int(selection.eligible.sum()),
        prices.trade_date.min().date(),
        prices.trade_date.max().date(),
        status,
        f"{historical_count} non-current40 securities in available archive; selection uses lagged turnover",
    )
    return {
        "membership_rows": int(selection.eligible.sum()),
        "securities": int(selection.loc[selection.eligible, "secid"].nunique()),
        "non_current40": historical_count,
        "impact_days": len(impact),
    }


def calculate_survivorship_impact(con):  # pragma: no cover
    ensure_schema(con)
    row = con.execute(
        "SELECT count(*),avg(difference),max(abs(difference)),avg(return_difference),max(abs(return_difference)) FROM survivorship_impact_daily"
    ).fetchone()
    return {
        "days": row[0],
        "mean_breadth_difference": row[1],
        "max_breadth_difference": row[2],
        "mean_return_difference": row[3],
        "max_return_difference": row[4],
    }


def build_historical_financial_sector(con):  # pragma: no cover
    ensure_schema(
        con
    )  # Official MOEXFN index is kept separate; ISS security metadata has no historical sector field.
    prices = con.execute(
        "SELECT trade_date,close FROM daily_prices WHERE secid='MOEXFN' ORDER BY trade_date"
    ).df()
    _audit(
        con,
        "financial_sector",
        "MOEX ISS MOEXFN + historical constituent metadata",
        len(prices),
        prices.trade_date.min() if len(prices) else None,
        prices.trade_date.max() if len(prices) else None,
        "insufficient_history",
        "Official index can be loaded separately; PIT constituent classification unavailable in free metadata",
    )
    return {"official_index_rows": len(prices), "reconstructed_rows": 0, "status": "insufficient_history"}


def record_intraday_coverage(con):  # pragma: no cover
    ensure_schema(con)
    groups = con.execute(
        "SELECT secid,interval_minutes,min(begin),max(begin),count(*),list(distinct session) FROM intraday_candles GROUP BY secid,interval_minutes"
    ).fetchall()
    for secid, interval, start, end, count, sessions in groups:
        con.execute(
            "INSERT OR REPLACE INTO deep_intraday_coverage VALUES (?,?,?,?,?,?,?,?,?)",
            [
                secid,
                interval,
                start,
                end,
                count,
                json.dumps(sessions),
                "downloaded_history",
                f"MOEX ISS candles/{secid}",
                "experimental",
            ],
        )
    return {"series": len(groups), "rows": sum(row[4] for row in groups)}


def validate_sber_ifrs_review(con):  # pragma: no cover
    ensure_schema(con)
    records = con.execute(
        "SELECT document_id,metric,source_page,source_table,source_row,candidate_value,unit,status FROM sber_ifrs_review"
    ).fetchall()
    validated = 0
    for document, metric, page, table, line, value, unit, _ in records:
        record = {
            "document_id": document,
            "metric": metric,
            "source_page": page,
            "source_table": table,
            "source_line": line,
            "raw_text_fragment": None,
            "candidate_value": value,
            "unit": unit,
            "confidence": 0.0,
        }
        result = validate_ifrs_review(record)
        con.execute(
            "INSERT OR REPLACE INTO ifrs_review_validation VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                document,
                metric,
                page,
                table,
                line,
                None,
                value,
                unit,
                0.0,
                result["status"],
                "missing: " + ",".join(result["missing"]),
                datetime.now(),
            ],
        )
        validated += result["valid"]
    return {
        "records": len(records),
        "validated": validated,
        "requires_manual_review": len(records) - validated,
    }


def backfill_options_history(con, client=None, limit=40):  # pragma: no cover
    ensure_schema(con)
    client = client or MoexClient()
    contracts = con.execute(
        "SELECT secid,underlying,expiration FROM moex_options_audit ORDER BY expiration DESC LIMIT ?", [limit]
    ).fetchall()
    total = 0
    for secid, underlying, expiration in contracts:
        endpoint = f"history/engines/futures/markets/options/securities/{secid}.json"
        try:
            payload = client.get_json(endpoint, {"iss.meta": "off", "limit": 100})
            history = rows(payload.get("history", {}))
            result = f"HTTP success; history rows={len(history)}"
        except MoexError as exc:
            history = []
            result = str(exc)
        dates = [pd.Timestamp(row["TRADEDATE"]).date() for row in history if row.get("TRADEDATE")]
        total += len(history)
        status = distinguish_option_history(1, len(history))
        con.execute(
            "INSERT OR REPLACE INTO options_history_coverage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                secid,
                underlying,
                expiration,
                len(history),
                min(dates) if dates else None,
                max(dates) if dates else None,
                1,
                bool(history),
                f"{client.base_url}/{endpoint}",
                result,
                status,
                datetime.now(),
            ],
        )
    _audit(
        con,
        "options_history",
        "MOEX ISS options history by SECID",
        total,
        None,
        None,
        "experimental" if total else "requires_paid_data",
        f"pilot contracts={len(contracts)}; snapshot never treated as history",
        "free_pilot_or_paid_archive",
    )
    return {"contracts_checked": len(contracts), "history_rows": total}


def build_common_sample(con):  # pragma: no cover
    ensure_schema(con)
    dates = [
        r[0]
        for r in con.execute(
            "SELECT trade_date FROM predictive_market_prices WHERE secid='SBER' AND board='TQBR' ORDER BY trade_date"
        ).fetchall()
    ]
    sets = {
        "technical": set(dates),
        "breadth": {r[0] for r in con.execute("SELECT trade_date FROM survivorship_impact_daily").fetchall()},
        "finance": {
            r[0] for r in con.execute("SELECT trade_date FROM historical_financial_sector").fetchall()
        },
        "zcyc": {r[0] for r in con.execute("SELECT observation_date FROM deep_zcyc_archive").fetchall()},
        "futures": {
            r[0]
            for r in con.execute(
                "SELECT trade_date FROM deep_continuous_futures WHERE rule='combined'"
            ).fetchall()
        },
        "intraday": {
            r[0]
            for r in con.execute("SELECT trade_date FROM intraday_features WHERE secid='SBER'").fetchall()
        },
        "ifrs": {
            r[0]
            for r in con.execute(
                "SELECT publication_date FROM fundamental_metric_values WHERE quality_status='validated' AND publication_date IS NOT NULL"
            ).fetchall()
        },
        "options": {
            r[0]
            for r in con.execute(
                "SELECT date_from FROM options_history_coverage WHERE history_accessible AND date_from IS NOT NULL"
            ).fetchall()
        },
    }
    con.execute("DELETE FROM sber_predictive_common_sample WHERE feature_version=?", [VERSION])
    written = 0
    date_index = {d: i for i, d in enumerate(dates)}
    for traded in dates:
        for horizon in HORIZONS:
            flags = {k: traded in v for k, v in sets.items()}
            target = date_index[traded] + horizon < len(dates)
            counts = {k: int(v) for k, v in flags.items()}
            missing = {k: 0.0 if v else 1.0 for k, v in flags.items()}
            pit = {k: "point_in_time" if v else "not_available" for k, v in flags.items()}
            quality = {k: "available" if v else "missing_not_imputed" for k, v in flags.items()}
            cutoff = datetime.combine(traded, time(19), MOSCOW)
            con.execute(
                "INSERT INTO sber_predictive_common_sample VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    traded,
                    cutoff,
                    horizon,
                    target,
                    flags["technical"],
                    flags["breadth"],
                    flags["finance"],
                    flags["zcyc"],
                    flags["futures"],
                    flags["intraday"],
                    flags["ifrs"],
                    flags["options"],
                    json.dumps(counts),
                    json.dumps({k: 0 if v else None for k, v in flags.items()}),
                    json.dumps(missing),
                    json.dumps(pit),
                    json.dumps(quality),
                    VERSION,
                    "forward-close-v1",
                ],
            )
            written += 1
    return {"rows": written, "dates": len(dates), "horizons": len(HORIZONS)}


def calculate_coverage_tiers(con):  # pragma: no cover
    ensure_schema(con)
    con.execute("DELETE FROM sber_coverage_tiers WHERE calculation_version=?", [VERSION])
    result = []
    conditions = {
        1: "technical_available AND historical_breadth_available AND financial_sector_available AND zcyc_available",
        2: "technical_available AND historical_breadth_available AND financial_sector_available AND zcyc_available AND futures_available",
        3: "technical_available AND historical_breadth_available AND financial_sector_available AND zcyc_available AND futures_available AND intraday_available",
        4: "technical_available AND historical_breadth_available AND financial_sector_available AND zcyc_available AND futures_available AND intraday_available AND ifrs_available",
        5: "technical_available AND historical_breadth_available AND financial_sector_available AND zcyc_available AND futures_available AND intraday_available AND ifrs_available AND options_available",
    }
    blocks = {
        1: ["technical", "historical_breadth", "financial_sector", "zcyc"],
        2: ["technical", "historical_breadth", "financial_sector", "zcyc", "futures"],
        3: ["technical", "historical_breadth", "financial_sector", "zcyc", "futures", "intraday"],
        4: ["technical", "historical_breadth", "financial_sector", "zcyc", "futures", "intraday", "ifrs"],
        5: [
            "technical",
            "historical_breadth",
            "financial_sector",
            "zcyc",
            "futures",
            "intraday",
            "ifrs",
            "options",
        ],
    }
    for horizon in HORIZONS:
        for tier, condition in conditions.items():
            dates = [
                r[0]
                for r in con.execute(
                    f"SELECT trade_date FROM sber_predictive_common_sample WHERE horizon=? AND target_available AND {condition} ORDER BY trade_date",
                    [horizon],
                ).fetchall()
            ]
            rows_count = len(dates)
            effective = float(rows_count)
            folds = max(0, min(10, rows_count // 100))
            suitability = coverage_suitability(rows_count, effective, folds)
            con.execute(
                "INSERT INTO sber_coverage_tiers VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    horizon,
                    tier,
                    min(dates) if dates else None,
                    max(dates) if dates else None,
                    rows_count,
                    effective,
                    folds,
                    json.dumps(blocks[tier]),
                    suitability,
                    VERSION,
                ],
            )
            result.append((horizon, tier, rows_count, suitability))
    return {"tiers": result}


def rerun_deep_ablation(con):  # pragma: no cover
    ensure_schema(con)
    run_hash = hashlib.sha256(
        (
            VERSION
            + str(
                con.execute("SELECT count(*),max(trade_date) FROM sber_predictive_common_sample").fetchone()
            )
        ).encode()
    ).hexdigest()[:16]
    written = 0
    for horizon in HORIZONS:
        tiers = con.execute(
            "SELECT tier,rows_count,effective_sample_size,folds,suitability FROM sber_coverage_tiers WHERE horizon=? AND calculation_version=? ORDER BY tier",
            [horizon, VERSION],
        ).fetchall()
        for tier, count, effective, folds, suitability in tiers:
            con.execute(
                "INSERT OR REPLACE INTO deep_ablation_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    horizon,
                    f"technical_vs_tier_{tier}",
                    tier,
                    count,
                    effective,
                    folds,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    suitability,
                    run_hash,
                    json.dumps(
                        {
                            "method": "common_sample_expanding_walk_forward_purged_embargo",
                            "metrics_not_computed_without_sufficient_common_sample": True,
                        }
                    ),
                ],
            )
            written += 1
    return {"rows": written, "run_hash": run_hash}


def model_readiness(con):  # pragma: no cover
    ensure_schema(con)
    con.execute("DELETE FROM sber_model_readiness")
    result = []
    for horizon in HORIZONS:
        best = con.execute(
            "SELECT tier,rows_count,effective_sample_size,folds,suitability,available_blocks_json FROM sber_coverage_tiers WHERE horizon=? ORDER BY CASE suitability WHEN 'ready_for_direction_model' THEN 1 WHEN 'ready_for_experimental_model' THEN 2 ELSE 3 END,tier ASC LIMIT 1",
            [horizon],
        ).fetchone()
        if best:
            tier, count, effective, folds, status, blocks = best
        else:
            tier, count, effective, folds, status, blocks = 0, 0, 0, 0, "insufficient_common_sample", "[]"
        reasons = (
            []
            if status != "insufficient_common_sample"
            else ["common sample below minimum", "unavailable blocks are not back-imputed"]
        )
        con.execute(
            "INSERT INTO sber_model_readiness VALUES (?,?,?,?,?,?,?,?,?)",
            [horizon, status, count, effective, folds, tier, blocks, json.dumps(reasons), datetime.now()],
        )
        result.append({"horizon": horizon, "status": status, "rows": count, "tier": tier})
    return result


def complete_deep_backfill(con):  # pragma: no cover
    result = {}
    result["zcyc"] = backfill_zcyc_history(con)
    result["futures_discovery"] = discover_expired_sber_futures(con)
    result["futures_history"] = backfill_sber_futures(con)
    result["continuous"] = rebuild_continuous_futures(con)
    result["universe"] = backfill_historical_liquid_universe(con)
    result["survivorship"] = calculate_survivorship_impact(con)
    result["financial_sector"] = build_historical_financial_sector(con)
    result["intraday"] = record_intraday_coverage(con)
    result["ifrs"] = validate_sber_ifrs_review(con)
    result["options"] = backfill_options_history(con)
    result["common_sample"] = build_common_sample(con)
    result["tiers"] = calculate_coverage_tiers(con)
    result["ablation"] = rerun_deep_ablation(con)
    result["readiness"] = model_readiness(con)
    return result
