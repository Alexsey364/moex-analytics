"""Adapters for official Bank of Russia public endpoints."""

from __future__ import annotations

import re
import time as clock
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import requests

from ..models import Observation, SeriesDefinition

BASE = "https://www.cbr.ru"
MOSCOW = ZoneInfo("Europe/Moscow")
CURRENCY_NAMES = {
    "R01235": ("cbr_usd_rub", "USD/RUB official rate"),
    "R01239": ("cbr_eur_rub", "EUR/RUB official rate"),
    "R01375": ("cbr_cny_rub", "CNY/RUB official rate"),
}


def _get(client: requests.Session, url: str, params: dict) -> requests.Response:
    last_error = None
    for attempt in range(4):
        try:
            response = client.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                clock.sleep(2**attempt)
    raise RuntimeError(f"CBR request failed: {url}: {last_error}") from last_error


def definitions() -> list[SeriesDefinition]:
    items = [
        SeriesDefinition(
            series_id,
            name,
            "RUB",
            "daily",
            "Bank of Russia",
            f"{BASE}/scripts/XML_dynamic.asp",
            None,
            "Usable from the official effective date at 00:00 Moscow",
            "CBR endpoint exposes current historical series, no vintages",
            True,
            f"VAL_NM_RQ={code}; official and market rates remain separate",
        )
        for code, (series_id, name) in CURRENCY_NAMES.items()
    ]
    items.extend(
        [
            SeriesDefinition(
                "cbr_key_rate",
                "Bank of Russia key rate",
                "% p.a.",
                "daily",
                "Bank of Russia",
                f"{BASE}/hd_base/KeyRate/",
                date(2013, 9, 17),
                "Decision press release is normally published at 13:30 Moscow",
                "Decision archive is authoritative; no expectation vintage",
                True,
            ),
            SeriesDefinition(
                "cbr_ruonia",
                "RUONIA",
                "% p.a.",
                "business daily",
                "Bank of Russia",
                f"{BASE}/hd_base/ruonia/dynamics/",
                None,
                "Use the explicit publication date supplied by CBR",
                "Published values may carry status; retained as loaded vintage",
                True,
            ),
        ]
    )
    return items


def parse_currency_xml(xml: str, code: str) -> list[Observation]:
    series_id = CURRENCY_NAMES[code][0]
    rows = []
    for node in ET.fromstring(xml).findall("Record"):
        effective = datetime.strptime(node.attrib["Date"], "%d.%m.%Y").date()
        nominal = float(node.findtext("Nominal", "1").replace(",", "."))
        value = float(node.findtext("Value", "").replace(",", ".")) / nominal
        rows.append(
            Observation(
                series_id,
                effective,
                effective,
                datetime.combine(effective, time.min, MOSCOW),
                value,
                "current-history",
                f"{BASE}/scripts/XML_dynamic.asp",
            )
        )
    return rows


def download_currency(
    code: str, date_from: date, date_to: date, session: requests.Session | None = None
) -> list[Observation]:
    client = session or requests.Session()
    response = _get(
        client,
        f"{BASE}/scripts/XML_dynamic.asp",
        params={
            "date_req1": date_from.strftime("%d/%m/%Y"),
            "date_req2": date_to.strftime("%d/%m/%Y"),
            "VAL_NM_RQ": code,
        },
    )
    return parse_currency_xml(response.text, code)


def parse_ruonia_rows(rows: list[dict]) -> list[Observation]:
    result = []
    for row in rows:
        observed = datetime.strptime(row["date"], "%d.%m.%Y").date()
        released = datetime.strptime(row["release_date"], "%d.%m.%Y").date()
        result.append(
            Observation(
                "cbr_ruonia",
                observed,
                released,
                datetime.combine(released, time(15, 0), MOSCOW),
                float(str(row["value"]).replace(",", ".")),
                "current-history",
                f"{BASE}/hd_base/ruonia/dynamics/",
            )
        )
    return result


def key_rate_decision(decision_date: date, value: float) -> Observation:
    return Observation(
        "cbr_key_rate",
        decision_date,
        decision_date,
        datetime.combine(decision_date, time(13, 30), MOSCOW),
        value,
        "decision-release",
        f"{BASE}/dkp/mp_dec/",
    )


def _table_rows(html: str) -> list[list[str]]:
    table = re.search(r'<table class="data">(.*?)</table>', html, re.DOTALL | re.IGNORECASE)
    if not table:
        return []
    result = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1), re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        if cells:
            result.append([unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in cells])
    return result


def parse_key_rate_html(html: str) -> list[Observation]:
    daily = sorted(
        (datetime.strptime(row[0], "%d.%m.%Y").date(), float(row[1].replace(",", ".")))
        for row in _table_rows(html)
    )
    result = []
    previous = None
    for observed, value in daily:
        if value != previous:
            result.append(key_rate_decision(observed, value))
            previous = value
    return result


def parse_ruonia_html(html: str) -> list[Observation]:
    records = [
        {"date": row[0], "value": row[1], "release_date": row[10]}
        for row in _table_rows(html)
        if len(row) >= 11 and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", row[10])
    ]
    result = parse_ruonia_rows(records)
    return [
        Observation(
            row.series_id,
            row.observation_date,
            row.release_date,
            datetime.combine(row.release_date + timedelta(days=1), time.min, MOSCOW),
            row.value,
            row.vintage,
            row.source,
        )
        for row in result
    ]


def download_rates(
    date_from: date, date_to: date, session: requests.Session | None = None
) -> list[Observation]:
    client = session or requests.Session()
    params = {
        "UniDbQuery.Posted": "True",
        "UniDbQuery.From": date_from.strftime("%d.%m.%Y"),
        "UniDbQuery.To": date_to.strftime("%d.%m.%Y"),
    }
    key_params = dict(params)
    key_params["UniDbQuery.From"] = (date_from - timedelta(days=14)).strftime("%d.%m.%Y")
    key = _get(client, f"{BASE}/hd_base/KeyRate/", key_params)
    ruonia = _get(client, f"{BASE}/hd_base/ruonia/dynamics/", params)
    decisions = [row for row in parse_key_rate_html(key.text) if row.observation_date >= date_from]
    return [*decisions, *parse_ruonia_html(ruonia.text)]
